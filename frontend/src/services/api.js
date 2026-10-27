// Single place for all backend communication.
// The browser only ever talks to OUR FastAPI backend. The OpenAI key stays on the server.

const BASE = (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");
const TOKEN_KEY = "shopmate.token";

let token = null;
try {
  token = window.localStorage.getItem(TOKEN_KEY);
} catch {
  token = null; // storage can be blocked; the app still works for this tab
}

export function setToken(value) {
  token = value;
  try {
    if (value) window.localStorage.setItem(TOKEN_KEY, value);
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

export function hasToken() {
  return Boolean(token);
}

export class ApiError extends Error {
  constructor(status, code, message, details) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

async function request(path, { method = "GET", body, signal } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  let res;
  try {
    res = await fetch(`${BASE}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new ApiError(0, "network_error", `Can't reach the backend at ${BASE}. Is FastAPI running?`);
  }
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (!res.ok) {
    const e = data?.error || {};
    if (res.status === 401) setToken(null);
    throw new ApiError(res.status, e.code || "http_error", e.message || `Request failed (${res.status}).`, e.details);
  }
  return data;
}

const qs = (params) => {
  const s = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") s.set(k, v);
  });
  const str = s.toString();
  return str ? `?${str}` : "";
};

export const api = {
  baseUrl: BASE,
  login: (username, password) => request("/api/auth/login", { method: "POST", body: { username, password } }),
  me: () => request("/api/auth/me"),
  health: () => request("/api/health"),
  tools: () => request("/api/tools"),

  chat: (message, conversationId, signal) =>
    request("/api/chat", { method: "POST", body: { message, conversation_id: conversationId }, signal }),
  approve: (id) => request(`/api/approvals/${id}/approve`, { method: "POST", body: { continue_agent: true } }),
  cancelApproval: (id) => request(`/api/approvals/${id}/cancel`, { method: "POST" }),
  pendingApprovals: (conversationId) => request(`/api/approvals${qs({ conversation_id: conversationId })}`),

  conversations: () => request("/api/conversations"),
  messages: (id) => request(`/api/conversations/${id}/messages`),

  products: (params) => request(`/api/products${qs(params)}`),
  product: (id) => request(`/api/products/${id}`),
  compare: (ids, budget) => request(`/api/products/compare${qs({ ids: ids.join(","), budget })}`),

  cart: () => request("/api/cart"),
  addToCart: (productId, quantity = 1) =>
    request("/api/cart/items", { method: "POST", body: { product_id: productId, quantity } }),
  updateCartItem: (productId, quantity) =>
    request(`/api/cart/items/${productId}`, { method: "PATCH", body: { quantity } }),
  removeCartItem: (productId) => request(`/api/cart/items/${productId}`, { method: "DELETE" }),
  checkoutPreview: () => request("/api/checkout/preview", { method: "POST" }),
  checkoutConfirm: (approvalId) => request("/api/checkout/confirm", { method: "POST", body: { approval_id: approvalId } }),
  orders: () => request("/api/orders"),
};

export const inr = (value) => {
  const n = Number(value);
  if (value === null || value === undefined || Number.isNaN(n)) return "—";
  return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: n % 1 ? 2 : 0 }).format(n);
};
