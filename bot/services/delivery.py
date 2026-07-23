"""Diamond delivery provider abstraction.

"manual" is the only provider that actually delivers diamonds today: the
admin tops up the player's Free Fire ID by hand (via the official app,
site, or whatever supplier they personally use), then taps "Delivered" in
the bot, which notifies the customer.

There is no generic public API that credits Free Fire diamonds — that
capability only exists behind whichever specific reseller/supplier
account you sign up with. `AutoDeliveryProvider` is a scaffold: point
`deliver()` at that supplier's real endpoint once you have one, and the
rest of the bot (order flow, payment, admin panel) does not need to
change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from bot.config import config


@dataclass
class DeliveryResult:
    success: bool
    reference: str | None
    message: str


class DeliveryProvider(ABC):
    @abstractmethod
    async def deliver(self, order_id: int, ff_player_id: str, diamonds: int) -> DeliveryResult:
        ...


class ManualDeliveryProvider(DeliveryProvider):
    async def deliver(self, order_id: int, ff_player_id: str, diamonds: int) -> DeliveryResult:
        # No automatic action possible; admin fulfills by hand and marks the
        # order as delivered via the admin panel (see bot/handlers/admin.py).
        return DeliveryResult(
            success=False,
            reference=None,
            message="Manual delivery required — waiting for admin to fulfill and confirm.",
        )


class AutoDeliveryProvider(DeliveryProvider):
    """Scaffold only. Replace the body of `deliver` with a real call to your
    reseller/supplier's top-up API once you have one — see module docstring."""

    def __init__(self) -> None:
        if not (config.supplier_api_base_url and config.supplier_api_key):
            raise RuntimeError(
                "DELIVERY_PROVIDER=auto is set but SUPPLIER_API_BASE_URL / "
                "SUPPLIER_API_KEY are empty. You need a real diamond supplier/reseller "
                "account with a programmatic top-up API first — see "
                "bot/services/delivery.py docstring."
            )

    async def deliver(self, order_id: int, ff_player_id: str, diamonds: int) -> DeliveryResult:
        raise NotImplementedError(
            "Call your supplier's real top-up API here, e.g.:\n"
            "  POST {SUPPLIER_API_BASE_URL}/topup {player_id, diamonds}\n"
            "and map their response to DeliveryResult. Left unimplemented on purpose "
            "so orders fail loudly instead of silently pretending to deliver."
        )


def get_delivery_provider() -> DeliveryProvider:
    if config.delivery_provider == "auto":
        return AutoDeliveryProvider()
    return ManualDeliveryProvider()
