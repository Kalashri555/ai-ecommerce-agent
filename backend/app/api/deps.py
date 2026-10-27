"""Shared API dependencies: app services and a simple in-memory rate limiter."""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request

from app.auth.dependencies import CurrentUser, get_current_user
from app.config import get_settings


def get_agent(request: Request):
    return request.app.state.agent


def get_executor(request: Request):
    return request.app.state.executor


class RateLimiter:
    """Sliding-window limiter per user (single-process demo; use Redis in production)."""

    def __init__(self) -> None:
        self._hits: dict[int, deque[float]] = defaultdict(deque)

    def check(self, user_id: int, limit: int, window: float = 60.0) -> None:
        now = time.monotonic()
        q = self._hits[user_id]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            raise HTTPException(status_code=429, detail={"code": "rate_limited",
                                "message": f"Too many requests. Limit is {limit} per minute."})
        q.append(now)


chat_limiter = RateLimiter()


def chat_rate_limit(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    chat_limiter.check(user.id, get_settings().chat_rate_limit_per_minute)
    return user
