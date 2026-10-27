import { useEffect, useRef, useState } from "react";
import ChatMessage from "./ChatMessage.jsx";

const EXAMPLES = [
  "Find a wireless mouse under ₹2,000.",
  "I need a laptop for Python and machine learning. Budget ₹80,000, at least 16 GB RAM.",
  "Compare the five best laptops under ₹80,000 for Python programming.",
  "Find a wireless mouse under ₹2,000 and add the cheapest one rated above 4.2 to my cart.",
  "Tell me the specifications of product LAP-001.",
  "Add LAP-010 to my cart.",
  "Show me my cart.",
  "Find a laptop under ₹10,000 with 64 GB RAM.",
];

export default function ChatWindow({ messages, approvalsById, loading, error, onSend, onStop, compare, onReviewApproval, onDetails, modelReady }) {
  const [draft, setDraft] = useState("");
  const endRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, loading]);

  const send = (text) => {
    const value = (text ?? draft).trim();
    if (!value || loading) return;
    onSend(value);
    setDraft("");
    inputRef.current?.focus();
  };

  const onKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send();
    }
  };

  return (
    <section className="chat" aria-label="Conversation">
      <div className="chat-scroll">
        {messages.length === 0 && (
          <div className="chat-empty">
            <h2>What are you shopping for?</h2>
            <p>
              Ask in plain language. The assistant decides which catalog and cart tools to call, and you can watch
              every step in <strong>Agent activity</strong>. Cart changes always wait for your approval.
            </p>
            {!modelReady && (
              <p className="warning-note">
                The OpenAI model isn’t configured yet — set <code>OPENAI_API_KEY</code> and <code>OPENAI_MODEL</code> in the
                backend <code>.env</code> and restart FastAPI. Browsing and the cart work without it.
              </p>
            )}
            <div className="examples">
              {EXAMPLES.map((ex) => (
                <button key={ex} type="button" className="example" onClick={() => send(ex)}>
                  {ex}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m) => (
          <ChatMessage
            key={m.key}
            message={m}
            approvals={(m.approvalIds || []).map((id) => approvalsById[id]).filter(Boolean)}
            compare={compare}
            onReviewApproval={onReviewApproval}
            onDetails={onDetails}
          />
        ))}
        {loading && (
          <div className="msg msg-assistant">
            <div className="msg-avatar" aria-hidden="true">S</div>
            <div className="bubble thinking" role="status">
              <span className="dot" />
              <span className="dot" />
              <span className="dot" />
              <span className="sr-only">The assistant is working</span>
            </div>
          </div>
        )}
        {error && (
          <div className="error-banner" role="alert">
            {error}
          </div>
        )}
        <div ref={endRef} />
      </div>

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
      >
        <label htmlFor="chat-input" className="sr-only">
          Message
        </label>
        <textarea
          id="chat-input"
          ref={inputRef}
          rows={2}
          value={draft}
          maxLength={2000}
          placeholder="Ask for products, comparisons or cart changes…  (Enter to send, Shift+Enter for a new line)"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
        />
        {loading ? (
          <button type="button" className="btn btn-quiet" onClick={onStop}>
            Stop
          </button>
        ) : (
          <button type="submit" className="btn btn-primary" disabled={!draft.trim()}>
            Send
          </button>
        )}
      </form>
    </section>
  );
}
