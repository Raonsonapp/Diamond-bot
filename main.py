import asyncio
import logging

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramConflictError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import BotCommand, BotCommandScopeChat, ErrorEvent
from aiohttp import web

from bot.config import config
from bot.db.session import engine, init_db
from sqlalchemy import text
from bot.fsm_storage import storage
from bot.handlers import admin, customer, adsgram_admin
from bot.services.sms_webhook import register_sms_webhook

logging.basicConfig(level=logging.INFO)


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=storage)

    # Diagnostic trace: prove whether Telegram updates reach this process,
    # which update type arrived, and whether router dispatch raises an error.
    # Do not log message bodies, payment details, or other private content.
    async def trace_incoming_update(handler, event, data):
        update_id = getattr(event, "update_id", "unknown")
        message = (
            getattr(event, "message", None)
            or getattr(event, "edited_message", None)
            or getattr(event, "channel_post", None)
            or getattr(event, "edited_channel_post", None)
        )
        callback = getattr(event, "callback_query", None)
        chosen = message or callback
        user = getattr(chosen, "from_user", None)
        user_id = getattr(user, "id", None)
        chat = getattr(chosen, "chat", None)
        chat_id = getattr(chat, "id", None)
        update_type = (
            "message" if message is not None
            else "callback_query" if callback is not None
            else "other"
        )
        command = ""
        if message is not None:
            body = getattr(message, "text", None) or ""
            if body.startswith("/"):
                command = body.split(maxsplit=1)[0][:40]
        elif callback is not None:
            command = f"callback:{(getattr(callback, 'data', '') or '')[:60]}"
        elif message is not None and (getattr(message, "text", None) or "").strip().casefold() in {
            "📋 меню", "меню", "🏠 меню"
        }:
            command = "reply:menu"

        logging.info(
            "UPDATE_INCOMING id=%s type=%s user_id=%s chat_id=%s action=%s",
            update_id, update_type, user_id, chat_id, command or "-",
        )
        try:
            result = await handler(event, data)
        except Exception:
            logging.exception(
                "UPDATE_DISPATCH_FAILED id=%s type=%s user_id=%s action=%s",
                update_id, update_type, user_id, command or "-",
            )
            raise
        logging.info(
            "UPDATE_DISPATCH_FINISHED id=%s type=%s user_id=%s",
            update_id, update_type, user_id,
        )
        return result

    dp.update.outer_middleware(trace_incoming_update)
    dp.include_router(admin.router)
    dp.include_router(customer.router)
    dp.include_router(adsgram_admin.router)

    @dp.errors()
    async def handle_stale_edit(event: ErrorEvent) -> bool:
        """Tapping a button whose screen has since been re-edited to the
        exact same text/markup (e.g. "Ба меню" while already on the main
        menu) makes Telegram's editMessageText call fail with "message is
        not modified". Left uncaught, that exception stops the handler
        before it reaches callback.answer() — so the button's loading
        spinner never clears and just spins forever. Swallow only this
        specific error and clear the spinner instead."""
        if isinstance(event.exception, TelegramBadRequest) and "message is not modified" in str(event.exception):
            callback = event.update.callback_query
            if callback is not None:
                await callback.answer()
            return True
        return False

    return dp


