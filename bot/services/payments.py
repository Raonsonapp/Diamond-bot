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
    async def create_invoice(
        self, order_id: int, amount_somoni: float, bank_hint: str | None = None
    ) -> InvoiceResult:
        """Start a payment for an order and return how the customer should pay.

        bank_hint: which bank app the customer said they'd pay from (see
        keyboards.PAYMENT_METHODS) — purely cosmetic, since a card-to-card
        transfer reaches the same receiving card regardless of which bank's
        app sent it. None means no picker was shown (e.g. a cart-balance or
        legacy flow); a real provider is free to ignore it entirely.
        """

    @abstractmethod
    def verify_callback(self, payload: dict, headers: dict) -> tuple[bool, str | None]:
        """Validate an inbound payment-gateway webhook.

        Returns (is_valid, provider_reference_if_paid).
        """


def _build_alif_link(amount_somoni: float) -> str | None:
    """Deep link straight into the customer's own Alif Mobi app, landing on
    its "DC кошелек" top-up provider with the amount pre-filled — captured
    from a real link the admin generated in their own Alif app
    (https://alifmobi.page.link/providers?id=124&amount=X&account=Y). Only
    makes sense for a customer who already has the Alif app installed,
    which is exactly the "🏦 Алиф Мобайл" button in the bank picker."""
    if not config.alif_dc_receiving_account:
        return None
    return (
        f"https://alifmobi.page.link/providers?id={config.alif_dc_provider_id}"
        f"&amount={amount_somoni:.2f}&account={config.alif_dc_receiving_account}"
    )


_BANK_NAMES = {
    "dc": "Душанбе Сити",
    "amonat": "Амонатбонк",
    "alif": "Алиф Мобайл",
}


class ManualBankTransferProvider(PaymentProvider):
    """Works today with zero external accounts: customer transfers by card
    and sends proof; admin taps Confirm in the bot."""

    async def create_invoice(
        self, order_id: int, amount_somoni: float, bank_hint: str | None = None
    ) -> InvoiceResult:
        # Alif gets its own app deep link (opens straight into Alif Mobi
        # with the amount pre-filled) — a real link the admin captured from
        # their own app, see _build_alif_link. Every other bank falls back
        # to the plain card-number instructions below (card_line) — there
        # used to be a generic "ExpressPay" pay-by-link fallback here too,
        # but that domain (pay.expresspay.tj) was never a confirmed real
        # ExpressPay integration, just reverse-engineered from a similar
        # shop's bot, and a real customer's screenshot proved it doesn't
        # even resolve (DNS_PROBE_FINISHED_NXDOMAIN) — removed rather than
        # keep sending customers a dead payment link.
        alif_url = _build_alif_link(amount_somoni) if bank_hint == "alif" else None
        pay_url = alif_url

        if not config.receiving_card_number:
            card_line = "⚠️ Рақами корти қабулкунанда танзим нашудааст — бо админ тамос гиред.\n"
        else:
            card_line = f"💳 Корт: {config.receiving_card_number}\n"
            # Verified real path (confirmed against the admin's own
            # screenshot of their bank app): most Tajik banking apps have a
            # "DC кошелек" top-up option under Платежи → Эл. кошельки,
            # reachable by phone number — same alif_dc_receiving_account
            # used for the Alif deep link, since it's the same DC wallet
            # either way. Not needed when Alif's own deep link is doing the
            # work instead.
            bank_name = _BANK_NAMES.get(bank_hint or "")
            if bank_name and bank_hint != "dc" and not alif_url and config.alif_dc_receiving_account:
                card_line += (
                    f"(Ё аз барномаи мобилии {bank_name}-и худ: Платежи → Эл. кошельки → "
                    f"DC кошелек → рақами телефон {config.alif_dc_receiving_account} → "
                    f"маблағ {amount_somoni:.2f} сомонӣ → Оплатить.)\n"
                )

        # A real underpaid order (#49: 5с sent instead of 5.90с) showed the
        # old, softer "(на кам, на зиёд)" parenthetical wasn't prominent
        # enough — this line is now its own bold, always-shown warning on
        # every path (it was missing entirely from the Alif and no-pay_url
        # branches before).
        exact_amount_warning = (
            f"⚠️ <b>ДИҚҚАТ: маблағро АЙНАН {amount_somoni:.2f} сомонӣ фиристед — на 1 дирам кам, на зиёд!</b> "
            f"Агар камтар фиристед, фармоиш худкор тасдиқ намешавад."
        )

        if alif_url:
            instructions = (
                f"💰 Маблағи дақиқ: {amount_somoni:.2f} сомонӣ\n\n"
                f"Тугмаи «💳 Пардохт»-ро пахш кунед — мустақим ба барномаи Алиф-и шумо мекушояд, "
                f"бо маблағи аллакай пуркардашуда. Танҳо тасдиқро занед, баъд расиди пардохтро "
                f"(скриншот) ба ин ҷо фиристед.\n\n{exact_amount_warning}"
            )
        else:
            instructions = (
                f"{card_line}"
                f"Лутфан {amount_somoni:.2f} сомонӣ гузаронед ва расиди пардохтро "
                f"(скриншот) ба ин ҷо фиристед. Пас аз тасдиқи админ фармоишатон иҷро мешавад."
                f"\n\n{exact_amount_warning}"
            )

        return InvoiceResult(provider_reference=f"manual-{order_id}", pay_url=pay_url, instructions=instructions)

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

    async def create_invoice(
        self, order_id: int, amount_somoni: float, bank_hint: str | None = None
    ) -> InvoiceResult:
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


class DCBankProvider(PaymentProvider):
    """Scaffold only, same reasoning as AlifPayProvider. Dushanbe City Bank
    (dc.tj) does run merchant/internet-acquiring services ("Express Pay"),
    but — like Alif — doesn't publish a public generic API spec. Contact
    them directly as a registered business to get a Shop ID, Secret Key,
    and their real endpoint/callback signature documentation, then fill in
    `create_invoice` and `verify_callback` below before switching
    PAYMENT_PROVIDER=dc in production."""

    def __init__(self) -> None:
        if not (config.dc_shop_id and config.dc_secret_key and config.dc_api_base_url):
            raise RuntimeError(
                "PAYMENT_PROVIDER=dc is set but DC_SHOP_ID / DC_SECRET_KEY / "
                "DC_API_BASE_URL are empty. Get these from Dushanbe City Bank as a "
                "registered merchant first — see bot/services/payments.py docstring."
            )
        self.shop_id = config.dc_shop_id
        self.secret_key = config.dc_secret_key
        self.api_base_url = config.dc_api_base_url

    async def create_invoice(
        self, order_id: int, amount_somoni: float, bank_hint: str | None = None
    ) -> InvoiceResult:
        raise NotImplementedError(
            "Wire this up to DC Bank's real 'create invoice' endpoint once you have "
            "their merchant API doc. Placeholder to prevent silently taking payments "
            "without a real gateway behind it."
        )

    def verify_callback(self, payload: dict, headers: dict) -> tuple[bool, str | None]:
        # Placeholder HMAC check — replace with DC Bank's actual signature scheme.
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
    if config.payment_provider == "dc":
        return DCBankProvider()
    return ManualBankTransferProvider()
