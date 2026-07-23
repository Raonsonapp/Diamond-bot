"""HTTP endpoint for SMS-forwarder apps: the admin's phone forwards every
incoming bank "Zachislenie" (deposit) SMS here, and a matching pending
order gets confirmed automatically — no button tap needed.

Accepts either a JSON body with the SMS text under a common field name
(message/text/body/sms/content — different forwarder apps use different
ones) or a raw text body, so it works with whichever forwarder app the
admin picks.
"""

import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiohttp import web

from bot.config import config
from bot.db.repo import find_order_by_payment_reference, find_orders_awaiting_amount
from bot.db.session import get_session
from bot.services.fulfillment import confirm_and_deliver
from bot.services.sms_parser import parse_deposit_sms

logger = logging.getLogger(__name__)

_TEXT_FIELDS = ("message", "text", "body", "sms", "content")


async def _extract_text(request: web.Request) -> str:
    if request.content_type == "application/json":
        try:
            data = await request.json()
        except Exception:
            return ""
        for key in _TEXT_FIELDS:
            value = data.get(key)
            if isinstance(value, str):
                return value
        return ""

    if request.content_type == "application/x-www-form-urlencoded":
        data = await request.post()
        for key in _TEXT_FIELDS:
            if key in data:
                return str(data[key])
        return ""

    return await request.text()


def register_sms_webhook(app: web.Application, bot: Bot) -> None:
    if not config.sms_webhook_secret:
        logger.info("SMS_WEBHOOK_SECRET not set — SMS auto-confirmation endpoint disabled.")
        return

    async def handler(request: web.Request) -> web.Response:
        secret = request.headers.get("X-Webhook-Secret") or request.query.get("secret")
        if secret != config.sms_webhook_secret:
            return web.Response(status=403, text="forbidden")

        text = await _extract_text(request)
        parsed = parse_deposit_sms(text) if text else None
        if parsed is None:
            return web.Response(status=200, text="ignored")

        async with get_session() as session:
            already = await find_order_by_payment_reference(session, parsed.kod)
            if already is not None:
                return web.Response(status=200, text="already processed")

            since = datetime.now(timezone.utc) - timedelta(minutes=config.sms_match_window_minutes)
            candidates = await find_orders_awaiting_amount(session, parsed.amount_somoni, since)

        if len(candidates) == 1:
            order_id = candidates[0].id
            result = await confirm_and_deliver(bot, order_id, payment_reference=parsed.kod)
            if config.admin_chat_id and result is not None:
                status_note = "автоматӣ ирсол шуд" if result.auto_delivered else "лутфан дастӣ ирсол кунед ва 'Delivered'-ро занед"
                await bot.send_message(
                    config.admin_chat_id,
                    f"🤖 SMS: фармоиши #{order_id} худкор тасдиқ шуд "
                    f"({parsed.amount_somoni:.2f} сомонӣ) — {status_note}.",
                )
            return web.Response(status=200, text="confirmed")

        if config.admin_chat_id:
            if not candidates:
                note = "ягон фармоиши интизории мувофиқ ёфт нашуд"
            else:
                ids = ", ".join(f"#{o.id}" for o in candidates)
                note = f"якчанд фармоиши мувофиқ ёфт шуд ({ids}) — лутфан дастӣ тасдиқ кунед"
            await bot.send_message(
                config.admin_chat_id,
                f"⚠️ SMS-и пардохт омад ({parsed.amount_somoni:.2f} сомонӣ), аммо {note}.\n\n{text}",
            )
        return web.Response(status=200, text="unmatched")

    app.router.add_post(config.sms_webhook_path, handler)
    logger.info("SMS auto-confirmation endpoint registered at %s", config.sms_webhook_path)
