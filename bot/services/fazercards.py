"""Thin async client for the FazerCards reseller API (api.fzr.cards).

Docs (admin-provided): https://api.fzr.cards/public/docs/openapi.json
Auth: X-API-Key header. Every response is JSON shaped either
{"ok": true, ...} or {"ok": false, "error": "...", "code": "..."} —
regardless of HTTP status, so callers just check FazerCardsError.
"""

from __future__ import annotations

import aiohttp

from bot.config import config


class FazerCardsError(Exception):
    def __init__(self, message: str, code: str | None = None, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status


async def _request(method: str, path: str, **kwargs) -> dict:
    if not config.fazercards_api_key:
        raise FazerCardsError("FAZERCARDS_API_KEY is not set")

    url = config.fazercards_api_base_url.rstrip("/") + path
    headers = kwargs.pop("headers", {}) or {}
    headers["X-API-Key"] = config.fazercards_api_key
    headers.setdefault("Accept", "application/json")

    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.request(method, url, headers=headers, **kwargs) as resp:
            try:
                data = await resp.json(content_type=None)
            except Exception:
                text = await resp.text()
                raise FazerCardsError(
                    f"Non-JSON response (HTTP {resp.status}): {text[:200]}", status=resp.status
                )

    if not isinstance(data, dict) or not data.get("ok", False):
        error = (data or {}).get("error", "Unknown error") if isinstance(data, dict) else "Unknown error"
        code = (data or {}).get("code") if isinstance(data, dict) else None
        raise FazerCardsError(error, code=code)
    return data


async def list_topup_categories(limit: int = 200, cursor: str | None = None) -> dict:
    params = {"limit": str(limit)}
    if cursor:
        params["cursor"] = cursor
    return await _request("GET", "/api/v2/topups", params=params)


async def get_topup_offers(category_id: str) -> dict:
    return await _request("GET", "/api/v2/topups/offers", params={"category_id": category_id})


async def list_validate_id_categories() -> dict:
    return await _request("GET", "/api/v2/topups/validate-id")


async def validate_player_id(category_id: str, fields: dict) -> dict:
    return await _request(
        "POST",
        "/api/v2/topups/validate-id",
        json={"category_id": category_id, "fields": fields},
    )


async def place_topup_order(
    category_id: str, offer_id: str, fields: dict, idempotency_key: str | None = None
) -> dict:
    headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
    return await _request(
        "POST",
        "/api/v2/topups/order",
        json={"category_id": category_id, "offer_id": offer_id, "fields": fields},
        headers=headers,
    )
