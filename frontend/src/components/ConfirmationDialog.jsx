import { useEffect, useRef, useState } from "react";
import { inr } from "../services/api.js";

// Shows a pending approval. Approve/Cancel call the backend, which re-validates
// price and stock before executing. Closing the dialog keeps the request pending.
export default function ConfirmationDialog({ approval, onApprove, onCancel, onClose }) {
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const approveRef = useRef(null);

  useEffect(() => {
    setError(null);
    setBusy(null);
    approveRef.current?.focus();
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [approval?.approval_id, onClose]);

  if (!approval) return null;
  const s = approval.summary || {};
  const isOrder = approval.action_type === "place_order";
  const expired = new Date(approval.expires_at) < new Date();

  const run = async (kind, fn) => {
    setBusy(kind);
    setError(null);
    try {
      await fn(approval);
    } catch (err) {
      setError(err.message || "Something went wrong.");
      setBusy(null);
    }
  };

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}>
      <div className="modal confirm" role="dialog" aria-modal="true" aria-labelledby="confirm-title">
        <div className="confirm-band">{isOrder ? "Confirm your order" : "Confirm cart change"}</div>
        <h2 id="confirm-title">{s.title}</h2>

        {isOrder ? (
          <>
            <table className="order-table">
              <tbody>
                {(s.lines || []).map((l) => (
                  <tr key={l.product_id}>
                    <th scope="row">
                      {l.name}
                      <div className="muted small">{l.product_id}</div>
                    </th>
                    <td className="num">× {l.quantity}</td>
                    <td className="num">{inr(l.unit_price)}</td>
                    <td className="num">{inr(l.line_total)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <dl className="confirm-facts">
              <div><dt>Subtotal</dt><dd>{inr(s.subtotal)}</dd></div>
              <div><dt>Shipping</dt><dd>Not calculated</dd></div>
              <div><dt>Taxes</dt><dd>Not calculated</dd></div>
              <div className="total"><dt>Estimated total</dt><dd>{inr(s.estimated_total)}</dd></div>
            </dl>
            <p className="muted small">This is a simulated order. No payment is taken and no retailer receives it.</p>
          </>
        ) : (
          <dl className="confirm-facts">
            <div><dt>Action</dt><dd>{s.action_label}</dd></div>
            <div><dt>Product</dt><dd>{s.product_name}</dd></div>
            <div><dt>Product ID</dt><dd>{s.product_id}</dd></div>
            <div>
              <dt>Quantity</dt>
              <dd>
                {s.previous_quantity !== undefined ? `${s.previous_quantity} → ${s.quantity}` : s.quantity}
                {s.quantity_already_in_cart ? ` (already ${s.quantity_already_in_cart} in cart)` : ""}
              </dd>
            </div>
            <div><dt>Unit price</dt><dd>{inr(s.unit_price)}</dd></div>
            <div className="total"><dt>Estimated price</dt><dd>{inr(s.estimated_price)}</dd></div>
            <div><dt>Availability</dt><dd>{s.available_quantity} in stock</dd></div>
          </dl>
        )}

        {expired && <p className="text-bad small">This request has expired. Ask the assistant again.</p>}
        {error && (
          <p className="error-banner" role="alert">
            {error}
          </p>
        )}

        <div className="confirm-actions">
          <button type="button" className="btn btn-quiet" disabled={Boolean(busy)} onClick={onClose}>
            Decide later
          </button>
          <button type="button" className="btn btn-danger-quiet" disabled={Boolean(busy)} onClick={() => run("cancel", onCancel)}>
            {busy === "cancel" ? "Cancelling…" : "Cancel request"}
          </button>
          <button
            type="button"
            ref={approveRef}
            className="btn btn-accent"
            disabled={Boolean(busy) || expired}
            onClick={() => run("approve", onApprove)}
          >
            {busy === "approve" ? "Working…" : isOrder ? "Place simulated order" : "Approve"}
          </button>
        </div>
      </div>
    </div>
  );
}
