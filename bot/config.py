import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _int_list(raw: str) -> list[int]:
    return [int(x) for x in raw.split(",") if x.strip()]


@dataclass(frozen=True)
class Config:
    bot_token: str = os.getenv("BOT_TOKEN", "")
    admin_chat_id: int = int(os.getenv("ADMIN_CHAT_ID", "0") or "0")
    admin_user_ids: list[int] = field(
        default_factory=lambda: _int_list(os.getenv("ADMIN_USER_IDS", ""))
    )

    database_path: str = os.getenv("DATABASE_PATH", "./diamond_bot.db")

    # Public URL Render (or any other host) gives your service, e.g.
    # https://diamond-bot-qakk.onrender.com — leave empty to run in local
    # polling mode instead (see main.py).
    public_url: str = os.getenv("PUBLIC_URL", "")
    telegram_webhook_path: str = os.getenv("TELEGRAM_WEBHOOK_PATH", "/tg-webhook")
    telegram_webhook_secret: str = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    # Render sets $PORT itself at runtime; 8080 is just a local fallback.
    port: int = int(os.getenv("PORT", "8080"))

    payment_provider: str = os.getenv("PAYMENT_PROVIDER", "manual")
    # Your own card that customers pay into — shown as plain text and (if
    # set) used to build a pre-filled ExpressPay pay-by-link so the
    # customer doesn't have to type the card number/amount by hand.
    receiving_card_number: str = os.getenv("RECEIVING_CARD_NUMBER", "")
    # Plain http:// on purpose — pay.expresspay.tj's TLS cert doesn't match
    # this hostname (ERR_CERT_COMMON_NAME_INVALID over https); the reference
    # bot's real working link used http:// too.
    expresspay_base_url: str = os.getenv("EXPRESSPAY_BASE_URL", "http://pay.expresspay.tj/")
    alif_shop_id: str = os.getenv("ALIF_SHOP_ID", "")
    alif_secret_key: str = os.getenv("ALIF_SECRET_KEY", "")
    alif_api_base_url: str = os.getenv("ALIF_API_BASE_URL", "")
    alif_callback_path: str = os.getenv("ALIF_CALLBACK_PATH", "/webhooks/alif")
    dc_shop_id: str = os.getenv("DC_SHOP_ID", "")
    dc_secret_key: str = os.getenv("DC_SECRET_KEY", "")
    dc_api_base_url: str = os.getenv("DC_API_BASE_URL", "")
    dc_callback_path: str = os.getenv("DC_CALLBACK_PATH", "/webhooks/dc")
    webhook_server_host: str = os.getenv("WEBHOOK_SERVER_HOST", "0.0.0.0")
    webhook_server_port: int = int(os.getenv("WEBHOOK_SERVER_PORT", "8080"))

    delivery_provider: str = os.getenv("DELIVERY_PROVIDER", "manual")
    supplier_api_base_url: str = os.getenv("SUPPLIER_API_BASE_URL", "")
    supplier_api_key: str = os.getenv("SUPPLIER_API_KEY", "")

    # FazerCards reseller API (https://api.fzr.cards) — set DELIVERY_PROVIDER=fazercards
    # once products are mapped to real category/offer IDs (see /fzr_categories,
    # /fzr_offers, /mapproduct admin commands).
    fazercards_api_key: str = os.getenv("FAZERCARDS_API_KEY", "")
    fazercards_api_base_url: str = os.getenv("FAZERCARDS_API_BASE_URL", "https://api.fzr.cards")

    # SMS-based auto payment confirmation: an app on the admin's phone
    # forwards incoming "Zachislenie" (deposit) SMS from the bank to this
    # webhook. Empty secret disables the endpoint entirely.
    sms_webhook_path: str = os.getenv("SMS_WEBHOOK_PATH", "/sms-webhook")
    sms_webhook_secret: str = os.getenv("SMS_WEBHOOK_SECRET", "")
    sms_match_window_minutes: int = int(os.getenv("SMS_MATCH_WINDOW_MINUTES", "60"))

    # Public shop channel: bot must be added there as admin with "Post
    # Messages" permission, otherwise announcements silently fail.
    shop_channel_url: str = os.getenv("SHOP_CHANNEL_URL", "https://t.me/ALMAZ_TJ_SHOP")
    review_channel_id: str = os.getenv("REVIEW_CHANNEL_ID", "@ALMAZ_TJ_SHOP")

    # wa.me link opens WhatsApp directly to a chat with this number —
    # digits only (country code + number, no "+", spaces or leading zeros).
    contact_whatsapp_url: str = os.getenv("CONTACT_WHATSAPP_URL", "https://wa.me/992971769009")
    contact_instagram_url: str = os.getenv(
        "CONTACT_INSTAGRAM_URL", "https://www.instagram.com/ff.a1maz?igsh=aGxyNzFtaWtnNjht"
    )


config = Config()
