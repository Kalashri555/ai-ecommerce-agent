import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, hasToken, setToken } from "./services/api.js";
import ChatWindow from "./components/ChatWindow.jsx";
import CartPanel from "./components/CartPanel.jsx";
import ConfirmationDialog from "./components/ConfirmationDialog.jsx";
import FilterPanel from "./components/FilterPanel.jsx";
import LoginScreen from "./components/LoginScreen.jsx";
import ProductComparison from "./components/ProductComparison.jsx";
import ProductDetailsModal from "./components/ProductDetailsModal.jsx";
import ToolExecutionPanel from "./components/ToolExecutionPanel.jsx";

let keySeq = 0;
const nextKey = () => `k${++keySeq}`;

function assistantMessage(r) {
  return {
    key: nextKey(),
    role: "assistant",
    text: r.reply,
    products: r.products,
    ranking: r.ranking,
    comparison: r.comparison,
    approvalIds: (r.pending_approvals || []).map((a) => a.approval_id),
    error: r.error,
    toolCount: (r.tool_trace || []).length,
    rounds: r.rounds,
    durationMs: r.duration_ms,
  };
}

export default function App() {
  const [user, setUser] = useState(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [health, setHealth] = useState(null);
  const [tools, setTools] = useState(null);

  const [conversationId, setConversationId] = useState(null);
  const [conversations, setConversations] = useState([]);
  const [showHistory, setShowHistory] = useState(false);
  const [messages, setMessages] = useState([]);
  const [turns, setTurns] = useState([]);
  const [approvalsById, setApprovalsById] = useState({});
  const [activeApproval, setActiveApproval] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const abortRef = useRef(null);

  const [cart, setCart] = useState(null);
  const [orders, setOrders] = useState([]);
  const [mainTab, setMainTab] = useState("chat");
  const [railTab, setRailTab] = useState("activity");
  const [detailsId, setDetailsId] = useState(null);
  const [compareIds, setCompareIds] = useState([]);
  const [manualComparison, setManualComparison] = useState(null);
  const closeApproval = useCallback(() => setActiveApproval(null), []);
  const closeDetails = useCallback(() => setDetailsId(null), []);

  // ---------------------------------------------------------------- session
  useEffect(() => {
    if (!hasToken()) {
      setAuthChecked(true);
      return;
    }
    api.me().then(setUser).catch(() => setToken(null)).finally(() => setAuthChecked(true));
  }, []);

  const refreshCart = useCallback(async () => {
    try {
      const [c, o] = await Promise.all([api.cart(), api.orders()]);
      setCart(c);
      setOrders(o.orders);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setUser(null);
    }
  }, []);

  useEffect(() => {
    if (!user) return;
    api.health().then(setHealth).catch(() => setHealth(null));
    api.tools().then((d) => setTools(d.tools)).catch(() => setTools(null));
    api.conversations().then((d) => setConversations(d.conversations)).catch(() => {});
    refreshCart();
  }, [user, refreshCart]);

  const onLogin = ({ token, user: u }) => {
    setToken(token);
    setUser(u);
  };

  const logout = () => {
    setToken(null);
    setUser(null);
    newConversation();
    setCart(null);
  };

  // ---------------------------------------------------------------- chat
  const newConversation = () => {
    abortRef.current?.abort();
    setConversationId(null);
    setMessages([]);
    setTurns([]);
    setApprovalsById({});
    setError(null);
    setManualComparison(null);
    setShowHistory(false);
  };

  const absorbTurn = (r, request) => {
    setConversationId(r.conversation_id);
    setMessages((m) => [...m, assistantMessage(r)]);
    setApprovalsById((prev) => {
      const next = { ...prev };
      (r.pending_approvals || []).forEach((a) => (next[a.approval_id] = a));
      return next;
    });
    setTurns((t) => [
      ...t.filter((x) => !x.pending),
      { key: nextKey(), request, trace: r.tool_trace || [], error: r.error, pending: false },
    ]);
    if (r.cart) setCart(r.cart);
    if ((r.pending_approvals || []).length) {
      setActiveApproval(r.pending_approvals[r.pending_approvals.length - 1]);
    }
  };

  const send = async (text) => {
    setError(null);
    setMessages((m) => [...m, { key: nextKey(), role: "user", text }]);
    setTurns((t) => [...t, { key: nextKey(), request: text, trace: [], pending: true }]);
    setLoading(true);
    setRailTab("activity");
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const r = await api.chat(text, conversationId, controller.signal);
      absorbTurn(r, text);
      api.conversations().then((d) => setConversations(d.conversations)).catch(() => {});
    } catch (e) {
      setTurns((t) => t.filter((x) => !x.pending));
      if (e.name === "AbortError") {
        setError("Stopped waiting. The server may still finish this request; it will appear in the conversation history.");
      } else {
        setError(e.message);
        if (e instanceof ApiError && e.status === 401) setUser(null);
      }
    } finally {
      setLoading(false);
      abortRef.current = null;
    }
  };

  const openConversation = async (id) => {
    setShowHistory(false);
    try {
      const [{ messages: rows }, pend] = await Promise.all([api.messages(id), api.pendingApprovals(id)]);
      const msgs = [];
      const rebuilt = [];
      let lastUser = "";
      rows.forEach((m) => {
        if (m.role === "user") {
          lastUser = m.text;
          msgs.push({ key: nextKey(), role: "user", text: m.text });
        } else if (m.role === "event") {
          msgs.push({ key: nextKey(), role: "event", text: m.text });
          lastUser = "(after your approval)";
        } else {
          const md = m.metadata || {};
          msgs.push({
            key: nextKey(), role: "assistant", text: m.text, products: md.products, ranking: md.ranking,
            comparison: md.comparison, approvalIds: md.approval_ids || [], error: md.error,
            toolCount: (md.tool_trace || []).length, rounds: md.rounds, durationMs: md.duration_ms,
          });
          rebuilt.push({ key: nextKey(), request: lastUser, trace: md.tool_trace || [], error: md.error, pending: false });
        }
      });
      const byId = {};
      pend.approvals.forEach((a) => (byId[a.approval_id] = a));
      setConversationId(id);
      setMessages(msgs);
      setTurns(rebuilt);
      setApprovalsById(byId);
      setMainTab("chat");
    } catch (e) {
      setError(e.message);
    }
  };

  // ---------------------------------------------------------------- approvals
  const updateApproval = (a) => setApprovalsById((prev) => ({ ...prev, [a.approval_id]: a }));

  const approve = async (approval) => {
    const isCheckout = approval.action_type === "place_order" && !approval.conversation_id;
    try {
      const res = isCheckout ? await api.checkoutConfirm(approval.approval_id) : await api.approve(approval.approval_id);
      updateApproval(res.approval);
      setActiveApproval(null);
      const status = res.result?.approval_status;
      if (res.agent) {
        absorbTurn(res.agent, "(continuing after your approval)");
      } else if (status === "executed") {
        const what = res.result.order_id ? `Simulated order ${res.result.order_id} placed.` : `Done: ${approval.summary?.title}.`;
        setMessages((m) => [...m, { key: nextKey(), role: "event", text: what }]);
      }
      if (status && status !== "executed") {
        const msg = res.result?.error?.message || res.result?.message || "The action was not completed.";
        setMessages((m) => [...m, { key: nextKey(), role: "event", text: `Not done: ${msg}` }]);
      }
      await refreshCart();
    } catch (e) {
      if (e instanceof ApiError && [404, 409, 410].includes(e.status)) {
        // stale/expired/used approval - reflect it and let the dialog show why
        updateApproval({ ...approval, status: e.status === 410 ? "expired" : "failed" });
      }
      throw e;
    }
  };

  const cancelApproval = async (approval) => {
    const res = await api.cancelApproval(approval.approval_id);
    updateApproval(res.approval);
    setActiveApproval(null);
    setMessages((m) => [...m, { key: nextKey(), role: "event", text: `Cancelled: ${approval.summary?.title}. Nothing changed.` }]);
  };

  // ---------------------------------------------------------------- cart (direct user actions)
  const changeQty = async (pid, qty) => {
    try {
      await api.updateCartItem(pid, qty);
    } catch (e) {
      setError(e.message);
    }
    await refreshCart();
  };
  const removeItem = async (pid) => {
    try {
      await api.removeCartItem(pid);
    } catch (e) {
      setError(e.message);
    }
    await refreshCart();
  };
  const addDirect = async (pid) => {
    await api.addToCart(pid, 1);
    await refreshCart();
  };
  const checkout = async () => {
    try {
      const { approval } = await api.checkoutPreview();
      setActiveApproval(approval);
    } catch (e) {
      setError(e.message);
    }
  };

  // ---------------------------------------------------------------- compare selection
  const toggleCompare = (pid) =>
    setCompareIds((ids) => (ids.includes(pid) ? ids.filter((x) => x !== pid) : ids.length >= 6 ? ids : [...ids, pid]));
  const runCompare = async () => {
    try {
      const data = await api.compare(compareIds);
      setManualComparison(data);
    } catch (e) {
      setError(e.message);
    }
  };
  const compare = { selected: compareIds, toggle: toggleCompare, run: runCompare };

  // ---------------------------------------------------------------- render
  if (!authChecked) return null;
  if (!user) return <LoginScreen onLogin={onLogin} />;

  const modelReady = Boolean(health?.openai_configured);
  const mcpOk = health && Object.values(health.mcp || {}).every((s) => s.connected);
  const cartCount = cart?.item_count || 0;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          ShopMate
        </div>
        <div className="status-pills">
          <span className={`pill ${modelReady ? "pill-ok" : "pill-warn"}`} title="OpenAI model used by the agent">
            {modelReady ? `Model: ${health.openai_model}` : "Model not configured"}
          </span>
          <span className={`pill ${mcpOk ? "pill-ok" : "pill-bad"}`} title="MCP server connections">
            {health ? (mcpOk ? `MCP: ${health.tools_available} tools` : "MCP server offline") : "Checking…"}
          </span>
        </div>
        <div className="topbar-actions">
          <div className="history">
            <button type="button" className="btn btn-quiet" aria-expanded={showHistory} onClick={() => setShowHistory(!showHistory)}>
              History
            </button>
            {showHistory && (
              <div className="history-pop" role="menu">
                {conversations.length === 0 && <p className="muted small">No conversations yet.</p>}
                {conversations.map((c) => (
                  <button key={c.conversation_id} type="button" role="menuitem" className={c.conversation_id === conversationId ? "is-current" : ""} onClick={() => openConversation(c.conversation_id)}>
                    <span>{c.title}</span>
                    <span className="muted small">{new Date(c.updated_at).toLocaleString()}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
          <button type="button" className="btn btn-primary" onClick={newConversation}>
            New conversation
          </button>
          <button type="button" className="btn btn-quiet" onClick={logout} title={`Signed in as ${user.username}`}>
            Sign out {user.display_name}
          </button>
        </div>
      </header>

      <div className="layout">
        <main className="main">
          <nav className="tabs" aria-label="Main">
            <button type="button" className={mainTab === "chat" ? "is-active" : ""} onClick={() => setMainTab("chat")}>
              Assistant
            </button>
            <button type="button" className={mainTab === "browse" ? "is-active" : ""} onClick={() => setMainTab("browse")}>
              Browse catalog
            </button>
          </nav>

          {manualComparison && (
            <div className="manual-compare">
              <ProductComparison data={manualComparison} kind="comparison" onDetails={setDetailsId} />
              <div className="manual-compare-actions">
                <button type="button" className="btn btn-quiet" onClick={() => setManualComparison(null)}>Close comparison</button>
                <button type="button" className="btn btn-quiet" onClick={() => (setCompareIds([]), setManualComparison(null))}>Clear selection</button>
              </div>
            </div>
          )}

          {mainTab === "chat" ? (
            <ChatWindow
              messages={messages}
              approvalsById={approvalsById}
              loading={loading}
              error={error}
              onSend={send}
              onStop={() => abortRef.current?.abort()}
              compare={compare}
              onReviewApproval={setActiveApproval}
              onDetails={setDetailsId}
              modelReady={modelReady}
            />
          ) : (
            <FilterPanel compare={compare} onDetails={setDetailsId} />
          )}
        </main>

        <aside className="rail" aria-label="Agent activity and cart">
          <nav className="tabs" aria-label="Side panel">
            <button type="button" className={railTab === "activity" ? "is-active" : ""} onClick={() => setRailTab("activity")}>
              Agent activity
            </button>
            <button type="button" className={railTab === "cart" ? "is-active" : ""} onClick={() => setRailTab("cart")}>
              Cart{cartCount ? ` (${cartCount})` : ""}
            </button>
          </nav>
          {railTab === "activity" ? (
            <ToolExecutionPanel turns={turns} tools={tools} health={health} />
          ) : (
            <CartPanel cart={cart} orders={orders} busy={loading} onChangeQty={changeQty} onRemove={removeItem} onCheckout={checkout} onDetails={setDetailsId} />
          )}
        </aside>
      </div>

      <ConfirmationDialog approval={activeApproval} onApprove={approve} onCancel={cancelApproval} onClose={closeApproval} />
      <ProductDetailsModal productId={detailsId} onClose={closeDetails} onAdd={addDirect} />
    </div>
  );
}
