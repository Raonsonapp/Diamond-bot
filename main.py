import asyncio
import logging

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BotCommand, ErrorEvent
from aiohttp import web

from bot.config import config
from bot.db.session import init_db
from bot.fsm_storage import storage
from bot.handlers import admin, customer, adsgram_admin
from bot.services.sms_webhook import register_sms_webhook

logging.basicConfig(level=logging.INFO)


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=storage)
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
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="🏠 Ба меню баргаштан"),
            BotCommand(command="myorders", description="📦 Фармоишҳои ман"),
            BotCommand(command="wallet", description="👛 Баланс ва пуркунӣ"),
            BotCommand(command="apikey", description="🔑 Калиди API-и шахсӣ"),
        ]
    )


async def run_polling(bot: Bot, dp: Dispatcher) -> None:
    """Local development mode: the bot itself keeps asking Telegram for updates."""
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


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
        drop_pending_updates=True,
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

    await init_db()

    bot = Bot(token=config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = build_dispatcher()
    await _set_bot_commands(bot)

    if config.public_url:
        await run_webhook(bot, dp)
    else:
        await run_polling(bot, dp)


if __name__ == "__main__":
    asyncio.run(main())
