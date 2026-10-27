import { useEffect, useState } from "react";
import { api, inr } from "../services/api.js";
import { Availability, ProductArt, Stars } from "./ProductCard.jsx";

export default function ProductDetailsModal({ productId, onClose, onAdd }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [adding, setAdding] = useState(false);
  const [added, setAdded] = useState(null);

  useEffect(() => {
    if (!productId) return;
    setData(null);
    setError(null);
    setAdded(null);
    api.product(productId).then((d) => setData(d.product)).catch((e) => setError(e.message));
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [productId, onClose]);

  if (!productId) return null;

  const add = async () => {
    setAdding(true);
    setError(null);
    try {
      await onAdd(productId);
      setAdded("Added to your cart.");
    } catch (e) {
      setError(e.message);
    } finally {
      setAdding(false);
    }
  };

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal details" role="dialog" aria-modal="true" aria-labelledby="details-title">
        <button type="button" className="modal-close" aria-label="Close" onClick={onClose}>×</button>
        {!data && !error && <p className="muted">Loading…</p>}
        {error && <p className="error-banner" role="alert">{error}</p>}
        {data && (
          <>
            <div className="details-head">
              <ProductArt category={data.category} />
              <div>
                <div className="muted small">{data.brand} · {data.product_id}</div>
                <h2 id="details-title">{data.name}</h2>
                <div className="product-price">{inr(data.price)}</div>
                <div className="product-meta">
                  <Stars rating={data.average_rating} count={data.review_count} />
                  <Availability stock={data.stock_quantity} />
                </div>
              </div>
            </div>
            <p className="details-desc">{data.description}</p>
            <table className="spec-table">
              <tbody>
                {Object.entries(data.specifications).map(([k, v]) => (
                  <tr key={k}>
                    <th scope="row">{k.replaceAll("_", " ")}</th>
                    <td>{typeof v === "boolean" ? (v ? "Yes" : "No") : String(v)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {data.sample_reviews?.length > 0 && (
              <div className="reviews">
                <h3>Sample reviews</h3>
                <ul>
                  {data.sample_reviews.map((r, i) => (
                    <li key={i}>
                      <span className="stars-value">{"★".repeat(r.rating)}</span> {r.review_text}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <div className="confirm-actions">
              {added && <span className="text-good small">{added}</span>}
              <button type="button" className="btn btn-primary" disabled={adding || data.stock_quantity <= 0} onClick={add}>
                {data.stock_quantity <= 0 ? "Out of stock" : adding ? "Adding…" : "Add to cart"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
