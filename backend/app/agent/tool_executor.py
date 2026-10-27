"""Validates, authorises and executes the tool calls requested by the model.

For every call:
  1. Is the tool registered AND discovered from its MCP server?  (allow-list)
  2. Are the arguments valid JSON and valid for the Pydantic input model?
  3. Does policy require human approval?
       yes -> gather facts via read-only MCP tools, create a PendingApproval,
              and tell the model "approval_required" (nothing is changed).
       no  -> call the tool on its MCP server with the signed user context.
  4. Record a sanitised trace step for the UI and for debugging.

Approved actions are executed later through `execute_approved`, which
re-validates prices/stock first and passes the approval id to the MCP server
(which consumes it atomically).
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from app.agent import approval_service
from app.agent.mcp_client import MCPCallResult, MCPClientManager, MCPUnavailable
from app.agent.tool_registry import ToolRegistry, ToolSpec
from app.auth.mcp_context import build_meta
from app.config import get_settings
from app.database.connection import session_scope


@dataclass
class ExecContext:
    user_id: int
    conversation_id: str | None = None
    round: int = 0


@dataclass
class ToolOutcome:
    output: dict[str, Any]                 # what the model sees (JSON)
    trace: dict[str, Any]                  # what the UI / logs see
    ui: dict[str, Any] = field(default_factory=dict)
    approval: dict[str, Any] | None = None


def _err(code: str, message: str, details: dict | None = None, outcome: str = "failed") -> dict[str, Any]:
    return {"ok": False, "outcome": outcome, "error": {"code": code, "message": message, "details": details or {}}}


def _inr(value: str | Decimal | None) -> str:
    if value is None:
        return "-"
    return f"₹{Decimal(str(value)):,.2f}"


class ToolExecutor:
    def __init__(self, mcp: MCPClientManager, registry: ToolRegistry) -> None:
        self.mcp = mcp
        self.registry = registry

    # ------------------------------------------------------------------ main
    async def execute(self, name: str, raw_arguments: str | dict | None, ctx: ExecContext) -> ToolOutcome:
        started = time.perf_counter()
        trace = {"id": str(uuid.uuid4())[:8], "round": ctx.round, "tool": name, "server": None,
                 "label": name, "arguments": None, "status": "running", "duration_ms": 0, "summary": ""}

        def finish(output: dict[str, Any], status: str, summary: str, ui: dict | None = None,
                   approval: dict | None = None) -> ToolOutcome:
            trace.update(status=status, summary=summary, duration_ms=int((time.perf_counter() - started) * 1000),
                         # Truncated copy of the real result, for the learning UI / debugging.
                         result_preview=json.dumps(output, default=str, ensure_ascii=False)[:1500])
            if not output.get("ok", True) and "error" in output:
                trace["error"] = output["error"].get("code")
            return ToolOutcome(output=self._limit(output), trace=trace, ui=ui or {}, approval=approval)

        spec = self.registry.get(name)
        if spec is None or not self.registry.is_available(name):
            return finish(_err("unknown_tool", f"Tool '{name}' is not available. Use only the provided tools."),
                          "rejected", "Rejected: tool is not registered/available")
        trace.update(server=spec.server, label=spec.label)

        # -- parse + validate ------------------------------------------------
        try:
            args = raw_arguments if isinstance(raw_arguments, dict) else json.loads(raw_arguments or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments must be a JSON object")
        except (ValueError, json.JSONDecodeError) as exc:
            return finish(_err("invalid_json", f"Arguments were not valid JSON: {exc}"), "rejected", "Rejected: invalid JSON")
        args = {k: v for k, v in args.items() if v is not None}
        try:
            params = spec.input_model.model_validate(args)
        except ValidationError as exc:
            issues = [{"field": ".".join(str(x) for x in e["loc"]), "message": e["msg"]} for e in exc.errors()]
            trace["arguments"] = _sanitize(args)
            return finish(_err("invalid_arguments", "Invalid tool arguments.", {"issues": issues}),
                          "rejected", "Rejected: invalid arguments - " + "; ".join(i["message"] for i in issues)[:200])
        validated = params.model_dump(exclude_none=True)
        trace["arguments"] = _sanitize(validated)

        # -- approval policy -----------------------------------------------
        if spec.requires_approval:
            return await self._request_approval(spec, validated, ctx, finish)

        # -- execute via MCP -----------------------------------------------
        try:
            res = await self.mcp.call_tool(name, validated, build_meta(ctx.user_id), idempotent=spec.idempotent)
        except MCPUnavailable as exc:
            return finish(_err("mcp_unavailable", str(exc)), "error", f"MCP server unavailable ({spec.server})")
        return finish(res.data, "ok" if res.ok else "error", summarize(name, res.data), ui=ui_payload(name, res.data))

    # ---------------------------------------------------------- approvals
    async def _request_approval(self, spec: ToolSpec, args: dict[str, Any], ctx: ExecContext, finish) -> ToolOutcome:
        prepared = await self.prepare_action(spec.name, args, ctx.user_id)
        if not prepared["ok"]:
            return finish(prepared, "error", "Not possible: " + prepared["error"]["message"])
        args = prepared["arguments"]
        with session_scope() as db:
            a = approval_service.create_approval(db, user_id=ctx.user_id, action_type=spec.name, arguments=args,
                                                 summary=prepared["summary"], conversation_id=ctx.conversation_id)
            approval = approval_service.to_dict(a)
        output = {
            "ok": True, "status": "approval_required", "approval_id": approval["approval_id"],
            "action": spec.name, "summary": prepared["summary"],
            "message": ("NOT executed yet. The user is being shown a confirmation dialog. Tell the user to review and "
                        "approve or cancel it. Do not claim the action happened."),
        }
        return finish(output, "awaiting_approval", f"Waiting for your confirmation: {prepared['summary'].get('title', spec.name)}",
                      approval=approval)

    async def _read(self, tool: str, args: dict[str, Any], user_id: int) -> MCPCallResult:
        return await self.mcp.call_tool(tool, args, build_meta(user_id), idempotent=True)

    async def prepare_action(self, action: str, args: dict[str, Any], user_id: int) -> dict[str, Any]:
        """Gather current facts (via read-only MCP tools) for the confirmation dialog.

        Returns {"ok": True, "arguments": final_args, "summary": {...}} or an error dict.
        """
        try:
            if action in ("add_to_cart", "update_cart", "remove_from_cart"):
                cart = await self._read("get_cart", {}, user_id)
                if not cart.ok:
                    return cart.data
                in_cart = {i["product_id"]: i for i in cart.data["items"]}
                pid = args["product_id"]
                details = await self._read("get_product_details", {"product_id": pid}, user_id)
                if not details.ok:
                    return details.data
                p = details.data["product"]
                current_qty = in_cart.get(pid, {}).get("quantity", 0)
                if action == "add_to_cart":
                    qty = args.get("quantity", 1)
                    if p["stock_quantity"] <= 0:
                        return _err("out_of_stock", f"{p['name']} ({pid}) is out of stock. Suggest in-stock alternatives.",
                                    {"product_id": pid, "available_quantity": 0})
                    if current_qty + qty > p["stock_quantity"]:
                        return _err("insufficient_stock", f"Only {p['stock_quantity']} unit(s) of {p['name']} available "
                                    f"({current_qty} already in cart).", {"available_quantity": p["stock_quantity"]})
                    if current_qty + qty > 10:
                        return _err("quantity_limit", "At most 10 units of one product per cart.")
                    title = f"Add {qty} x {p['name']} to cart"
                    summary = {"title": title, "action_label": "Add to cart", "product_id": pid, "product_name": p["name"],
                               "quantity": qty, "quantity_already_in_cart": current_qty, "unit_price": p["price"],
                               "estimated_price": str(Decimal(p["price"]) * qty), "available_quantity": p["stock_quantity"]}
                elif action == "update_cart":
                    if pid not in in_cart:
                        return _err("not_in_cart", f"{p['name']} ({pid}) is not in the cart.")
                    qty = args["quantity"]
                    if qty > p["stock_quantity"]:
                        return _err("insufficient_stock", f"Only {p['stock_quantity']} unit(s) of {p['name']} available.",
                                    {"available_quantity": p["stock_quantity"]})
                    summary = {"title": f"Change {p['name']} quantity {current_qty} -> {qty}", "action_label": "Update quantity",
                               "product_id": pid, "product_name": p["name"], "previous_quantity": current_qty, "quantity": qty,
                               "unit_price": p["price"], "estimated_price": str(Decimal(p["price"]) * qty),
                               "available_quantity": p["stock_quantity"]}
                else:
                    if pid not in in_cart:
                        return _err("not_in_cart", f"{p['name']} ({pid}) is not in the cart.",
                                    {"cart_product_ids": list(in_cart)})
                    summary = {"title": f"Remove {p['name']} from cart", "action_label": "Remove from cart",
                               "product_id": pid, "product_name": p["name"], "quantity": current_qty,
                               "unit_price": p["price"], "estimated_price": str(Decimal(p["price"]) * current_qty),
                               "available_quantity": p["stock_quantity"]}
                return {"ok": True, "arguments": args, "summary": summary}

            if action == "place_order":
                prev = await self._read("checkout_preview", {}, user_id)
                if not prev.ok:
                    return prev.data
                d = prev.data
                if not d.get("can_checkout"):
                    return _err("cannot_checkout", "Checkout is not possible: " + "; ".join(d.get("problems", [])))
                summary = {"title": f"Place SIMULATED order for {_inr(d['estimated_total'])}", "action_label": "Place order",
                           "lines": d["lines"], "subtotal": d["subtotal"], "shipping": d["shipping"], "taxes": d["taxes"],
                           "discounts": d["discounts"], "estimated_total": d["estimated_total"], "note": d["note"],
                           "simulated": True}
                return {"ok": True, "arguments": {"expected_cart_hash": d["cart_hash"]}, "summary": summary}
        except MCPUnavailable as exc:
            return _err("mcp_unavailable", str(exc))
        return _err("unsupported_action", f"{action} does not support approvals.")

    async def execute_approved(self, approval: dict[str, Any], user_id: int) -> dict[str, Any]:
        """Run an approval the user just approved. Re-validates first."""
        action, args, approved_summary = approval["action_type"], approval["arguments"], approval["summary"]
        spec = self.registry.get(action)
        if spec is None:
            return _err("unknown_tool", "Unknown action.")

        # 1) Re-validate current state (price/stock may have changed).
        fresh = await self.prepare_action(action, dict(args), user_id)
        if not fresh["ok"]:
            self._mark(approval["approval_id"], "invalidated", fresh)
            return {**fresh, "approval_status": "invalidated"}
        if action == "place_order" and fresh["arguments"] != args:
            res = _err("cart_changed", "Your cart or prices changed since you reviewed the order. Please review again.",
                       {"new_summary": fresh["summary"]})
            self._mark(approval["approval_id"], "invalidated", res)
            return {**res, "approval_status": "invalidated"}
        if action in ("add_to_cart", "update_cart") and fresh["summary"].get("unit_price") != approved_summary.get("unit_price"):
            res = _err("price_changed", f"The price changed from {_inr(approved_summary.get('unit_price'))} to "
                       f"{_inr(fresh['summary'].get('unit_price'))}. Please confirm again.", {"new_summary": fresh["summary"]})
            self._mark(approval["approval_id"], "invalidated", res)
            return {**res, "approval_status": "invalidated"}

        # 2) Execute on the MCP server; it consumes the approval atomically.
        try:
            res = await self.mcp.call_tool(action, args, build_meta(user_id, approval["approval_id"]), idempotent=False,
                                           timeout=get_settings().tool_timeout_seconds)
        except MCPUnavailable as exc:
            out = _err("mcp_unavailable", str(exc))
            self._mark(approval["approval_id"], "failed", out)
            return {**out, "approval_status": "failed"}

        if res.ok:
            with session_scope() as db:
                approval_service.record_result(db, approval["approval_id"], res.data)
            return {**res.data, "approval_status": "executed", "trace": {"tool": action, "server": res.server,
                                                                          "duration_ms": res.duration_ms}}
        if res.outcome == "unknown":
            # Do NOT retry a write blindly. Leave status as-is for inspection.
            return {**res.data, "approval_status": "unknown",
                    "message": "The result is unknown. Check your cart before trying again."}
        self._mark(approval["approval_id"], "failed", res.data)
        return {**res.data, "approval_status": "failed"}

    @staticmethod
    def _mark(approval_id: str, status: str, result: dict[str, Any]) -> None:
        with session_scope() as db:
            approval_service.mark(db, approval_id, status, result, only_from=("approved", "pending"))

    @staticmethod
    def _limit(output: dict[str, Any]) -> dict[str, Any]:
        """Bound the size of what we send back to the model."""
        max_chars = get_settings().max_tool_result_chars
        text = json.dumps(output, default=str)
        if len(text) <= max_chars:
            return output
        trimmed = dict(output)
        for key in ("excluded", "sample_reviews", "assumptions"):
            trimmed.pop(key, None)
        if len(json.dumps(trimmed, default=str)) <= max_chars:
            trimmed["truncated"] = True
            return trimmed
        return {"ok": output.get("ok", True), "truncated": True,
                "partial_json": json.dumps(trimmed, default=str)[:max_chars],
                "note": "Result was too large and has been truncated. Narrow your query."}


# ---------------------------------------------------------------------------
# Human-readable summaries and UI payloads (no hidden reasoning, just facts)
# ---------------------------------------------------------------------------
def _sanitize(args: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in args.items():
        if k == "expected_cart_hash" and v:
            out[k] = str(v)[:8] + "..."
        else:
            out[k] = v
    return out


def summarize(tool: str, data: dict[str, Any]) -> str:
    if not data.get("ok", True):
        return "Failed: " + data.get("error", {}).get("message", "unknown error")
    if tool == "search_products":
        return f"Found {data['total_matches']} matching product(s); returned {data['returned']}."
    if tool == "rank_products":
        if data.get("no_match"):
            return f"No product meets all hard constraints ({data.get('excluded_count', 0)} excluded)."
        top = data["ranked"][0]
        return (f"Ranked {len(data['ranked'])} of {data['eligible_count']} eligible; "
                f"#1 {top['product_id']} ({top['total_score']}/100); {data['excluded_count']} excluded.")
    if tool == "compare_products":
        return f"Compared {len(data['products'])} products: " + ", ".join(p["product_id"] for p in data["products"])
    if tool == "get_product_details":
        p = data["product"]
        return f"{p['product_id']}: {p['name']} - {_inr(p['price'])}, stock {p['stock_quantity']}."
    if tool == "check_inventory":
        return f"{data['product_id']}: {data['available_quantity']} in stock."
    if tool == "get_product_reviews":
        return f"{len(data['reviews'])} sample review(s) for {data['product_id']}."
    if tool in ("get_cart",):
        return f"Cart: {data['item_count']} item(s), subtotal {_inr(data['subtotal'])}."
    if tool in ("calculate_cart_total", "checkout_preview"):
        return f"Estimated total {_inr(data['estimated_total'])} (shipping/taxes not calculated)."
    if tool in ("add_to_cart", "update_cart", "remove_from_cart"):
        return f"{tool.replace('_', ' ').capitalize()} succeeded for {data.get('product_id')}."
    if tool == "place_order":
        return f"Simulated order {data.get('order_id')} created ({_inr(data.get('total'))})."
    return "Done."


def ui_payload(tool: str, data: dict[str, Any]) -> dict[str, Any]:
    """Structured data the React app renders (cards, tables, cart)."""
    if not data.get("ok", True):
        return {}
    if tool == "search_products":
        return {"products": data["products"]}
    if tool == "get_product_details":
        return {"products": [data["product"]]}
    if tool == "rank_products":
        return {"ranking": {k: data.get(k) for k in ("ranked", "weights", "hard_constraints", "use_case",
                                                     "excluded_count", "eligible_count", "no_match", "message", "assumptions")}}
    if tool == "compare_products":
        return {"comparison": {"products": data["products"], "weights": data.get("weights")}}
    if tool in ("get_cart",):
        return {"cart": data}
    if tool in ("add_to_cart", "update_cart", "remove_from_cart") and "cart" in data:
        return {"cart": data["cart"]}
    return {}
