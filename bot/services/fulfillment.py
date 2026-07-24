"""Core "payment confirmed -> attempt delivery" logic, shared between the
admin's Telegram button and the SMS auto-confirmation webhook so both
paths behave identically (referral credit, review prompt, customer
messages) instead of drifting apart.
"""

from __future__ import annotations

from dataclasses import dataclass

from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey

from bot.config import config
from bot.db.models import Order, OrderStatus
from bot.db.repo import credit_referral_balance, get_order, get_product, get_user, set_order_status
from bot.db.session import get_session
from bot.fsm_storage import storage
from bot.keyboards import review_prompt_keyboard
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
        "🙏 Лутфан як шарҳи кӯтоҳ дар бораи хидмат нависед — ин ба дигар мизоҷон кӯмак мекунад!",
        reply_markup=review_prompt_keyboard(order.id),
    )


async def confirm_and_deliver(bot: Bot, order_id: int, payment_reference: str | None = None) -> FulfillmentResult | None:
    """Mark an order PAID and try to deliver it automatically. Returns None
    if the order doesn't exist. Caller is responsible for admin-side UI
    (keyboard updates, confirmations) — this only touches the order and
    messages the customer."""
    async with get_session() as session:
        order = await get_order(session, order_id)
        if order is None:
            return None
        order = await set_order_status(
            session, order, OrderStatus.PAID, payment_reference=payment_reference
        )
        product = await get_product(session, order.product_id)

    await bot.send_message(
        order.user_id,
        f"✅ Пардохти фармоиши #{order.id} тасдиқ шуд. "
        f"{product.diamonds}{product.unit_label} ба зудӣ ба ҳисоби шумо ирсол мешавад.",
    )

    delivery = get_delivery_provider()
    try:
        result = await delivery.deliver(order.id, order.ff_player_id, product)
    except NotImplementedError:
        result = None

    if result and result.success:
        async with get_session() as session:
            order = await get_order(session, order_id)
            # payment_reference already holds the SMS/bank dedup code from
            # the PAID step above (if any) — record the delivery provider's
            # own reference separately instead of overwriting it.
            note = f"delivery_ref={result.reference}" if result.reference else None
            await set_order_status(session, order, OrderStatus.DELIVERED, admin_note=note)
            await _credit_referral(session, order)
        await bot.send_message(
            order.user_id,
            f"🎉 {product.diamonds}{product.unit_label} ба аккаунти шумо ({order.ff_player_id}) ирсол шуд!",
        )
        await prompt_for_review(bot, order)
        return FulfillmentResult(order=order, auto_delivered=True)

    return FulfillmentResult(order=order, auto_delivered=False)


async def mark_delivered_and_notify(bot: Bot, order_id: int) -> Order | None:
    """Admin manually confirms an order was delivered by hand."""
    async with get_session() as session:
        order = await get_order(session, order_id)
        if order is None:
            return None
        order = await set_order_status(session, order, OrderStatus.DELIVERED)
        product = await get_product(session, order.product_id)
        await _credit_referral(session, order)

    await bot.send_message(
        order.user_id,
        f"🎉 {product.diamonds}{product.unit_label} ба аккаунти шумо ({order.ff_player_id}) ирсол шуд!",
    )
    await prompt_for_review(bot, order)
    return order
