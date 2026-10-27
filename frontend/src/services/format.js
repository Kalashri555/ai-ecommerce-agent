// Display helpers shared by product components.

const SPEC_LABELS = {
  cpu: (v) => v,
  chipset: (v) => v,
  gpu: (v) => v,
  ram_gb: (v) => `${v} GB RAM`,
  storage_gb: (v) => (v >= 1024 ? `${v / 1024} TB storage` : `${v} GB storage`),
  battery_hours: (v) => `${v} h battery`,
  battery_months: (v) => `${v} months battery`,
  battery_mah: (v) => `${v} mAh`,
  display: (v) => v,
  camera_mp: (v) => `${v} MP camera`,
  type: (v) => v,
  anc: (v) => (v ? "Noise cancelling" : "No ANC"),
  connectivity: (v) => v,
  dpi: (v) => `${v} DPI`,
  silent: (v) => (v ? "Silent clicks" : null),
  layout: (v) => `${v} layout`,
  switch_type: (v) => `${v} switches`,
  wireless: (v) => (v ? "Wireless" : "Wired"),
  backlit: (v) => (v ? "Backlit" : null),
  size_in: (v) => `${v}"`,
  resolution: (v) => v,
  refresh_hz: (v) => `${v} Hz`,
  panel: (v) => `${v} panel`,
};

export function specChips(keySpecs = {}, max = 4) {
  return Object.entries(keySpecs)
    .map(([k, v]) => (SPEC_LABELS[k] ? SPEC_LABELS[k](v) : null))
    .filter(Boolean)
    .slice(0, max);
}

export const CATEGORY_NAMES = {
  laptop: "Laptops",
  mobile_phone: "Mobile phones",
  headphones: "Headphones",
  wireless_mouse: "Wireless mice",
  keyboard: "Keyboards",
  monitor: "Monitors",
  accessories: "Accessories",
};

export const COMPONENT_NAMES = {
  performance: "Performance",
  price_value: "Price value",
  rating: "Rating",
  battery: "Battery",
};

// Normalise ranking items (rank_products) and comparison items (compare_products)
// into one row shape for the comparison table.
export function toComparisonRows(data, kind) {
  const items = kind === "ranking" ? data.ranked || [] : data.products || [];
  return items.map((it) => {
    const score = kind === "ranking" ? it : it.score || null;
    const specs = it.specifications || it.key_specs || {};
    const components = score?.components || {};
    const weights = data.weights || {};
    const strengths = Object.entries(components)
      .filter(([k, v]) => v !== null && v >= 70 && (weights[k] ?? 1) > 0)
      .map(([k, v]) => `${COMPONENT_NAMES[k]} ${Math.round(v)}`);
    const limitations = [
      ...Object.entries(components)
        .filter(([k, v]) => v !== null && v < 35 && (weights[k] ?? 1) > 0)
        .map(([k, v]) => `Low ${COMPONENT_NAMES[k].toLowerCase()} (${Math.round(v)})`),
      ...(score?.warnings || []).filter((w) => !w.startsWith("No budget")),
    ];
    const stock = it.stock_quantity;
    return {
      product_id: it.product_id,
      name: it.name,
      price: it.price,
      rating: it.average_rating,
      reviews: it.review_count,
      adjusted: it.adjusted_rating,
      ram: specs.ram_gb,
      cpu: specs.cpu || specs.chipset,
      gpu: specs.gpu,
      battery: specs.battery_months
        ? `${specs.battery_months} mo`
        : (specs.battery_hours ?? it.battery_hours)
          ? `${specs.battery_hours ?? it.battery_hours} h`
          : null,
      stock,
      rank: score?.rank,
      total: score?.total_score,
      components,
      explanation: score?.explanation,
      strengths,
      limitations,
    };
  });
}