async def _set_bot_commands(bot: Bot) -> None:
    """Populates Telegram's native "☰ Меню" quick-command popup next to
    the message box — separate from (and doesn't replace) the persistent
    reply-keyboard, which stays the primary navigation."""
    user_commands = [
        BotCommand(command="start", description="🏠 Менюи асосӣ"),
        BotCommand(command="products", description="🛍️ Каталоги маҳсулот"),
        BotCommand(command="myorders", description="📦 Фармоишҳои ман"),
        BotCommand(command="wallet", description="👛 Ҳамён ва пур кардани тавозун"),
        BotCommand(command="apikey", description="🔑 Калиди API-и шахсӣ"),
        BotCommand(command="help", description="❓ Дастури истифода"),
    ]
    await bot.set_my_commands(user_commands)

    # Telegram only permits ASCII English letters, digits and underscores
    # in native slash-command names. Admin command descriptions are localized
    # in Tajik, and the full list is visible only in each configured admin chat.
    admin_commands = [
        BotCommand(command="myid", description="🪪 ID-и Telegram-и худро бинед"),
        BotCommand(command="admincheck", description="🧪 Санҷиши фиристодани паём ба админ"),
        BotCommand(command="pending", description="⏳ Фармоишҳои интизори тасдиқ"),
        BotCommand(command="proofs", description="🧾 Чекҳои пардохт"),
        BotCommand(command="order", description="📦 Тафсилоти фармоиш"),
        BotCommand(command="products", description="🛍️ Рӯйхати бастаҳо"),
        BotCommand(command="addproduct", description="➕ Иловаи баста"),
        BotCommand(command="delproduct", description="🗑️ Нест кардани баста"),
        BotCommand(command="setprice", description="💰 Тағйири нархи баста"),
        BotCommand(command="setname", description="✏️ Тағйири номи баста"),
        BotCommand(command="roundprices", description="🔢 Яклухт кардани нархҳо"),
        BotCommand(command="mapproduct", description="🔗 Пайваст кардани баста ба таъминкунанда"),
        BotCommand(command="broadcast", description="📢 Эълон ба ҳамаи корбарон"),
        BotCommand(command="userstats", description="👥 Омори корбарон"),
        BotCommand(command="salesstats", description="📊 Омори фурӯш"),
        BotCommand(command="adstats", description="📣 Омори реклама"),
        BotCommand(command="balance", description="💳 Баланси таъминкунанда"),
        BotCommand(command="addbalance", description="➕ Иловаи баланс"),
        BotCommand(command="setbalance", description="🛠️ Танзими баланс"),
        BotCommand(command="walletpending", description="👛 Пардохтҳои ҳамёни интизор"),
        BotCommand(command="walletapprove", description="✅ Тасдиқи пардохти ҳамён"),
        BotCommand(command="paid", description="✅ Тасдиқи дастии пардохт"),
        BotCommand(command="underpaid", description="⚠️ Фармоишҳои кампардохт"),
        BotCommand(command="underpaidok", description="🧾 Тасдиқи кампардохт"),
        BotCommand(command="delivered", description="🚚 Қайд кардани ирсоли маҳсулот"),
        BotCommand(command="maintenance", description="🛠️ Фурӯзон/хомӯш кардани нигоҳдорӣ"),
        BotCommand(command="setbonus", description="🎁 Танзими бонус"),
        BotCommand(command="setalmazonprices", description="💎 Танзими нархҳои алмос"),
        BotCommand(command="contest_start", description="🏆 Оғози озмун"),
        BotCommand(command="contest_status", description="📋 Ҳолати озмун"),
        BotCommand(command="fzr_categories", description="🧩 Категорияҳои FazerCards"),
        BotCommand(command="fzr_offers", description="📦 Бастаҳои FazerCards"),
        BotCommand(command="fzr_validate_id", description="🆔 Санҷиши ID-и бозигар"),
        BotCommand(command="fzr_raw", description="🔍 Ҷавоби хоми FazerCards"),
        BotCommand(command="addstars", description="⭐ Иловаи бастаи Telegram Stars"),
        BotCommand(command="addbigo", description="📦 Иловаи маҳсулоти иловагӣ"),
        BotCommand(command="addffid", description="🆔 Иловаи бастаи Free Fire ID"),
        BotCommand(command="apologize", description="💬 Паёми узрхоҳӣ ба фармоишгар"),
        BotCommand(command="settgkind", description="✈️ Танзими навъи маҳсулоти Telegram"),
        BotCommand(command="fzr_docs_search", description="🔎 Ҷустуҷӯ дар ҳуҷҷатҳои FazerCards"),
        BotCommand(command="fzr_docs_path", description="📚 Роҳи ҳуҷҷатҳои FazerCards"),
        BotCommand(command="adcheck", description="🔍 Санҷиши пайвасти AdsGram"),
        BotCommand(command="setad", description="📢 Танзими рекламаи AdsGram"),
        BotCommand(command="stopad", description="⏹️ Қатъи рекламаи AdsGram"),
        BotCommand(command="campaignstats", description="📊 Омори маъракаи AdsGram"),
        BotCommand(command="announce_new_bot", description="📣 Эълони навсозии бот"),
        BotCommand(command="resetbot", description="⚠️ Аз нав танзим кардан"),
    ]
    admin_chat_ids = set(config.admin_user_ids or [])
    if config.admin_chat_id:
        admin_chat_ids.add(config.admin_chat_id)
    for chat_id in admin_chat_ids:
        await bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=chat_id))


# A PostgreSQL advisory lock prevents two deployments that share the same
# database from polling the same Telegram bot token at the same time. On
# SQLite/Hugging Face, a process lock prevents duplicate workers in one
# container. Telegram's 409 Conflict is still treated as fatal below: it is
# never silently retried forever while updates (including payment proofs) pile up.
_INSTANCE_LOCK_CONNECTION = None
_INSTANCE_LOCK_FILE = None
_INSTANCE_LOCK_ID = 8649779422


