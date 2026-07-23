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
    alif_shop_id: str = os.getenv("ALIF_SHOP_ID", "")
    alif_secret_key: str = os.getenv("ALIF_SECRET_KEY", "")
    alif_api_base_url: str = os.getenv("ALIF_API_BASE_URL", "")
    alif_callback_path: str = os.getenv("ALIF_CALLBACK_PATH", "/webhooks/alif")
    webhook_server_host: str = os.getenv("WEBHOOK_SERVER_HOST", "0.0.0.0")
    webhook_server_port: int = int(os.getenv("WEBHOOK_SERVER_PORT", "8080"))

    delivery_provider: str = os.getenv("DELIVERY_PROVIDER", "manual")
    supplier_api_base_url: str = os.getenv("SUPPLIER_API_BASE_URL", "")
    supplier_api_key: str = os.getenv("SUPPLIER_API_KEY", "")

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
