import { useState } from "react";

const STATUS_TEXT = {
  ok: "Completed",
  error: "Failed",
  rejected: "Rejected by the backend",
  awaiting_approval: "Waiting for your confirmation",
  limit: "Stopped",
  running: "Running",
};

const SERVER_NAMES = { product_catalog: "Product Catalog MCP", shopping_cart: "Shopping Cart MCP" };

function Step({ step, index }) {
  const [open, setOpen] = useState(false);
  return (
    <li className={`knot server-${step.server || "none"} status-${step.status}`}>
      <span className="knot-dot" aria-hidden="true" />
      <div className="knot-body">
        <button type="button" className="knot-head" onClick={() => setOpen(!open)} aria-expanded={open}>
          <span className="knot-index">{index}</span>
          <span className="knot-label">{step.label}</span>
          <span className={`knot-status s-${step.status}`}>{STATUS_TEXT[step.status] || step.status}</span>
        </button>
        <div className="knot-summary">{step.summary}</div>
        <div className="knot-meta muted small">
          <code>{step.tool}</code>
          {step.server ? ` on ${SERVER_NAMES[step.server] || step.server}` : ""}
          {step.duration_ms ? ` · ${step.duration_ms} ms` : ""}
        </div>
        {open && (
          <div className="knot-detail">
            <div className="detail-label">Validated arguments</div>
            <pre>{JSON.stringify(step.arguments ?? {}, null, 2)}</pre>
            {step.result_preview && (
              <>
                <div className="detail-label">Tool result (truncated)</div>
                <pre>{prettyPreview(step.result_preview)}</pre>
              </>
            )}
          </div>
        )}
      </div>
    </li>
  );
}

function prettyPreview(text) {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

function Turn({ turn }) {
  const rounds = [...new Set(turn.trace.map((t) => t.round))];
  let n = 0;
  return (
    <div className="turn">
      <div className="turn-request">
        <span className="turn-tag">You asked</span>
        <p>{turn.request}</p>
      </div>
      {turn.trace.length === 0 && !turn.pending && (
        <p className="muted small turn-none">No tools were needed — the model answered directly.</p>
      )}
      {rounds.map((r) => (
        <div key={r} className="round">
          <div className="round-label">
            Round {r}: model chose {turn.trace.filter((t) => t.round === r).length} tool call(s)
          </div>
          <ol className="thread">
            {turn.trace
              .filter((t) => t.round === r)
              .map((t) => {
                n += 1;
                return <Step key={t.id + n} step={t} index={n} />;
              })}
          </ol>
        </div>
      ))}
      {turn.pending ? (
        <p className="muted small turn-none">Waiting for the model…</p>
      ) : (
        <div className="turn-final">
          <span className="turn-tag">{turn.error ? "Ended with an error" : "Final response sent"}</span>
          {turn.error && <p className="text-bad small">{turn.error.message}</p>}
        </div>
      )}
    </div>
  );
}

function ToolDefinitions({ tools }) {
  const [openName, setOpenName] = useState(null);
  if (!tools) return null;
  return (
    <details className="tool-defs">
      <summary>
        Tools the model can call ({tools.filter((t) => t.available).length} discovered via MCP)
      </summary>
      <ul>
        {tools.map((t) => (
          <li key={t.name} className={t.available ? "" : "is-unavailable"}>
            <button type="button" className="link" onClick={() => setOpenName(openName === t.name ? null : t.name)}>
              <code>{t.name}</code>
            </button>
            <span className="muted small">
              {" "}
              {SERVER_NAMES[t.server]}
              {t.requires_approval ? " · needs your approval" : ""}
              {!t.available ? " · unavailable" : ""}
            </span>
            {openName === t.name && t.openai_definition && (
              <pre>{JSON.stringify(t.openai_definition, null, 2)}</pre>
            )}
          </li>
        ))}
      </ul>
    </details>
  );
}

export default function ToolExecutionPanel({ turns, tools, health }) {
  const mcp = health?.mcp || {};
  return (
    <div className="activity">
      <div className="servers">
        {Object.entries(mcp).map(([name, s]) => (
          <div key={name} className={`server-chip server-${name} ${s.connected ? "" : "is-down"}`} title={s.error || s.server_info}>
            <span className="server-dot" aria-hidden="true" />
            {SERVER_NAMES[name] || name}
            <span className="muted small">{s.connected ? `${s.tools.length} tools` : "offline"}</span>
          </div>
        ))}
      </div>
      <ToolDefinitions tools={tools} />
      {turns.length === 0 ? (
        <p className="muted activity-empty">
          Send a message to see which tools the model selects, the validated arguments, the MCP server that ran each
          one, and the result.
        </p>
      ) : (
        <div className="turns">
          {[...turns].reverse().map((t) => (
            <Turn key={t.key} turn={t} />
          ))}
        </div>
      )}
    </div>
  );
}