async def _acquire_single_instance_lock() -> None:
    """Acquire the one-worker lock, allowing an old Space worker to exit.

    Hugging Face may briefly overlap the old container process and the new
    process during a restart. Do not fail immediately in that handover
    window, but never start polling unless this process owns the lock.
    """
    global _INSTANCE_LOCK_CONNECTION, _INSTANCE_LOCK_FILE

    import time

    wait_seconds = 180
    retry_seconds = 3
    deadline = time.monotonic() + wait_seconds

    if engine.dialect.name == "postgresql":
        connection = await engine.connect()
        try:
            while True:
                acquired = await connection.scalar(
                    text("SELECT pg_try_advisory_lock(:lock_id)"),
                    {"lock_id": _INSTANCE_LOCK_ID},
                )
                if acquired:
                    _INSTANCE_LOCK_CONNECTION = connection
                    logging.info("Single-instance PostgreSQL advisory lock acquired")
                    return

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(
                        "Another FF MARGEDAR worker still owns the PostgreSQL bot lock "
                        f"after {wait_seconds}s. The old worker must stop before polling starts."
                    )
                logging.warning(
                    "Another worker still owns the PostgreSQL lock; waiting %.0fs before retry",
                    min(retry_seconds, remaining),
                )
                await asyncio.sleep(min(retry_seconds, remaining))
        except Exception:
            if _INSTANCE_LOCK_CONNECTION is not connection:
                await connection.close()
            raise

    # flock is released by the OS automatically when the owning process exits.
    # Keep one file descriptor open while retrying; never unlink/recreate the
    # lock file, since that could let two processes lock different inodes.
    import fcntl

    lock_file = open("/tmp/ff-margedar-bot.lock", "a+")
    try:
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                _INSTANCE_LOCK_FILE = lock_file
                logging.info("Single-instance process lock acquired")
                return
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(
                        "Another FF MARGEDAR worker still owns the process lock "
                        f"after {wait_seconds}s. The old worker must stop before polling starts."
                    )
                logging.warning(
                    "Another worker still owns the process lock; waiting %.0fs before retry",
                    min(retry_seconds, remaining),
                )
                await asyncio.sleep(min(retry_seconds, remaining))
    except Exception:
        lock_file.close()
        raise

async def _release_single_instance_lock() -> None:
    global _INSTANCE_LOCK_CONNECTION, _INSTANCE_LOCK_FILE

    if _INSTANCE_LOCK_CONNECTION is not None:
        try:
            await _INSTANCE_LOCK_CONNECTION.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": _INSTANCE_LOCK_ID},
            )
        finally:
            await _INSTANCE_LOCK_CONNECTION.close()
            _INSTANCE_LOCK_CONNECTION = None

    if _INSTANCE_LOCK_FILE is not None:
        import fcntl
        try:
            fcntl.flock(_INSTANCE_LOCK_FILE.fileno(), fcntl.LOCK_UN)
        finally:
            _INSTANCE_LOCK_FILE.close()
            _INSTANCE_LOCK_FILE = None


async def run_polling(bot: Bot, dp: Dispatcher) -> None:
    """Single-owner polling loop with visible, bounded recovery from outages."""
    webhook = await bot.get_webhook_info()
    if webhook.url:
        logging.warning(
            "A Telegram webhook was configured at %s; removing it without dropping queued updates",
            webhook.url,
        )
        await bot.delete_webhook(drop_pending_updates=False)

    offset = None
    backoff = 1
    allowed_updates = dp.resolve_used_update_types()
    logging.info("Starting single-owner polling; allowed_updates=%s", allowed_updates)

    while True:
        try:
            updates = await bot.get_updates(
                offset=offset,
                timeout=25,
                request_timeout=35,
                allowed_updates=allowed_updates,
            )
            backoff = 1
            for update in updates:
                # Acknowledge an update to Telegram only after dispatch
                # succeeds. If a receipt handler crashes, keep the same offset
                # so Telegram can redeliver it instead of silently losing it.
                try:
                    await dp.feed_update(bot, update)
                except Exception:
                    logging.exception(
                        "Unhandled update dispatch error; update_id=%s; update will be retried",
                        update.update_id,
                    )
                    await asyncio.sleep(1)
                    break
                offset = update.update_id + 1
        except TelegramConflictError:
            logging.critical(
                "Telegram 409 Conflict: another server is polling this bot token. "
                "This worker is stopping to avoid competing for updates. "
                "Keep exactly one active deployment using this BOT_TOKEN."
            )
            raise
        except TelegramRetryAfter as exc:
            delay = max(1, int(exc.retry_after))
            logging.warning("Telegram rate limit; retrying in %ss", delay)
            await asyncio.sleep(delay)
        except (TelegramNetworkError, TelegramServerError) as exc:
            logging.warning(
                "Telegram polling network/server error: %s; retrying in %ss",
                exc,
                backoff,
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)



