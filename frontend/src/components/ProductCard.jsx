import { inr } from "../services/api.js";
import { specChips } from "../services/format.js";

// Neutral local placeholder art per category (no third-party images).
const ICONS = {
  laptop: <path d="M10 14h28v18H10zM6 36h36l-3 4H9z" />,
  mobile_phone: <path d="M17 6h14a2 2 0 0 1 2 2v32a2 2 0 0 1-2 2H17a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2zm5 31h4" />,
  headphones: <path d="M10 30v-6a14 14 0 0 1 28 0v6M10 28h5v10h-5zM33 28h5v10h-5z" />,
  wireless_mouse: <path d="M24 8a10 10 0 0 1 10 10v12a10 10 0 0 1-20 0V18A10 10 0 0 1 24 8zm0 0v10" />,
  keyboard: <path d="M6 16h36v18H6zM11 21h2m5 0h2m5 0h2m5 0h2M11 26h2m5 0h12m5 0h2" />,
  monitor: <path d="M7 10h34v22H7zM20 38h8m-4-6v6" />,
  accessories: <path d="M14 18h20v20H14zM19 18v-6h10v6" />,
};

export function ProductArt({ category }) {
  return (
    <div className={`product-art art-${category}`} aria-hidden="true">
      <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
        {ICONS[category] || ICONS.accessories}
      </svg>
    </div>
  );
}

export function Stars({ rating, count }) {
  return (
    <span className="stars" title={`${rating} out of 5`}>
      <span className="stars-value">★ {Number(rating).toFixed(1)}</span>
      {count !== undefined && <span className="muted"> ({Number(count).toLocaleString("en-IN")})</span>}
    </span>
  );
}

export function Availability({ stock }) {
  if (stock <= 0) return <span className="badge badge-out">Out of stock</span>;
  if (stock <= 5) return <span className="badge badge-low">Only {stock} left</span>;
  return <span className="badge badge-in">In stock</span>;
}

export default function ProductCard({ product, score, selected, onToggleCompare, onDetails, compareDisabled }) {
  const chips = specChips(product.key_specs);
  return (
    <article className={`product-card ${selected ? "is-selected" : ""}`}>
      <ProductArt category={product.category} />
      <div className="product-body">
        <div className="product-top">
          <span className="product-brand">{product.brand}</span>
          <span className="product-id">{product.product_id}</span>
        </div>
        <h3 className="product-name">{product.name}</h3>
        <div className="product-price">{inr(product.price)}</div>
        <div className="product-meta">
          <Stars rating={product.average_rating} count={product.review_count} />
          <Availability stock={product.stock_quantity} />
        </div>
        {chips.length > 0 && (
          <ul className="spec-chips">
            {chips.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        )}
        {score !== undefined && score !== null && (
          <div className="score-line" title="Deterministic recommendation score (0–100)">
            <span>Score</span>
            <span className="score-bar">
              <span style={{ width: `${Math.min(100, score)}%` }} />
            </span>
            <strong>{score}</strong>
          </div>
        )}
        <div className="product-actions">
          <button type="button" className="btn btn-quiet" onClick={() => onDetails?.(product.product_id)}>
            View details
          </button>
          {onToggleCompare && (
            <label className={`compare-toggle ${compareDisabled && !selected ? "is-disabled" : ""}`}>
              <input
                type="checkbox"
                checked={selected}
                disabled={compareDisabled && !selected}
                onChange={() => onToggleCompare(product.product_id)}
              />
              Compare
            </label>
          )}
        </div>
      </div>
    </article>
  );
}
