"""Payment provider abstraction.

Only "manual" is wired to a real, working flow: the customer sends a
transfer receipt (screenshot or text) in the bot, and an admin confirms it
with one tap. That is enough to launch and take real orders immediately.

`AlifPayProvider` is a scaffold, not a working integration. Alif Business
does not publish a public generic REST spec — the real Shop ID, Secret
Key, endpoint URLs and signature scheme are handed to you directly by
Alif after you sign a merchant agreement. Fill in `_create_invoice` and
`verify_callback` from that document before switching
PAYMENT_PROVIDER=alif in production. Shipping this half-finished would
silently take customers' money without confirming it actually arrived.
"""

from __future__ import annotations

import hashlib
import hmac
from abc import ABC, abstractmethod
from dataclasses import dataclass

from bot.config import config


@dataclass
class InvoiceResult:
    provider_reference: str
    pay_url: str | None  # link to send the customer to, if any
    instructions: str  # what to show the customer in the bot


class PaymentProvider(ABC):
    @abstractmethod
    async def create_invoice(self, order_id: int, amount_somoni: float) -> InvoiceResult:
        """Start a payment for an order and return how the customer should pay."""

    @abstractmethod
    def verify_callback(self, payload: dict, headers: dict) -> tuple[bool, str | None]:
        """Validate an inbound payment-gateway webhook.

        Returns (is_valid, provider_reference_if_paid).
        """


class ManualBankTransferProvider(PaymentProvider):
    """Works today with zero external accounts: customer transfers by card
    and sends proof; admin taps Confirm in the bot."""

    async def create_invoice(self, order_id: int, amount_somoni: float) -> InvoiceResult:
        return InvoiceResult(
            provider_reference=f"manual-{order_id}",
            pay_url=None,
            instructions=(
                f"Лутфан {amount_somoni:.0f} сомонӣ ба корти дар боло зикршуда гузаронед "
                f"ва расиди пардохтро (скриншот) ба ин ҷо фиристед. Пас аз тасдиқи админ "
                f"фармоишатон иҷро мешавад."
            ),
        )

    def verify_callback(self, payload: dict, headers: dict) -> tuple[bool, str | None]:
        # Manual provider has no webhook; confirmation happens via admin button.
        return False, None


class AlifPayProvider(PaymentProvider):
    """Scaffold only — see module docstring. Do not enable without real
    credentials and the real callback signature scheme from Alif Business."""

    def __init__(self) -> None:
        if not (config.alif_shop_id and config.alif_secret_key and config.alif_api_base_url):
            raise RuntimeError(
                "PAYMENT_PROVIDER=alif is set but ALIF_SHOP_ID / ALIF_SECRET_KEY / "
                "ALIF_API_BASE_URL are empty. Get these from your Alif Business merchant "
                "agreement first — see bot/services/payments.py docstring."
            )
        self.shop_id = config.alif_shop_id
        self.secret_key = config.alif_secret_key
        self.api_base_url = config.alif_api_base_url

    async def create_invoice(self, order_id: int, amount_somoni: float) -> InvoiceResult:
        raise NotImplementedError(
            "Wire this up to Alif's real 'create invoice' endpoint once you have "
            "their merchant API doc. Placeholder to prevent silently taking payments "
            "without a real gateway behind it."
        )

    def verify_callback(self, payload: dict, headers: dict) -> tuple[bool, str | None]:
        # Placeholder HMAC check — replace with Alif's actual signature scheme.
        signature = headers.get("X-Signature", "")
        expected = hmac.new(
            self.secret_key.encode(), str(payload).encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return False, None
        if payload.get("status") == "paid":
            return True, str(payload.get("transaction_id"))
        return False, None


def get_payment_provider() -> PaymentProvider:
    if config.payment_provider == "alif":
        return AlifPayProvider()
    return ManualBankTransferProvider()
