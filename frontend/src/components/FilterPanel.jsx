import { useEffect, useState } from "react";
import { api } from "../services/api.js";
import { CATEGORY_NAMES } from "../services/format.js";
import ProductGrid from "./ProductGrid.jsx";

const EMPTY = { category: "", q: "", max_price: "", min_rating: "", min_ram_gb: "", in_stock_only: false, sort_by: "relevance" };

// Manual catalog browsing via the REST API (no AI involved).
export default function FilterPanel({ compare, onDetails }) {
  const [filters, setFilters] = useState(EMPTY);
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const limit = 12;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .products({ ...filters, limit, offset })
      .then((d) => !cancelled && (setData(d), setError(null)))
      .catch((e) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [filters, offset]);

  const set = (k, v) => {
    setOffset(0);
    setFilters((f) => ({ ...f, [k]: v }));
  };

  return (
    <section className="browse" aria-label="Browse catalog">
      <form className="filters" onSubmit={(e) => e.preventDefault()}>
        <label>
          Category
          <select value={filters.category} onChange={(e) => set("category", e.target.value)}>
            <option value="">All categories</option>
            {Object.entries(CATEGORY_NAMES).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
        </label>
        <label>
          Keywords
          <input type="search" value={filters.q} placeholder="e.g. RTX, silent" onChange={(e) => set("q", e.target.value)} />
        </label>
        <label>
          Max price (₹)
          <input type="number" min="0" step="500" value={filters.max_price} onChange={(e) => set("max_price", e.target.value)} />
        </label>
        <label>
          Min rating
          <select value={filters.min_rating} onChange={(e) => set("min_rating", e.target.value)}>
            <option value="">Any</option>
            {[3.5, 4, 4.2, 4.5].map((r) => (
              <option key={r} value={r}>{r}+</option>
            ))}
          </select>
        </label>
        <label>
          Min RAM
          <select value={filters.min_ram_gb} onChange={(e) => set("min_ram_gb", e.target.value)}>
            <option value="">Any</option>
            {[8, 16, 32].map((r) => (
              <option key={r} value={r}>{r} GB+</option>
            ))}
          </select>
        </label>
        <label>
          Sort
          <select value={filters.sort_by} onChange={(e) => set("sort_by", e.target.value)}>
            <option value="relevance">Relevance</option>
            <option value="price_asc">Price: low to high</option>
            <option value="price_desc">Price: high to low</option>
            <option value="rating_desc">Rating</option>
          </select>
        </label>
        <label className="check">
          <input type="checkbox" checked={filters.in_stock_only} onChange={(e) => set("in_stock_only", e.target.checked)} />
          In stock only
        </label>
        <button type="button" className="btn btn-quiet" onClick={() => (setFilters(EMPTY), setOffset(0))}>
          Clear filters
        </button>
      </form>

      {error && <div className="error-banner" role="alert">{error}</div>}
      {data && (
        <>
          <p className="muted small browse-count" aria-live="polite">
            {loading ? "Loading…" : `${data.total_matches} product(s)`}
          </p>
          {data.total_matches === 0 ? (
            <p className="muted">No products match. Try removing a filter.</p>
          ) : (
            <ProductGrid
              products={data.products}
              selectedIds={compare.selected}
              onToggleCompare={compare.toggle}
              onCompareSelected={compare.run}
              onDetails={onDetails}
            />
          )}
          {data.total_matches > limit && (
            <div className="pager">
              <button type="button" className="btn btn-quiet" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>
                Previous
              </button>
              <span className="muted small">
                {offset + 1}–{Math.min(offset + limit, data.total_matches)} of {data.total_matches}
              </span>
              <button type="button" className="btn btn-quiet" disabled={offset + limit >= data.total_matches} onClick={() => setOffset(offset + limit)}>
                Next
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
