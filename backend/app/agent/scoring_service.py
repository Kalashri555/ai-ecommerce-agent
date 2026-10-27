"""Deterministic product scoring and ranking.

The LLM never ranks products "by intuition". It extracts the customer's
criteria (budget, minimum RAM, use case, ...) and passes them to the
`rank_products` tool, which runs the code below. Same input -> same ranking.

Pipeline
--------
1. Hard constraints (budget, min RAM, min rating, brand, required features,
   stock). A product that violates any of them is *excluded*, with a reason.
2. Component scores, each normalised to 0-100:
     performance  - category specific (see `_performance`)
     price_value  - 100 * (budget - price) / budget, clamped to 0..100
     rating       - Bayesian-adjusted rating mapped from 1..5 to 0..100
     battery      - battery life vs a target, capped at 100
3. Weighted total with weights normalised to sum to 1.
4. Deterministic sort: total desc, price asc, adjusted rating desc, id asc.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

# --------------------------------------------------------------------------
# Configuration (documented assumptions)
# --------------------------------------------------------------------------
COMPONENTS = ("performance", "price_value", "rating", "battery")

DEFAULT_WEIGHTS: dict[str, dict[str, float]] = {
    "laptop": {"performance": 40, "price_value": 25, "rating": 20, "battery": 15},
    "mobile_phone": {"performance": 35, "price_value": 25, "rating": 25, "battery": 15},
    "monitor": {"performance": 45, "price_value": 30, "rating": 25, "battery": 0},
    "headphones": {"performance": 0, "price_value": 35, "rating": 40, "battery": 25},
    "wireless_mouse": {"performance": 0, "price_value": 45, "rating": 45, "battery": 10},
    "keyboard": {"performance": 0, "price_value": 45, "rating": 55, "battery": 0},
    "accessories": {"performance": 0, "price_value": 45, "rating": 55, "battery": 0},
}

# Battery target (hours) that earns a full battery score.
BATTERY_TARGET_HOURS = {"laptop": 10, "mobile_phone": 24, "headphones": 30, "wireless_mouse": 12 * 30 * 24}

# Bayesian rating: pull ratings with few reviews toward a prior mean.
#   adjusted = (v / (v + m)) * R + (m / (v + m)) * C
RATING_PRIOR_MEAN = 3.8   # C: typical rating in the catalog
RATING_PRIOR_WEIGHT = 50  # m: "virtual reviews" behind the prior

# Laptop performance sub-weights by use case (cpu, ram, gpu, storage).
LAPTOP_USE_CASE_WEIGHTS = {
    "general": (0.40, 0.30, 0.10, 0.20),
    "basic_programming": (0.40, 0.35, 0.05, 0.20),
    "office": (0.35, 0.30, 0.05, 0.30),
    "ml_cpu": (0.40, 0.40, 0.10, 0.10),
    "ml_gpu": (0.25, 0.25, 0.40, 0.10),
    "gaming": (0.30, 0.20, 0.40, 0.10),
}

LAPTOP_RAM_POINTS = [(4, 10), (8, 40), (12, 60), (16, 75), (24, 90), (32, 100)]
PHONE_RAM_POINTS = [(4, 30), (6, 50), (8, 75), (12, 100)]
PHONE_STORAGE_POINTS = [(64, 30), (128, 60), (256, 90), (512, 100)]
SSD_POINTS = [(128, 20), (256, 40), (512, 70), (1024, 100)]
MONITOR_RES_POINTS = [(1080, 50), (1440, 75), (2160, 100)]
MONITOR_HZ_POINTS = [(60, 40), (75, 50), (100, 65), (144, 85), (165, 90), (180, 95), (240, 100)]


@dataclass
class ScoringData:
    """Plain data needed for scoring (decoupled from the ORM for testing)."""

    product_id: str
    name: str
    category: str
    brand: str
    price: Decimal
    average_rating: float
    review_count: int
    stock_quantity: int
    specs: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScoringCriteria:
    category: str
    budget: float | None = None
    min_ram_gb: int | None = None
    min_rating: float | None = None
    brand: str | None = None
    required_features: list[str] = field(default_factory=list)
    in_stock_only: bool = True
    use_case: str = "general"
    weights: dict[str, float] | None = None
    top_n: int = 5
    battery_target_hours: float | None = None


class ScoringError(ValueError):
    pass


def product_to_scoring_data(p) -> ScoringData:  # p: models.Product
    return ScoringData(product_id=p.sku, name=p.name, category=p.category, brand=p.brand, price=p.price,
                       average_rating=float(p.average_rating), review_count=int(p.review_count),
                       stock_quantity=int(p.stock_quantity), specs=p.specifications)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def interpolate(value: float, points: list[tuple[float, float]]) -> float:
    """Piecewise-linear lookup; clamps outside the table."""
    if value <= points[0][0]:
        return float(points[0][1])
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if value <= x1:
            return float(y0 + (y1 - y0) * (value - x0) / (x1 - x0))
    return float(points[-1][1])


def normalize_weights(weights: dict[str, float] | None, category: str) -> dict[str, float]:
    """Validate and scale weights so they sum to exactly 1.0."""
    raw = dict(weights) if weights else dict(DEFAULT_WEIGHTS.get(category, DEFAULT_WEIGHTS["accessories"]))
    unknown = set(raw) - set(COMPONENTS)
    if unknown:
        raise ScoringError(f"Unknown weight(s): {', '.join(sorted(unknown))}")
    for k, v in raw.items():
        if v is None or v < 0:
            raise ScoringError(f"Weight '{k}' must be >= 0")
    total = sum(raw.get(k, 0) for k in COMPONENTS)
    if total <= 0:
        raise ScoringError("At least one weight must be greater than zero")
    return {k: raw.get(k, 0) / total for k in COMPONENTS}


def bayesian_rating(rating: float, reviews: int) -> float:
    v = max(0, reviews)
    m = RATING_PRIOR_WEIGHT
    return (v / (v + m)) * rating + (m / (v + m)) * RATING_PRIOR_MEAN


def rating_score(rating: float, reviews: int) -> float:
    return clamp((bayesian_rating(rating, reviews) - 1) / 4 * 100)


def price_value_score(price: Decimal, budget: float | None, ref_min: Decimal, ref_max: Decimal) -> tuple[float, str | None]:
    """100 * (budget - price) / budget. Without a valid budget, fall back to the
    candidates' own price range (cheapest = 100, most expensive = 0)."""
    if budget is not None and budget > 0:
        b = Decimal(str(budget))
        return clamp(float(Decimal(100) * (b - price) / b)), None
    if ref_max == ref_min:
        return 50.0, "No budget given: price value is neutral (all candidates cost the same)."
    return clamp(float(Decimal(100) * (ref_max - price) / (ref_max - ref_min))), \
        "No budget given: price value is relative to the cheapest/most expensive candidate."


