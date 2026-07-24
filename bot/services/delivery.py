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

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass

from bot.config import config

_ID_FIELD_HINTS = ("player", "user", "uid", "account", "id")


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

        try:
            result = await place_topup_order(
                product.fzr_category_id,
                product.fzr_offer_id,
                {field_key: ff_player_id},
                idempotency_key=f"diamondbot-order-{order_id}",
            )
        except FazerCardsError as exc:
            return DeliveryResult(
                success=False, reference=None, message=f"FazerCards order error [{exc.code}]: {exc}"
            )

        reference = _extract_reference(result.get("order", {})) or f"fzr-order-{order_id}"
        return DeliveryResult(success=True, reference=reference, message="Delivered via FazerCards API.")


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
