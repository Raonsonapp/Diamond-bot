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
from bot.db.repo import (
    credit_referral_balance,
    get_order,
    get_orders_by_group,
    get_product,
    get_user,
    set_order_status,
)
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


async def _resolve_group(session, order: Order) -> list[Order]:
    if not order.cart_group_id:
        return [order]
    return await get_orders_by_group(session, order.cart_group_id)


def _item_line(order: Order, product) -> str:
    bonus = f" (+{product.bonus_diamonds} бонус)" if product.bonus_diamonds else ""
    return f"📦 {product.diamonds}{bonus}{product.unit_label}"


async def confirm_and_deliver(bot: Bot, order_id: int, payment_reference: str | None = None) -> FulfillmentResult | None:
    """Mark an order (and, for a multi-pack cart, every sibling order that
    shares its cart_group_id) PAID, then try to deliver automatically.
    Returns None if the order doesn't exist. Caller is responsible for
    admin-side UI (keyboard updates, confirmations) — this only touches
    orders and messages the customer.

    Auto-delivery for a cart is all-or-nothing: if every item in the group
    can be delivered via the API, all get marked DELIVERED and the customer
    gets one combined message; if even one item can't (unmapped product,
    API error), none are auto-delivered and the whole group waits for the
    admin's single manual "Delivered" tap — simpler and safer than tracking
    a half-delivered cart."""
    async with get_session() as session:
        order = await get_order(session, order_id)
        if order is None:
            return None
        group = await _resolve_group(session, order)
        for o in group:
            await set_order_status(
                session, o, OrderStatus.PAID,
                payment_reference=payment_reference if o.id == order.id else None,
            )
        products = {o.id: await get_product(session, o.product_id) for o in group}
        order = await get_order(session, order_id)

    total = sum(o.amount_somoni for o in group) or sum(products[o.id].price_somoni for o in group)
    if len(group) > 1:
        summary = "\n".join(_item_line(o, products[o.id]) for o in group)
        await bot.send_message(
            order.user_id,
            f"✅ Пардохти фармоиши гурӯҳии #{order.id} тасдиқ шуд ({total:.2f} сомонӣ):\n{summary}\n\n"
            f"Ба зудӣ ба ҳисоби шумо ирсол мешавад.",
        )
    else:
        product = products[order.id]
        await bot.send_message(
            order.user_id,
            f"✅ Пардохти фармоиши #{order.id} тасдиқ шуд. "
            f"{product.diamonds}{product.unit_label} ба зудӣ ба ҳисоби шумо ирсол мешавад.",
        )

    delivery = get_delivery_provider()
    results = []
    for o in group:
        try:
            r = await delivery.deliver(o.id, o.ff_player_id, products[o.id])
        except NotImplementedError:
            r = None
        results.append((o, r))

    all_success = bool(results) and all(r and r.success for _, r in results)
    if not all_success:
        if config.admin_chat_id:
            lines = [f"⚠️ Таҳвили худкор барои фармоиши #{order.id} нашуд — лутфан санҷед ва дастӣ иҷро карда, 'Delivered'-ро занед:"]
            for o, r in results:
                reason = r.message if r is not None else "delivery provider raised NotImplementedError"
                lines.append(f"#{o.id} ({o.ff_player_id}): {reason}")
            await bot.send_message(config.admin_chat_id, "\n".join(lines)[:4000])
        return FulfillmentResult(order=order, auto_delivered=False)

    async with get_session() as session:
        delivered = []
        for o, r in results:
            fresh = await get_order(session, o.id)
            note = f"delivery_ref={r.reference}" if r.reference else None
            fresh = await set_order_status(session, fresh, OrderStatus.DELIVERED, admin_note=note)
            await _credit_referral(session, fresh)
            delivered.append(fresh)

    if len(delivered) > 1:
        summary = "\n".join(f"{_item_line(o, products[o.id])} → {o.ff_player_id}" for o in delivered)
        await bot.send_message(order.user_id, f"🎉 Ҳама маҳсулоти фармоиши шумо ирсол шуд:\n{summary}")
    else:
        product = products[delivered[0].id]
        await bot.send_message(
            order.user_id,
            f"🎉 {product.diamonds}{product.unit_label} ба аккаунти шумо ({delivered[0].ff_player_id}) ирсол шуд!",
        )
    await prompt_for_review(bot, delivered[0])
    return FulfillmentResult(order=delivered[0], auto_delivered=True)


async def mark_delivered_and_notify(bot: Bot, order_id: int) -> Order | None:
    """Admin manually confirms an order (and any sibling cart orders) was
    delivered by hand."""
    async with get_session() as session:
        order = await get_order(session, order_id)
        if order is None:
            return None
        group = await _resolve_group(session, order)
        delivered = []
        products = {}
        for o in group:
            fresh = await set_order_status(session, o, OrderStatus.DELIVERED)
            products[fresh.id] = await get_product(session, fresh.product_id)
            await _credit_referral(session, fresh)
            delivered.append(fresh)

    if len(delivered) > 1:
        summary = "\n".join(f"{_item_line(o, products[o.id])} → {o.ff_player_id}" for o in delivered)
        await bot.send_message(order.user_id, f"🎉 Ҳама маҳсулоти фармоиши шумо ирсол шуд:\n{summary}")
    else:
        product = products[delivered[0].id]
        await bot.send_message(
            order.user_id,
            f"🎉 {product.diamonds}{product.unit_label} ба аккаунти шумо ({delivered[0].ff_player_id}) ирсол шуд!",
        )
    await prompt_for_review(bot, delivered[0])
    return delivered[0]
