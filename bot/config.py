import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _int_list(raw: str) -> list[int]:
    return [int(x) for x in raw.split(",") if x.strip()]


def _normalize_database_url(raw: str) -> str:
    """Supabase (and most providers) hand out a plain "postgresql://" or
    "postgres://" URI meant for psycopg2/libpq — SQLAlchemy needs the
    "+asyncpg" driver suffix to use it from async code, and psycopg2 isn't
    even installed here. Rewrite the scheme automatically instead of
    relying on a manual find-replace step that's easy to skip and fails
    with a confusing "No module named 'psycopg2'" traceback instead of a
    clear error."""
    if not raw:
        return raw
    if raw.startswith("postgres://"):
        return "postgresql+asyncpg://" + raw[len("postgres://"):]
    if raw.startswith("postgresql://"):
        return "postgresql+asyncpg://" + raw[len("postgresql://"):]
    return raw


@dataclass(frozen=True)
class Config:
    bot_token: str = os.getenv("BOT_TOKEN", "")
    admin_chat_id: int = int(os.getenv("ADMIN_CHAT_ID", "0") or "0")
    admin_user_ids: list[int] = field(
        default_factory=lambda: _int_list(os.getenv("ADMIN_USER_IDS", ""))
    )

    database_path: str = os.getenv("DATABASE_PATH", "./diamond_bot.db")
    # A real Postgres URL (e.g. from Supabase) takes over from the local
    # SQLite file when set — SQLite alone is fine for local development,
    # but on Render's free tier the disk is wiped on every deploy, taking
    # every user/order/product edit with it. See README for the Supabase
    # setup steps.
    database_url: str = field(
        default_factory=lambda: _normalize_database_url(os.getenv("DATABASE_URL", ""))
    )

    # Public URL Render (or any other host) gives your service, e.g.
    # https://diamond-bot-qakk.onrender.com — leave empty to run in local
    # polling mode instead (see main.py).
    public_url: str = os.getenv("PUBLIC_URL", "")
    telegram_webhook_path: str = os.getenv("TELEGRAM_WEBHOOK_PATH", "/tg-webhook")
    telegram_webhook_secret: str = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    # Render sets $PORT itself at runtime; 8080 is just a local fallback.
    port: int = int(os.getenv("PORT", "8080"))

    payment_provider: str = os.getenv("PAYMENT_PROVIDER", "manual")
    # Your own card that customers pay into — shown as plain text so the
    # customer can transfer to it from any banking app.
    receiving_card_number: str = os.getenv("RECEIVING_CARD_NUMBER", "")
    # NOTE: an earlier version of this bot also built a pre-filled
    # "pay.expresspay.tj" link (reverse-engineered from a similar shop's
    # bot, never actually confirmed against ExpressPay's own docs). A real
    # customer's screenshot showed that domain doesn't even resolve
    # (DNS_PROBE_FINISHED_NXDOMAIN) — it was sending everyone who picked
    # "dc" or "amonat" a dead link. Removed; see bot/services/payments.py.
    #
    # pay.dc.tj is a DIFFERENT, separately-verified link — the admin
    # personally opened https://pay.dc.tj/?a=<card>&s=<amount>&c=<label>&f1=133
    # in a browser and confirmed it lands on a real payment page (not
    # reverse-engineered from a rival bot this time). Only used for
    # bank_hint=="dc" — see _build_dc_link in bot/services/payments.py.
    dc_pay_base_url: str = os.getenv("DC_PAY_BASE_URL", "https://pay.dc.tj/")
    dc_pay_f1: str = os.getenv("DC_PAY_F1", "133")
    # Alif Mobi app deep link into its "DC кошелек" top-up provider —
    # captured from a real link the admin generated in their own app
    # (https://alifmobi.page.link/providers?id=124&amount=X&account=Y).
    # id=124 is Alif's fixed catalog entry for DC wallet top-ups (same for
    # every customer); account is the admin's own receiving DC wallet
    # phone number. Unrelated to ALIF_SHOP_ID below, which is for the
    # separate (unused, scaffold-only) Alif Business merchant-API integration.
    alif_dc_provider_id: str = os.getenv("ALIF_DC_PROVIDER_ID", "124")
    alif_dc_receiving_account: str = os.getenv("ALIF_DC_RECEIVING_ACCOUNT", "559994751")
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

    # Optional application-level self-ping. This is NOT a substitute for an
    # always-on host: Render free-tier usage limits may still suspend the
    # service, and external pings cannot override provider policies.
    # Keep disabled by default. Enable only if your paid hosting plan allows it.
    keepalive_ping_seconds: int = int(os.getenv("KEEPALIVE_PING_SECONDS", "300"))

    # AdsGram monetization. Keep the token in the host secret store, never in git.
    adsgram_token: str = os.getenv("ADSGRAM_TOKEN", "")
    adsgram_block_id: str = os.getenv("ADSGRAM_BLOCK_ID", "54082") or "54082"

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

    # Optional Telegram GIF/MP4 animation file_id for the welcome screen.
    # Set WELCOME_ANIMATION_FILE_ID in Render after uploading the animation to the bot.
    welcome_animation_file_id: str = os.getenv("WELCOME_ANIMATION_FILE_ID", "")
    # Optional Telegram animation IDs; invalid IDs are handled with text fallbacks.
    shop_animation_file_id: str = os.getenv("SHOP_ANIMATION_FILE_ID", "")
    loading_animation_file_id: str = os.getenv("LOADING_ANIMATION_FILE_ID", "")
    # Optional MP4 tutorial video shown by /tutorial.
    tutorial_video_file_id: str = os.getenv("TUTORIAL_VIDEO_FILE_ID", "").strip()

    # Public shop channel: bot must be added there as admin with "Post
    # Messages" permission, otherwise announcements silently fail.
    shop_channel_url: str = os.getenv("SHOP_CHANNEL_URL", "https://t.me/FF_MARGEDAR_SHOP")
    review_channel_id: str = os.getenv("REVIEW_CHANNEL_ID", "@OTZIV_FF_MARGEDAR")
    review_channel_url: str = os.getenv("REVIEW_CHANNEL_URL", "https://t.me/OTZIV_FF_MARGEDAR")

    # Customer support is Telegram-only. Set this to the owner's public
    # Telegram username, e.g. https://t.me/your_support_account.
    # Falls back to the shop channel until the support username is configured.
    support_telegram_url: str = os.getenv("SUPPORT_TELEGRAM_URL", "https://t.me/FF_MARGEDAR_SHOP")


config = Config()
