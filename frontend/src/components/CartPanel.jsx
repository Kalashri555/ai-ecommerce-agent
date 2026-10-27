import { useState } from "react";
import { inr } from "../services/api.js";

// Buttons here are the USER's own direct actions, so they apply immediately.
// Changes the AI agent proposes always go through the confirmation dialog.
export default function CartPanel({ cart, orders, busy, onChangeQty, onRemove, onCheckout, onDetails }) {
  const [pending, setPending] = useState(null);
  const items = cart?.items || [];

  const act = async (key, fn) => {
    setPending(key);
    try {
      await fn();
    } finally {
      setPending(null);
    }
  };

  return (
    <div className="cart">
      {items.length === 0 ? (
        <p className="muted cart-empty">Your cart is empty. Ask the assistant to find something, or browse the catalog.</p>
      ) : (
        <>
          <ul className="cart-items">
            {items.map((it) => (
              <li key={it.product_id} className="cart-item">
                <div className="cart-item-main">
                  <button type="button" className="link cart-name" onClick={() => onDetails(it.product_id)}>
                    {it.name}
                  </button>
                  <div className="muted small">
                    {it.product_id} · {inr(it.unit_price)} each
                    {it.price_when_added !== it.unit_price && (
                      <span className="text-warn"> · was {inr(it.price_when_added)} when added</span>
                    )}
                  </div>
                  {!it.stock_ok && <div className="text-bad small">Only {it.available_quantity} available</div>}
                </div>
                <div className="qty" aria-label={`Quantity for ${it.name}`}>
                  <button
                    type="button"
                    aria-label="Decrease quantity"
                    disabled={busy || pending || it.quantity <= 1}
                    onClick={() => act(it.product_id, () => onChangeQty(it.product_id, it.quantity - 1))}
                  >
                    −
                  </button>
                  <span>{it.quantity}</span>
                  <button
                    type="button"
                    aria-label="Increase quantity"
                    disabled={busy || pending || it.quantity >= Math.min(10, it.available_quantity)}
                    onClick={() => act(it.product_id, () => onChangeQty(it.product_id, it.quantity + 1))}
                  >
                    +
                  </button>
                </div>
                <div className="cart-line">
                  <strong>{inr(it.line_total)}</strong>
                  <button
                    type="button"
                    className="link small text-bad"
                    disabled={busy || pending}
                    onClick={() => act(it.product_id, () => onRemove(it.product_id))}
                  >
                    Remove
                  </button>
                </div>
              </li>
            ))}
          </ul>
          <div className="cart-total">
            <span>Subtotal ({cart.item_count} item{cart.item_count > 1 ? "s" : ""})</span>
            <strong>{inr(cart.subtotal)}</strong>
          </div>
          <p className="muted small">Shipping and taxes aren’t calculated in this demo.</p>
          <button type="button" className="btn btn-primary btn-block" disabled={busy} onClick={onCheckout}>
            Review order
          </button>
        </>
      )}
      {orders?.length > 0 && (
        <div className="orders">
          <h3>Simulated orders</h3>
          <ul>
            {orders.slice(0, 5).map((o) => (
              <li key={o.order_id}>
                <span>{o.order_id}</span>
                <span className="muted small">{o.items.length} item(s)</span>
                <strong>{inr(o.total)}</strong>
              </li>
            ))}
          </ul>
          <p className="muted small">No payment was taken; these orders exist only in the demo database.</p>
        </div>
      )}
    </div>
  );
}
