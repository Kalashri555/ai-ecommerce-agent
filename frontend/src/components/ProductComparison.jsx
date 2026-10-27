import { inr } from "../services/api.js";
import { COMPONENT_NAMES, toComparisonRows } from "../services/format.js";

// Renders either a ranking (rank_products) or a comparison (compare_products).
export default function ProductComparison({ data, kind = "comparison", onDetails }) {
  if (!data) return null;
  if (kind === "ranking" && data.no_match) {
    return (
      <div className="no-match">
        <strong>No product meets every requirement.</strong>
        <p>
          {data.excluded_count} product(s) were excluded by hard constraints
          {data.hard_constraints ? ` (${Object.entries(data.hard_constraints).map(([k, v]) => `${k.replaceAll("_", " ")}: ${v}`).join(", ")})` : ""}.
          Try relaxing the budget or a specification.
        </p>
      </div>
    );
  }
  const rows = toComparisonRows(data, kind);
  if (!rows.length) return null;
  const hasRam = rows.some((r) => r.ram !== undefined);
  const hasBattery = rows.some((r) => r.battery !== undefined && r.battery !== null);
  const hasScore = rows.some((r) => r.total !== undefined && r.total !== null);
  const weights = data.weights;

  return (
    <section className="comparison" aria-label={kind === "ranking" ? "Ranking" : "Comparison"}>
      <div className="comparison-head">
        <h3>{kind === "ranking" ? "Ranked by the scoring algorithm" : "Side-by-side comparison"}</h3>
        {weights && (
          <p className="weights">
            Weights:{" "}
            {Object.entries(weights)
              .filter(([, w]) => w > 0)
              .map(([k, w]) => `${COMPONENT_NAMES[k]} ${Math.round(w * 100)}%`)
              .join(", ")}
            {data.use_case ? ` · profile: ${data.use_case.replaceAll("_", " ")}` : ""}
          </p>
        )}
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              {hasScore && <th scope="col">Rank</th>}
              <th scope="col">Product</th>
              <th scope="col" className="num">Price</th>
              <th scope="col" className="num">Rating</th>
              {hasRam && <th scope="col" className="num">RAM</th>}
              {hasBattery && <th scope="col" className="num">Battery</th>}
              <th scope="col">Availability</th>
              {hasScore && <th scope="col" className="num">Score</th>}
              <th scope="col">Strengths</th>
              <th scope="col">Limitations</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.product_id}>
                {hasScore && <td className="rank-cell">{r.rank ?? "—"}</td>}
                <th scope="row">
                  <button type="button" className="link" onClick={() => onDetails?.(r.product_id)}>
                    {r.name}
                  </button>
                  <div className="muted small">
                    {r.product_id}
                    {r.cpu ? ` · ${r.cpu}` : ""}
                    {r.gpu ? ` · ${r.gpu}` : ""}
                  </div>
                </th>
                <td className="num">{inr(r.price)}</td>
                <td className="num">
                  ★ {Number(r.rating).toFixed(1)}
                  <div className="muted small">{Number(r.reviews).toLocaleString("en-IN")} reviews</div>
                </td>
                {hasRam && <td className="num">{r.ram ? `${r.ram} GB` : "—"}</td>}
                {hasBattery && <td className="num">{r.battery || "—"}</td>}
                <td>{r.stock === undefined ? "—" : r.stock > 0 ? `${r.stock} in stock` : <span className="text-bad">Out of stock</span>}</td>
                {hasScore && (
                  <td className="num">
                    <strong className="score-pill">{r.total ?? "—"}</strong>
                    <div className="components">
                      {Object.entries(r.components)
                        .filter(([k, v]) => v !== null && (!weights || weights[k] > 0))
                        .map(([k, v]) => (
                          <span key={k} title={COMPONENT_NAMES[k]}>
                            {COMPONENT_NAMES[k].split(" ")[0]} {Math.round(v)}
                          </span>
                        ))}
                    </div>
                  </td>
                )}
                <td>{r.strengths.length ? r.strengths.join(", ") : <span className="muted">—</span>}</td>
                <td className="limits">
                  {r.limitations.length ? (
                    <ul>
                      {r.limitations.map((l) => (
                        <li key={l}>{l}</li>
                      ))}
                    </ul>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {kind === "ranking" && data.excluded_count > 0 && (
        <p className="muted small">
          {data.eligible_count} eligible · {data.excluded_count} excluded by hard constraints. Scores are computed by
          backend code, not by the language model.
        </p>
      )}
    </section>
  );
}
