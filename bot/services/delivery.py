"""Diamond/Stars delivery provider abstraction.

"manual" is the baseline that always works: the admin tops up the player
by hand (via the official app, site, or whatever supplier they personally
use), then taps "Delivered" in the bot, which notifies the customer.

"fazercards" calls the real FazerCards reseller API (api.fzr.cards) for
products that have been mapped to a real category/offer via
/mapproduct — see bot/services/fazercards.py. Unmapped products (e.g. the
"custom amount" quotes, which don't correspond to any fixed FazerCards
offer) fall back to manual delivery automatically.
"""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass

from bot.config import config
from bot.db.models import ProductCategory

_ID_FIELD_HINTS = ("player", "user", "uid", "account", "id")
# Confirmed live: a real order goes created -> processing -> completed,
# all within the same few seconds. "completed" is the real terminal
# success status FazerCards uses; kept as a set in case other categories
# report an equivalent synonym.
_CONFIRMED_STATUSES = {"completed", "success", "delivered", "done", "fulfilled", "successful"}
# Real observed case: order #52 (110 Diamonds) came back with
# status="refund" — FazerCards actively rejected and refunded it (wrong
# player ID, insufficient FazerCards balance, out of stock, etc.), which is
# a fundamentally different, more urgent situation than "still processing"
# — it will NEVER become "completed" no matter how long polling continues,
# so it belongs in the terminal-failure set, not left to exhaust every
# retry first.
_FAILED_STATUSES = {"failed", "cancelled", "canceled", "error", "rejected", "declined", "refund", "refunded"}
# How long to wait for "created" to become "completed" before giving up
# and falling back to manual. Most orders confirm within ~1 second, but a
# real order (ord-510807, a Weekly Membership) was observed to still be
# "processing" after the original ~12s window and only show completed on
# FazerCards' own dashboard some time later — so this is generous on
# purpose, not a tight timing bet. Bumped from ~30s to a full minute after
# repeatedly seeing genuinely-still-processing orders outlast the shorter
# window. bot/services/fulfillment.py also does a few longer-spaced
# background rechecks after this window closes, for orders that take even
# longer than this.
_POLL_ATTEMPTS = 10
_POLL_DELAY_SECONDS = 6


@dataclass
class DeliveryResult:
    success: bool
    reference: str | None
    message: str


class DeliveryProvider(ABC):
    @abstractmethod
    async def deliver(self, order_id: int, ff_player_id: str, product) -> DeliveryResult:
        ...


class ManualDeliveryProvider(DeliveryProvider):
    async def deliver(self, order_id: int, ff_player_id: str, product) -> DeliveryResult:
        # No automatic action possible; admin fulfills by hand and marks the
        # order as delivered via the admin panel (see bot/handlers/admin.py).
        return DeliveryResult(
            success=False,
            reference=None,
            message="Manual delivery required — waiting for admin to fulfill and confirm.",
        )