def _performance(d: ScoringData, use_case: str) -> tuple[float | None, list[str]]:
    s, warnings = d.specs, []
    if d.category == "laptop":
        needed = ("cpu_tier", "ram_gb", "gpu_tier", "storage_gb")
        if any(k not in s for k in needed):
            warnings.append("Some performance specifications are missing; performance is estimated.")
        cpu = clamp(float(s.get("cpu_tier", 2)) / 5 * 100)
        ram = interpolate(float(s.get("ram_gb", 8)), LAPTOP_RAM_POINTS)
        gpu = clamp(float(s.get("gpu_tier", 0)) / 4 * 100)
        storage = interpolate(float(s.get("storage_gb", 256)), SSD_POINTS)
        stype = str(s.get("storage_type", "SSD")).upper()
        if stype == "HDD":
            storage *= 0.5
        elif stype == "EMMC":
            storage *= 0.3
        wc, wr, wg, ws = LAPTOP_USE_CASE_WEIGHTS.get(use_case, LAPTOP_USE_CASE_WEIGHTS["general"])
        return wc * cpu + wr * ram + wg * gpu + ws * storage, warnings
    if d.category == "mobile_phone":
        if "chipset_tier" not in s:
            return None, ["Chipset information missing; performance not scored."]
        chip = clamp(float(s["chipset_tier"]) / 5 * 100)
        ram = interpolate(float(s.get("ram_gb", 4)), PHONE_RAM_POINTS)
        storage = interpolate(float(s.get("storage_gb", 64)), PHONE_STORAGE_POINTS)
        return 0.5 * chip + 0.25 * ram + 0.25 * storage, warnings
    if d.category == "monitor":
        if "vertical_px" not in s or "refresh_hz" not in s:
            return None, ["Resolution/refresh-rate missing; performance not scored."]
        res = interpolate(float(s["vertical_px"]), MONITOR_RES_POINTS)
        hz = interpolate(float(s["refresh_hz"]), MONITOR_HZ_POINTS)
        w_res, w_hz = (0.35, 0.65) if use_case == "gaming" else (0.55, 0.45)
        return w_res * res + w_hz * hz, warnings
    return None, []


