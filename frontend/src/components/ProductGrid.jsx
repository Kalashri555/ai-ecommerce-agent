import ProductCard from "./ProductCard.jsx";

const MAX_COMPARE = 6;

export default function ProductGrid({ products, scores = {}, selectedIds, onToggleCompare, onDetails, onCompareSelected }) {
  if (!products?.length) return null;
  const selectedHere = products.filter((p) => selectedIds.includes(p.product_id)).length;
  return (
    <section className="product-grid-wrap" aria-label="Products">
      <div className="product-grid">
        {products.map((p) => (
          <ProductCard
            key={p.product_id}
            product={p}
            score={scores[p.product_id]}
            selected={selectedIds.includes(p.product_id)}
            compareDisabled={selectedIds.length >= MAX_COMPARE}
            onToggleCompare={onToggleCompare}
            onDetails={onDetails}
          />
        ))}
      </div>
      {selectedIds.length >= 2 && selectedHere > 0 && (
        <div className="compare-bar">
          <span>{selectedIds.length} selected for comparison</span>
          <button type="button" className="btn btn-primary" onClick={onCompareSelected}>
            Compare selected
          </button>
        </div>
      )}
      <p className="demo-note">Demonstration catalog — not live retailer listings.</p>
    </section>
  );
}