def guess_id_field_key(fields: list[dict]) -> str | None:
    """The order-fields schema FazerCards returns for a category is
    dynamic (whatever that game requires) — usually just one field (the
    player ID). If there's exactly one, use it; otherwise look for a key
    or label that plausibly means "player id". Never guess among several
    equally-plausible fields — better to fall back to manual than submit
    a real paid order with the wrong field filled in."""
    if not fields:
        return None
    if len(fields) == 1:
        return fields[0].get("key")

    matches = [
        f.get("key")
        for f in fields
        if any(
            hint in (f.get("key") or "").lower() or hint in (f.get("label") or "").lower()
            for hint in _ID_FIELD_HINTS
        )
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def _extract_reference(order_info: dict) -> str | None:
    for key in ("id", "order_id", "tx_id", "transaction_id"):
        value = order_info.get(key)
        if value:
            return str(value)
    if order_info:
        return json.dumps(order_info)[:128]
    return None


class FazerCardsDeliveryProvider(DeliveryProvider):
    """Real integration against api.fzr.cards. Only fires for products with
    fzr_category_id/fzr_offer_id set (see /mapproduct admin command)."""

    def __init__(self) -> None:
        if not config.fazercards_api_key:
            raise RuntimeError(
                "DELIVERY_PROVIDER=fazercards is set but FAZERCARDS_API_KEY is empty."
            )

    async def deliver(self, order_id: int, ff_player_id: str, product) -> DeliveryResult:
        if product.category == ProductCategory.TELEGRAM and product.telegram_kind:
            return await self._deliver_telegram(order_id, ff_player_id, product)

        if not (product.fzr_category_id and product.fzr_offer_id):
            return DeliveryResult(
                success=False,
                reference=None,
                message=f"Product #{product.id} isn't mapped to a FazerCards offer yet — use /mapproduct.",
            )

        from bot.services.fazercards import FazerCardsError, get_topup_offers, place_topup_order

        try:
            offers_data = await get_topup_offers(product.fzr_category_id)
        except FazerCardsError as exc:
            return DeliveryResult(success=False, reference=None, message=f"FazerCards offers error: {exc}")

        field_key = guess_id_field_key(offers_data.get("fields", []))
        if field_key is None:
            return DeliveryResult(
                success=False,
                reference=None,
                message=f"Could not determine the player-ID field from schema: {offers_data.get('fields')}",
            )

        idempotency_key = f"diamondbot-order-{order_id}"
        try:
            result = await place_topup_order(
                product.fzr_category_id,
                product.fzr_offer_id,
                {field_key: ff_player_id},
                idempotency_key=idempotency_key,
            )
        except FazerCardsError as exc:
            return DeliveryResult(
                success=False, reference=None, message=f"FazerCards order error [{exc.code}]: {exc}"
            )

        order_info = result.get("order", {}) if isinstance(result, dict) else {}
        reference = _extract_reference(order_info) or f"fzr-order-{order_id}"
        status = str(order_info.get("status", "")).lower()

        # A fresh order starts at "created" and FazerCards processes it
        # asynchronously (created -> processing -> completed, observed to
        # take only a few seconds) — check once immediately, then poll a
        # few times before giving up. Re-sending the *same* Idempotency-Key*
        # is assumed to return the current state of that same order rather
        # than creating a duplicate; if that assumption is wrong for some
        # category, the retry just raises FazerCardsError like any other
        # failed call and polling stops there, falling through to the safe
        # "not confirmed" report below — never a false "delivered".
        attempts = 0
        while (
            status not in _CONFIRMED_STATUSES
            and status not in _FAILED_STATUSES
            and attempts < _POLL_ATTEMPTS
        ):
            await asyncio.sleep(_POLL_DELAY_SECONDS)
            try:
                result = await place_topup_order(
                    product.fzr_category_id,
                    product.fzr_offer_id,
                    {field_key: ff_player_id},
                    idempotency_key=idempotency_key,
                )
            except FazerCardsError:
                break
            order_info = result.get("order", {}) if isinstance(result, dict) else {}
            reference = _extract_reference(order_info) or reference
            status = str(order_info.get("status", "")).lower()
            attempts += 1

        if status in _CONFIRMED_STATUSES:
            return DeliveryResult(success=True, reference=reference, message="Delivered via FazerCards API.")

        # {"ok": true} on /topups/order only means FazerCards *accepted*
        # the order into their queue — not that the diamonds actually
        # reached the player. Treating that alone as "delivered" caused
        # the bot to tell customers diamonds arrived when they hadn't.
        # Anything short of a recognised completed-style status (including
        # an unfamiliar status field) falls back to the admin's manual
        # "Delivered" confirmation instead of guessing.
        if status in _FAILED_STATUSES:
            message = (
                f"⛔ FazerCards РАДД КАРД ин фармоишро (status={status}) — эҳтимол ID-и бозингар "
                f"нодуруст аст ё балансатони FazerCards кам аст. Санҷед пеш аз таҳвили дастӣ! "
                f"Raw order info: {json.dumps(order_info)[:300]}"
            )
        else:
            message = (
                f"FazerCards accepted the order but hasn't confirmed delivery "
                f"(status={status or '(none)'}). Raw order info: {json.dumps(order_info)[:300]}"
            )
        return DeliveryResult(success=False, reference=reference, message=message)

    async def _deliver_telegram(self, order_id: int, recipient: str, product) -> DeliveryResult:
        """Telegram Stars/Premium via FazerCards' dedicated API — found
        through /fzr_docs_search since it isn't part of the game topups
        catalog at all (no category_id/offer_id, no fields schema). The
        exact response shape for POST .../buy hasn't been confirmed against
        a real order yet, so this is deliberately conservative: only an
        explicit recognised "completed"-style status counts as delivered,
        anything else (including an unfamiliar shape) safely falls back to
        manual review — same rule as the game topups path above, for the
        same reason (this debits the FazerCards balance immediately
        regardless of what we detect, so never guess "delivered")."""
        from bot.services.fazercards import FazerCardsError, buy_telegram_premium, buy_telegram_stars

        username = recipient.lstrip("@")
        idempotency_key = f"diamondbot-order-{order_id}"

        async def _place() -> dict:
            if product.telegram_kind == "stars":
                return await buy_telegram_stars(username, product.diamonds, idempotency_key=idempotency_key)
            return await buy_telegram_premium(username, product.diamonds, idempotency_key=idempotency_key)

        try:
            result = await _place()
        except FazerCardsError as exc:
            return DeliveryResult(
                success=False, reference=None, message=f"FazerCards Telegram order error [{exc.code}]: {exc}"
            )

        payload = result.get("order", result) if isinstance(result, dict) else {}
        reference = _extract_reference(payload) or f"fzr-tg-order-{order_id}"
        status = str(payload.get("status", "")).lower()

        attempts = 0
        while (
            status not in _CONFIRMED_STATUSES
            and status not in _FAILED_STATUSES
            and attempts < _POLL_ATTEMPTS
        ):
            await asyncio.sleep(_POLL_DELAY_SECONDS)
            try:
                result = await _place()
            except FazerCardsError:
                break
            payload = result.get("order", result) if isinstance(result, dict) else {}
            reference = _extract_reference(payload) or reference
            status = str(payload.get("status", "")).lower()
            attempts += 1

        if status in _CONFIRMED_STATUSES:
            return DeliveryResult(success=True, reference=reference, message="Delivered via FazerCards Telegram API.")

        if status in _FAILED_STATUSES:
            message = (
                f"⛔ FazerCards РАДД КАРД ин фармоишро (status={status}) — эҳтимол username нодуруст "
                f"аст ё балансатони FazerCards кам аст. Санҷед пеш аз таҳвили дастӣ! "
                f"Raw response: {json.dumps(payload)[:300]}"
            )
        else:
            message = (
                f"FazerCards accepted the Telegram order but hasn't confirmed delivery "
                f"(status={status or '(none)'}). Raw response: {json.dumps(payload)[:300]}"
            )
        return DeliveryResult(success=False, reference=reference, message=message)


class AutoDeliveryProvider(DeliveryProvider):
    """Generic scaffold for any other supplier's API — kept for suppliers
    besides FazerCards. Replace the body of `deliver` with a real call
    once you have credentials from that supplier."""

    def __init__(self) -> None:
        if not (config.supplier_api_base_url and config.supplier_api_key):
            raise RuntimeError(
                "DELIVERY_PROVIDER=auto is set but SUPPLIER_API_BASE_URL / "
                "SUPPLIER_API_KEY are empty. You need a real diamond supplier/reseller "
                "account with a programmatic top-up API first — see "
                "bot/services/delivery.py docstring."
            )

    async def deliver(self, order_id: int, ff_player_id: str, product) -> DeliveryResult:
        raise NotImplementedError(
            "Call your supplier's real top-up API here and map their response to "
            "DeliveryResult. Left unimplemented on purpose so orders fail loudly "
            "instead of silently pretending to deliver."
        )


def get_delivery_provider() -> DeliveryProvider:
    if config.delivery_provider == "fazercards":
        return FazerCardsDeliveryProvider()
    if config.delivery_provider == "auto":
        return AutoDeliveryProvider()
    return ManualDeliveryProvider()