def _battery_hours(d: ScoringData) -> float | None:
    if "battery_hours" in d.specs:
        return float(d.specs["battery_hours"])
    if "battery_months" in d.specs:
        return float(d.specs["battery_months"]) * 30 * 24
    return None


def _suitability_notes(d: ScoringData, use_case: str) -> list[str]:
    """Honest caveats, e.g. 16 GB RAM does NOT make a laptop an ML workstation."""
    if d.category != "laptop":
        return []
    s, notes = d.specs, []
    ram = s.get("ram_gb", 0)
    if use_case in ("ml_cpu", "ml_gpu"):
        if ram < 16:
            notes.append(f"Only {ram} GB RAM - limits dataset sizes and running several tools at once.")
        if use_case == "ml_gpu":
            if not s.get("gpu_cuda"):
                notes.append("No NVIDIA (CUDA) GPU - not suitable for GPU-accelerated training; plan to use cloud GPUs.")
            elif s.get("gpu_vram_gb", 0) < 8:
                notes.append(f"{s.get('gpu_vram_gb')} GB VRAM - fine for small models and experiments, limiting for larger ones.")
        else:
            notes.append("Good for classical ML / small models on CPU; large deep-learning training needs a strong GPU or the cloud.")
    if use_case == "basic_programming" and ram < 16:
        notes.append(f"{ram} GB RAM is workable for coding but tight with IDEs, browsers and Docker together.")
    return notes


