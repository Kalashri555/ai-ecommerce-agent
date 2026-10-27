import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import ProductGrid from "./ProductGrid.jsx";
import ProductComparison from "./ProductComparison.jsx";
import { inr } from "../services/api.js";

function ApprovalCard({ approval, onReview }) {
  const s = approval.summary || {};
  const label = {
    pending: "Waiting for your confirmation",
    executed: "Approved and done",
    cancelled: "Cancelled — nothing changed",
    expired: "Expired — ask again if you still want this",
    invalidated: "Not done — details changed, please confirm again",
    failed: "Not done — the action failed",
    approved: "Approved — running",
  }[approval.status] || approval.status;
  return (
    <div className={`approval-card status-${approval.status}`}>
      <div>
        <div className="approval-title">{s.title || approval.action_type}</div>
        <div className="muted small">
          {label}
          {s.estimated_price ? ` · est. ${inr(s.estimated_price)}` : ""}
          {s.estimated_total ? ` · est. total ${inr(s.estimated_total)}` : ""}
        </div>
      </div>
      {approval.status === "pending" && (
        <button type="button" className="btn btn-accent" onClick={() => onReview(approval)}>
          Review
        </button>
      )}
    </div>
  );
}

export default function ChatMessage({ message, approvals, compare, onReviewApproval, onDetails }) {
  if (message.role === "event") {
    return <div className="event-line">{message.text}</div>;
  }
  const isUser = message.role === "user";
  const scores = {};
  (message.ranking?.ranked || []).forEach((r) => (scores[r.product_id] = r.total_score));
  const products = message.products || [];

  return (
    <div className={`msg ${isUser ? "msg-user" : "msg-assistant"}`}>
      {!isUser && <div className="msg-avatar" aria-hidden="true">S</div>}
      <div className="msg-content">
        <div className={`bubble ${message.error ? "bubble-error" : ""}`}>
          {isUser ? (
            <p>{message.text}</p>
          ) : (
            <div className="markdown">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.text}</ReactMarkdown>
            </div>
          )}
        </div>
        {!isUser && (
          <>
            {approvals?.length > 0 && (
              <div className="approvals">
                {approvals.map((a) => (
                  <ApprovalCard key={a.approval_id} approval={a} onReview={onReviewApproval} />
                ))}
              </div>
            )}
            {message.ranking && <ProductComparison data={message.ranking} kind="ranking" onDetails={onDetails} />}
            {message.comparison && <ProductComparison data={message.comparison} kind="comparison" onDetails={onDetails} />}
            {products.length > 0 && (
              <ProductGrid
                products={products}
                scores={scores}
                selectedIds={compare.selected}
                onToggleCompare={compare.toggle}
                onCompareSelected={compare.run}
                onDetails={onDetails}
              />
            )}
            {message.toolCount > 0 && (
              <div className="msg-foot muted small">
                {message.toolCount} tool call{message.toolCount > 1 ? "s" : ""} · {message.rounds} round
                {message.rounds > 1 ? "s" : ""} · {(message.durationMs / 1000).toFixed(1)} s
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