_SELF_PING_RETRY_SECONDS = 20  # how soon to retry after a failed ping, instead of waiting a full interval


async def _self_ping_loop(url: str, interval_seconds: int) -> None:
    """Render's free tier sleeps the service after ~15 min without incoming
    HTTP traffic — not just a slow-next-request problem, but a real risk of
    silently losing an incoming SMS webhook if the sender gives up before
    the ~30-60s cold start finishes responding. Pinging our own health
    endpoint on a shorter interval keeps it looking active so it never gets
    the chance to sleep. On a failed ping, retries soon (not after the full
    interval again) so one transient network blip can't leave a much
    longer-than-intended gap for the service to fall asleep in."""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
        while True:
            try:
                async with session.get(url) as resp:
                    logging.info("Self-ping %s -> HTTP %s", url, resp.status)
                await asyncio.sleep(interval_seconds)
            except Exception as exc:
                logging.warning("Self-ping to %s failed: %s — retrying in %ss", url, exc, _SELF_PING_RETRY_SECONDS)
                await asyncio.sleep(_SELF_PING_RETRY_SECONDS)


async def run_webhook(bot: Bot, dp: Dispatcher) -> None:
    """Production mode (e.g. Render): Telegram pushes updates to our public URL.

    Render (and most hosts) route external traffic to whatever port your
    process listens on via the $PORT environment variable, and expect the
    process to keep running and answering HTTP requests — that's what this
    aiohttp app does. PUBLIC_URL must be the exact https URL Render gave
    your service, e.g. https://diamond-bot-qakk.onrender.com
    """
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

    webhook_url = config.public_url.rstrip("/") + config.telegram_webhook_path
    await bot.set_webhook(
        url=webhook_url,
        secret_token=config.telegram_webhook_secret or None,
        drop_pending_updates=False,
    )

    app = web.Application()

    async def health(_request: web.Request) -> web.Response:
        return web.Response(text="OK")

    app.router.add_get("/", health)
    from bot.services.wallet_api import register_wallet_api
    register_wallet_api(app, bot)

    async def promo_click(request: web.Request) -> web.StreamResponse:
        """Track an app-promo redirect and send the user to the official Play Store page."""
        from bot.services.ad_campaign import APP_URL, record_promo_click

        raw_user_id = request.query.get("uid", "")
        if raw_user_id.isdigit() and int(raw_user_id) > 0:
            try:
                await record_promo_click(int(raw_user_id))
            except Exception:
                logging.exception("Could not record app promo click")
        raise web.HTTPFound(location=APP_URL)

    app.router.add_get("/promo/click", promo_click)

    async def adsgram_reward_callback(request: web.Request) -> web.Response:
        """Accept AdsGram's optional reward postback without granting money/items.

        This bot does not currently promise an in-bot reward for viewing ads.
        The endpoint only acknowledges the callback and logs the Telegram ID;
        do not use it as proof of a reward unless a reward system is implemented.
        """
        user_id = request.query.get("userid") or request.query.get("userId")
        if not user_id or not user_id.isdigit():
            return web.Response(status=400, text="Missing or invalid userid")
        logging.info(
            "AdsGram reward postback received for Telegram user %s; no reward configured",
            user_id,
        )
        return web.Response(text="OK")

    app.router.add_get("/adsgram/reward", adsgram_reward_callback)
    register_sms_webhook(app, bot)

    SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=config.telegram_webhook_secret or None,
    ).register(app, path=config.telegram_webhook_path)

    setup_application(app, dp, bot=bot)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=config.port)
    await site.start()
    logging.info("Webhook server listening on port %s, webhook url %s", config.port, webhook_url)

    if config.keepalive_ping_seconds > 0:
        asyncio.create_task(_self_ping_loop(config.public_url, config.keepalive_ping_seconds))
        logging.info("Self-ping keepalive enabled: every %ss", config.keepalive_ping_seconds)

    # Keep the process alive; aiohttp runs the handlers in the background.
    await asyncio.Event().wait()


async def main() -> None:
    if not config.bot_token:
        raise RuntimeError("BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")

    await _acquire_single_instance_lock()
    bot = Bot(token=config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        await init_db()
        dp = build_dispatcher()
        await _set_bot_commands(bot)

        if config.public_url:
            await run_webhook(bot, dp)
        else:
            await run_polling(bot, dp)
    finally:
        await bot.session.close()
        await _release_single_instance_lock()


if __name__ == "__main__":
    asyncio.run(main())