def check_hard_constraints(d: ScoringData, c: ScoringCriteria) -> list[str]:
    reasons = []
    if d.category != c.category:
        reasons.append(f"category is {d.category}, not {c.category}")
    if c.budget is not None and d.price > Decimal(str(c.budget)):
        reasons.append(f"price ₹{d.price:,.0f} exceeds budget ₹{Decimal(str(c.budget)):,.0f}")
    if c.min_ram_gb is not None and (d.specs.get("ram_gb") or 0) < c.min_ram_gb:
        reasons.append(f"RAM {d.specs.get('ram_gb', 'unknown')} GB is below {c.min_ram_gb} GB")
    if c.min_rating is not None and d.average_rating < c.min_rating:
        reasons.append(f"rating {d.average_rating} is below {c.min_rating}")
    if c.brand and d.brand.lower() != c.brand.lower():
        reasons.append(f"brand is {d.brand}, not {c.brand}")
    for feat in c.required_features:
        if not d.specs.get(feat):
            reasons.append(f"missing required feature '{feat}'")
    if c.in_stock_only and d.stock_quantity <= 0:
        reasons.append("out of stock")
    return reasons


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------
def score_products(candidates: list[ScoringData], c: ScoringCriteria) -> dict[str, Any]:
    if c.budget is not None and c.budget <= 0:
        raise ScoringError("budget must be greater than zero")
    weights = normalize_weights(c.weights, c.category)

    eligible: list[ScoringData] = []
    excluded: list[dict[str, Any]] = []
    for d in candidates:
        reasons = check_hard_constraints(d, c)
        if reasons:
            excluded.append({"product_id": d.product_id, "name": d.name, "reasons": reasons})
        else:
            eligible.append(d)

    hard = {k: v for k, v in {
        "category": c.category, "max_price": c.budget, "min_ram_gb": c.min_ram_gb, "min_rating": c.min_rating,
        "brand": c.brand, "required_features": c.required_features or None, "in_stock_only": c.in_stock_only,
    }.items() if v not in (None, False)}

    base = {"weights": {k: round(v, 4) for k, v in weights.items()}, "hard_constraints": hard,
            "use_case": c.use_case, "excluded": excluded[:20], "excluded_count": len(excluded),
            "assumptions": _assumptions(c)}

    if not eligible:
        return {**base, "ok": True, "no_match": True, "ranked": [], "eligible_count": 0,
                "message": "No products satisfy every hard constraint. Ask the user whether to relax a constraint."}

    prices = [d.price for d in eligible]
    ref_min, ref_max = min(prices), max(prices)
    target = c.battery_target_hours or BATTERY_TARGET_HOURS.get(c.category)

    scored = []
    for d in eligible:
        warnings: list[str] = []
        perf, perf_warn = _performance(d, c.use_case)
        warnings += perf_warn
        if perf is None and weights["performance"] > 0:
            perf = 50.0
            warnings.append("Performance data unavailable; neutral 50 used.")
        price_s, price_note = price_value_score(d.price, c.budget, ref_min, ref_max)
        if price_note:
            warnings.append(price_note)
        rate_s = rating_score(d.average_rating, d.review_count)
        if d.review_count < 50:
            warnings.append(f"Only {d.review_count} reviews - rating confidence is low and was adjusted toward the average.")
        hours = _battery_hours(d)
        if weights["battery"] > 0:
            if hours is None or not target:
                batt = 50.0
                warnings.append("Battery life unknown; neutral 50 used.")
            else:
                batt = clamp(hours / target * 100)
        else:
            batt = clamp(hours / target * 100) if (hours is not None and target) else None

        comps = {"performance": perf, "price_value": price_s, "rating": rate_s, "battery": batt}
        total = sum(weights[k] * (comps[k] or 0.0) for k in COMPONENTS)
        scored.append((d, total, comps, warnings + _suitability_notes(d, c.use_case)))

    scored.sort(key=lambda t: (-round(t[1], 6), t[0].price, -bayesian_rating(t[0].average_rating, t[0].review_count), t[0].product_id))

    ranked = []
    for rank, (d, total, comps, warnings) in enumerate(scored[: c.top_n], start=1):
        ranked.append({
            "rank": rank,
            "product_id": d.product_id,
            "name": d.name,
            "brand": d.brand,
            "price": f"{d.price:.2f}",
            "average_rating": d.average_rating,
            "review_count": d.review_count,
            "stock_quantity": d.stock_quantity,
            "total_score": round(total, 2),
            "components": {k: (None if v is None else round(v, 1)) for k, v in comps.items()},
            "adjusted_rating": round(bayesian_rating(d.average_rating, d.review_count), 2),
            "battery_hours": _battery_hours(d),
            "key_specs": {k: d.specs[k] for k in ("cpu", "ram_gb", "gpu", "storage_gb", "battery_hours", "battery_months", "chipset", "resolution", "refresh_hz") if k in d.specs},
            "warnings": warnings,
            "explanation": _explain(rank, d, total, comps, weights, c),
        })
    return {**base, "ok": True, "no_match": False, "eligible_count": len(eligible), "ranked": ranked}


def _explain(rank: int, d: ScoringData, total: float, comps: dict, weights: dict, c: ScoringCriteria) -> str:
    parts = [f"#{rank} with {total:.1f}/100."]
    if weights["performance"] > 0 and comps["performance"] is not None:
        parts.append(f"Performance {comps['performance']:.0f}/100 ({c.use_case.replace('_', ' ')} profile).")
    if c.budget:
        pct = (1 - float(d.price) / c.budget) * 100
        parts.append(f"Price ₹{d.price:,.0f} is {pct:.0f}% under budget.")
    else:
        parts.append(f"Price ₹{d.price:,.0f}.")
    parts.append(f"Rating {d.average_rating}★ from {d.review_count:,} reviews (adjusted {bayesian_rating(d.average_rating, d.review_count):.2f}).")
    hours = _battery_hours(d)
    if weights["battery"] > 0 and hours is not None and d.category in ("laptop", "mobile_phone", "headphones"):
        parts.append(f"Battery about {hours:g} h.")
    return " ".join(parts)


def _assumptions(c: ScoringCriteria) -> list[str]:
    out = [
        "Scores are computed by deterministic code from demo-catalog data, not by the language model.",
        f"Ratings use a Bayesian adjustment (prior mean {RATING_PRIOR_MEAN}, weight {RATING_PRIOR_WEIGHT} reviews).",
        "Price value = 100 x (budget - price) / budget, clamped to 0-100." if c.budget else
        "No budget: price value is relative to the cheapest and most expensive eligible product.",
    ]
    if c.category == "laptop":
        out.append("Laptop performance combines CPU tier, RAM, GPU tier and SSD size, weighted by use case.")
    return out
