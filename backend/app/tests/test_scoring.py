"""Deterministic scoring algorithm."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.agent.scoring_service import (ScoringCriteria, ScoringData, ScoringError, bayesian_rating, normalize_weights,
                                       price_value_score, rating_score, score_products)


def laptop(pid, price, rating=4.2, reviews=500, stock=5, **specs):
    base = dict(cpu_tier=3, ram_gb=16, gpu_tier=1, storage_gb=512, storage_type="SSD", battery_hours=8)
    base.update(specs)
    return ScoringData(pid, f"Laptop {pid}", "laptop", "Brand", Decimal(str(price)), rating, reviews, stock, base)


def test_default_weights_sum_to_one_and_custom_weights_normalised():
    w = normalize_weights(None, "laptop")
    assert w == pytest.approx({"performance": 0.40, "price_value": 0.25, "rating": 0.20, "battery": 0.15})
    w = normalize_weights({"performance": 2, "price_value": 1, "rating": 1, "battery": 0}, "laptop")
    assert sum(w.values()) == pytest.approx(1.0) and w["performance"] == pytest.approx(0.5)


@pytest.mark.parametrize("bad", [{"performance": -1}, {"speed": 1}, {"performance": 0, "rating": 0}])
def test_invalid_weights_rejected(bad):
    with pytest.raises(ScoringError):
        normalize_weights(bad, "laptop")


def test_budget_is_a_hard_constraint():
    r = score_products([laptop("A", 79000), laptop("B", 81000)], ScoringCriteria("laptop", budget=80000))
    assert [x["product_id"] for x in r["ranked"]] == ["A"]
    assert r["excluded"][0]["product_id"] == "B" and "exceeds budget" in r["excluded"][0]["reasons"][0]


def test_min_ram_and_stock_constraints():
    items = [laptop("A", 50000, ram_gb=8), laptop("B", 50000, stock=0), laptop("C", 50000)]
    r = score_products(items, ScoringCriteria("laptop", min_ram_gb=16, in_stock_only=True))
    assert [x["product_id"] for x in r["ranked"]] == ["C"]
    assert r["excluded_count"] == 2


def test_performance_rewards_better_hardware():
    weak = laptop("W", 60000, cpu_tier=1, ram_gb=8, gpu_tier=0, storage_gb=256)
    strong = laptop("S", 60000, cpu_tier=5, ram_gb=32, gpu_tier=4, storage_gb=1024)
    r = score_products([weak, strong], ScoringCriteria("laptop", budget=80000))
    assert r["ranked"][0]["product_id"] == "S"
    assert r["ranked"][0]["components"]["performance"] > r["ranked"][1]["components"]["performance"]


def test_ml_gpu_use_case_prefers_cuda_and_warns_without_gpu():
    igpu = laptop("I", 60000, gpu_tier=1, gpu_cuda=False)
    cuda = laptop("C", 70000, gpu_tier=3, gpu_cuda=True, gpu_vram_gb=6)
    r = score_products([igpu, cuda], ScoringCriteria("laptop", budget=80000, use_case="ml_gpu"))
    assert r["ranked"][0]["product_id"] == "C"
    warn_i = next(x for x in r["ranked"] if x["product_id"] == "I")["warnings"]
    assert any("not suitable for GPU-accelerated training" in w for w in warn_i)


def test_rating_normalisation_and_bayesian_confidence():
    assert rating_score(5.0, 1_000_000) == pytest.approx(100, abs=0.1)
    assert rating_score(1.0, 1_000_000) == pytest.approx(0, abs=0.1)
    # 4.8 from 22 reviews is less trustworthy than 4.5 from 2000 reviews.
    assert bayesian_rating(4.8, 22) < bayesian_rating(4.5, 2000)
    assert bayesian_rating(4.0, 0) == pytest.approx(3.8)


def test_price_value_formula_and_boundaries():
    assert price_value_score(Decimal("60000"), 80000, Decimal(0), Decimal(0))[0] == pytest.approx(25.0)
    assert price_value_score(Decimal("80000"), 80000, Decimal(0), Decimal(0))[0] == 0.0
    assert price_value_score(Decimal("90000"), 80000, Decimal(0), Decimal(0))[0] == 0.0  # clamped
    score, note = price_value_score(Decimal("50"), None, Decimal("50"), Decimal("150"))
    assert score == 100.0 and "No budget" in note
    with pytest.raises(ScoringError):
        score_products([laptop("A", 100)], ScoringCriteria("laptop", budget=0))


def test_missing_specifications_produce_warnings():
    d = ScoringData("X", "No specs", "laptop", "B", Decimal("50000"), 4.0, 100, 3, {})
    r = score_products([d], ScoringCriteria("laptop", budget=80000))
    warnings = r["ranked"][0]["warnings"]
    assert any("missing" in w for w in warnings) and any("Battery life unknown" in w for w in warnings)


def test_ties_are_broken_deterministically():
    a, b = laptop("B-2", 50000), laptop("A-1", 50000)
    r1 = score_products([a, b], ScoringCriteria("laptop", budget=80000))
    r2 = score_products([b, a], ScoringCriteria("laptop", budget=80000))
    assert r1["ranked"][0]["total_score"] == r1["ranked"][1]["total_score"]
    assert [x["product_id"] for x in r1["ranked"]] == [x["product_id"] for x in r2["ranked"]] == ["A-1", "B-2"]


def test_no_eligible_products():
    r = score_products([laptop("A", 90000)], ScoringCriteria("laptop", budget=10000, min_ram_gb=64))
    assert r["no_match"] is True and r["ranked"] == [] and r["excluded_count"] == 1


def test_rankings_are_reproducible():
    items = [laptop(f"P{i}", 40000 + i * 3000, rating=3.8 + i * 0.1, cpu_tier=1 + i % 5) for i in range(8)]
    c = ScoringCriteria("laptop", budget=80000, top_n=5)
    assert score_products(items, c)["ranked"] == score_products(list(reversed(items)), c)["ranked"]


def test_battery_component_is_capped():
    r = score_products([laptop("A", 50000, battery_hours=30)], ScoringCriteria("laptop", budget=80000))
    assert r["ranked"][0]["components"]["battery"] == 100.0
