"""Core "payment confirmed -> attempt delivery" logic, shared between the
admin's Telegram button and the SMS auto-confirmation webhook so both
paths behave identically (referral credit, review prompt, customer
messages) instead of drifting apart.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey

from bot.config import config
from bot.db.models import Order, OrderStatus
from bot.db.repo import (
    adjust_fazercards_balance,
    credit_referral_balance,
    get_fazercards_balance,
    get_order,
    get_orders_by_group,
    get_product,
    get_user,
    set_order_status,
    set_progress_message_id,
)
from bot.db.session import get_session
from bot.fsm_storage import storage
from bot.keyboards import review_prompt_keyboard
from bot.services.contest import maybe_declare_contest_winner
from bot.services.delivery import get_delivery_provider
from bot.states import OrderFlow

REFERRAL_BONUS_RATE = 0.05


@dataclass
class FulfillmentResult:
    order: Order
    auto_delivered: bool


async def _credit_referral(session, order: Order) -> None:
    buyer = await get_user(session, order.user_id)
    if buyer is None or buyer.referred_by is None:
        return
    bonus = round(order.amount_somoni * REFERRAL_BONUS_RATE, 2)
    await credit_referral_balance(session, buyer.referred_by, bonus)


async def prompt_for_review(bot: Bot, order: Order) -> None:
    """After delivery, ask the customer for a short review — customer.py
    posts it to the shop channel once they reply (or skip)."""
    if not config.review_channel_id:
        return

    key = StorageKey(bot_id=bot.id, chat_id=order.user_id, user_id=order.user_id)
    customer_state = FSMContext(storage=storage, key=key)
    await customer_state.set_state(OrderFlow.awaiting_review)
    await customer_state.update_data(order_id=order.id)

    await bot.send_message(
        order.user_id,
        "🎁 Ваучери шумо бо муваффақият расид!

"
        "😊 Лутфан, фикру отзиви худро дар бораи гирифтани ваучер нависед. "
        "Назари шумо барои зиёд шудани боварии муштариёни дигар хеле муҳим аст. ❤️",
        reply_markup=review_prompt_keyboard(order.id),
    )
