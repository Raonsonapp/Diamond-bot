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
from bot.keyboards import receipt_keyboard, review_prompt_keyboard
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
        "🙏 Лутфан як шарҳи кӯтоҳ дар бораи хидмат нависед — ин ба дигар мизоҷон кӯмак мекунад!",
        reply_markup=review_prompt_keyboard(order.id),
    )


async def _resolve_group(session, order: Order) -> list[Order]:
    if not order.cart_group_id:
        return [order]
    return await get_orders_by_group(session, order.cart_group_id)


def _item_line(order: Order, product) -> str:
    return f"📦 {product.display_name}"


_PROGRESS_TEXT = "⏳ Фармоиши шумо коркард шуда истодааст..."
# 9 steps (0% -> 90% by +10 each) * 6.4s ≈ 58s — timed to span
# delivery.py's own ~60s FazerCards polling window (_POLL_ATTEMPTS *
# _POLL_DELAY_SECONDS) so 90% is reached right around when a genuinely
# slow order would still be polling, instead of stalling at 90% for up to
# ~48s beforehand looking stuck. A fast real confirmation (most orders,
# within ~1s) still jumps straight to 100% via _finish_progress_bar
# regardless of wherever the animation happens to be.
_PROGRESS_STEP_SECONDS = 6.4
_PROGRESS_AUTO_CAP = 90  # never claims 100% on its own — only a confirmed delivery does that


def _progress_bar(pct: int) -> str:
    filled = pct // 10
    bar = "▓" * filled + "░" * (10 - filled)
    return f"{_PROGRESS_TEXT}\n{bar} {pct}%"


async def _animate_progress(bot: Bot, chat_id: int, message_id: int, stop_event: asyncio.Event) -> None:
    """Ticks a progress bar up to _PROGRESS_AUTO_CAP while the real
    auto-delivery attempt runs, so the customer sees something moving
    instead of silence. Deliberately never reaches 100% by itself — only
    _finish_progress_bar (called once delivery is *actually* confirmed,
    whether immediately or later by hand) does that, so this can never
    claim diamonds arrived before they really did."""
    pct = 0
    while pct < _PROGRESS_AUTO_CAP and not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=_PROGRESS_STEP_SECONDS)
        except asyncio.TimeoutError:
            pass
        if stop_event.is_set():
            return
        pct = min(pct + 10, _PROGRESS_AUTO_CAP)
        try:
            await bot.edit_message_text(_progress_bar(pct), chat_id=chat_id, message_id=message_id)
        except Exception:
            return  # message deleted, chat blocked the bot, etc. — not worth retrying


async def _start_progress_bar(bot: Bot, order: Order) -> tuple[int | None, asyncio.Event]:
    stop_event = asyncio.Event()
    try:
        sent = await bot.send_message(order.user_id, _progress_bar(0))
        message_id = sent.message_id
        if not isinstance(message_id, int):
            raise TypeError("send_message did not return a real message_id")
    except Exception:
        return None, stop_event
    asyncio.create_task(_animate_progress(bot, order.user_id, message_id, stop_event))
    return message_id, stop_event


async def _pause_progress_bar(bot: Bot, order: Order, message_id: int) -> None:
    """Auto-delivery fell through to manual — freeze the bar at 90% (never
    100%, that would be a lie) and persist message_id so whichever admin
    action finishes the order later (Delivered button / /delivered) can
    find and complete this same message instead of leaving it stuck."""
    text = f"{_PROGRESS_TEXT}\n▓▓▓▓▓▓▓▓▓░ 90% — дар навбати коркарди дастӣ, дар наздиктарин вақт мерасад."
    try:
        await bot.edit_message_text(text, chat_id=order.user_id, message_id=message_id)
    except Exception:
        pass
    async with get_session() as session:
        fresh = await get_order(session, order.id)
        if fresh is not None:
            await set_progress_message_id(session, fresh, message_id)


async def _finish_progress_bar(bot: Bot, order: Order, message_id: int | None) -> None:
    """Delivery is now genuinely confirmed — jump the bar to 100% (whether
    it was still animating from this same confirm_and_deliver call, or
    paused at 90% from an earlier failed auto-attempt) and clear the
    stored message_id since there's nothing left to finish later."""
    if not message_id:
        return
    text = f"{_PROGRESS_TEXT}\n▓▓▓▓▓▓▓▓▓▓ 100% ✅"
    try:
        await bot.edit_message_text(text, chat_id=order.user_id, message_id=message_id)
    except Exception:
        pass
    async with get_session() as session:
        fresh = await get_order(session, order.id)
        if fresh is not None and fresh.progress_message_id:
            await set_progress_message_id(session, fresh, None)


async def clear_awaiting_review(bot: Bot, order: Order) -> None:
    """Once the admin has actually confirmed/rejected an order, drop the
    customer out of "awaiting_admin_review" (set in customer.py's
    receive_payment_proof) so they stop getting the canned "still under
    review" reply. Only clears it if they're still on *this* order and
    haven't since moved on to a newer one — an SMS auto-confirm or an
    order that never went through the photo-proof flow at all just finds
    a non-matching state here and is a no-op."""
    key = StorageKey(bot_id=bot.id, chat_id=order.user_id, user_id=order.user_id)
    customer_state = FSMContext(storage=storage, key=key)
    if await customer_state.get_state() != OrderFlow.awaiting_admin_review.state:
        return
    data = await customer_state.get_data()
    if data.get("order_id") == order.id:
        await customer_state.clear()


async def _attempt_delivery(
    bot: Bot, group: list[Order], products: dict, progress_message_id: int | None
) -> tuple[bool, list[tuple[Order, object]]]:
    """One delivery pass across every order in the group. On full success,
    marks them DELIVERED, credits referrals, messages the customer,
    finishes the progress bar, and prompts for a review — the same
    completion path whether this is the very first attempt (inside
    confirm_and_deliver) or a later background recheck. Returns
    (success, per-order results) so the caller can still build a
    diagnostic message on failure."""
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
        return False, results

    primary = group[0]
    await _finish_progress_bar(bot, primary, progress_message_id)

    async with get_session() as session:
        delivered = []
        for o, r in results:
            fresh = await get_order(session, o.id)
            note = f"delivery_ref={r.reference}" if r.reference else None
            fresh = await set_order_status(session, fresh, OrderStatus.DELIVERED, admin_note=note)
            await _credit_referral(session, fresh)
            delivered.append(fresh)
        # `success` here only ever comes from a real FazerCards call (see
        # DeliveryProvider.deliver — ManualDeliveryProvider never returns
        # success=True), so every order in this block genuinely spent
        # FazerCards balance. Kept as our own best-effort estimate (there's
        # no confirmed live balance-check endpoint) — see /balance,
        # /setbalance, /addbalance in admin.py.
        spent = sum(products[o.id].cost_somoni for o in delivered)
        if spent:
            await adjust_fazercards_balance(session, -spent)

    if len(delivered) > 1:
        summary = "\n".join(f"{_item_line(o, products[o.id])} → {o.ff_player_id}" for o in delivered)
        await bot.send_message(
            primary.user_id,
            f"🎉 Ҳама маҳсулоти фармоиши шумо ирсол шуд:\n{summary}",
            reply_markup=receipt_keyboard(delivered[0].id),
        )
    else:
        product = products[delivered[0].id]
        await bot.send_message(
            primary.user_id,
            f"🎉 {product.display_name} ба аккаунти шумо ({delivered[0].ff_player_id}) ирсол шуд!",
            reply_markup=receipt_keyboard(delivered[0].id),
        )
    await prompt_for_review(bot, delivered[0])
    return True, results


# Bumped from 4 (~2min total with the ~30s initial window) after three real
# orders in one session (#49, #50, #52 — Weekly Lite, Weekly Membership,
# 110 Diamonds) all still needed manual /delivered despite that window —
# FazerCards is apparently often slower than that in practice, not just in
# the one earlier ord-510807 case this was originally sized for.
_DELAYED_RECHECK_ATTEMPTS = 10
_DELAYED_RECHECK_INTERVAL_SECONDS = 30


async def _delayed_recheck(bot: Bot, order_id: int) -> None:
    """FazerCards can occasionally take longer than _attempt_delivery's
    own ~30s polling window to finish an order — a real order (a Weekly
    Membership) was still "processing" when we gave up, yet later showed
    completed on FazerCards' own dashboard. Rather than leaving that
    entirely to the admin, keep quietly rechecking every 30s for a couple
    of minutes in the background; if it turns out done, finish the order
    exactly like an immediate success would have."""
    for _ in range(_DELAYED_RECHECK_ATTEMPTS):
        await asyncio.sleep(_DELAYED_RECHECK_INTERVAL_SECONDS)

        async with get_session() as session:
            order = await get_order(session, order_id)
            if order is None or order.status != OrderStatus.PAID:
                return  # already resolved by hand (or something else changed) — nothing left to do
            group = await _resolve_group(session, order)
            if any(o.status != OrderStatus.PAID for o in group):
                return
            products = {o.id: await get_product(session, o.product_id) for o in group}

        success, results = await _attempt_delivery(bot, group, products, order.progress_message_id)
        if success:
            if config.admin_chat_id:
                await bot.send_message(
                    config.admin_chat_id,
                    f"✅ Фармоиши #{order.id} дертар худкор тасдиқ шуд (FazerCards дертар ҷавоб дод).",
                )
            return
        if any(r is not None and r.message.startswith("⛔") for _, r in results):
            if config.admin_chat_id:
                await bot.send_message(
                    config.admin_chat_id,
                    f"⛔ Фармоиши #{order.id}: FazerCards онро радд кард (refund) — такрор кардан бефоида аст, санҷед.",
                )
            return
    # Ran out of delayed attempts too — leave it exactly where the
    # original failure notice already put it: waiting for the admin's
    # manual "Delivered" / /delivered.


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
            f"{product.display_name} ба зудӣ ба ҳисоби шумо ирсол мешавад.",
        )
    await clear_awaiting_review(bot, order)

    # Proactive, honest heads-up when our own tracked FazerCards balance
    # estimate looks too low for this order — never a blocking gate (the
    # estimate can be stale/wrong), the real delivery attempt below still
    # runs exactly as normal either way. Only checked for products actually
    # wired to FazerCards (cost_somoni > 0 for the rest is 0 anyway, so a
    # fully-manual order would never trip this).
    total_cost = sum(products[o.id].cost_somoni for o in group)
    if total_cost:
        async with get_session() as session:
            current_balance = await get_fazercards_balance(session)
        if current_balance < total_cost:
            await bot.send_message(
                order.user_id,
                "ℹ️ Ҳозир ин маблағ дар балансатони мо назди таъминкунанда нест, "
                "аммо ба нигарониятон сабаб нест — фармоиши шумо баҳар ҳол иҷро мешавад, "
                "танҳо метавонад каме бештар вақт гирад. Админ ба зудӣ баланси худро пур мекунад.",
            )
            if config.admin_chat_id:
                await bot.send_message(
                    config.admin_chat_id,
                    f"⚠️ Фармоиши #{order.id}: баланси тахминии FazerCards ({current_balance:.2f}с) "
                    f"аз нархи фармоиш ({total_cost:.2f}с) камтар аст — балки лозим ба пуркунӣ бошад.",
                )

    async with get_session() as session:
        buyer = await get_user(session, order.user_id)
    if buyer is not None and buyer.referred_by is not None:
        # This buyer just paid — if they were referred into a still-open
        # contest, this purchase may be the exact thing that satisfies its
        # "at least one referral must buy something" requirement.
        await maybe_declare_contest_winner(bot, buyer.referred_by)

    progress_message_id, progress_stop = await _start_progress_bar(bot, order)
    success, results = await _attempt_delivery(bot, group, products, progress_message_id)
    progress_stop.set()

    if not success:
        if progress_message_id:
            await _pause_progress_bar(bot, order, progress_message_id)
        # "⛔" marks a definitive FazerCards rejection (refund/failed/etc,
        # see delivery.py's _FAILED_STATUSES) rather than "still
        # processing" — retrying that in the background for another 5
        # minutes is pointless, it will just get refused again every time,
        # so skip straight to telling the admin instead of wasting the
        # whole recheck window on something that can never complete.
        is_terminal_failure = any(r is not None and r.message.startswith("⛔") for _, r in results)
        if config.admin_chat_id:
            lines = [f"⚠️ Таҳвили худкор барои фармоиши #{order.id} нашуд — лутфан санҷед ва дастӣ иҷро карда, 'Delivered'-ро занед:"]
            for o, r in results:
                reason = r.message if r is not None else "delivery provider raised NotImplementedError"
                lines.append(f"#{o.id} ({o.ff_player_id}): {reason}")
            await bot.send_message(config.admin_chat_id, "\n".join(lines)[:4000])
        if not is_terminal_failure:
            asyncio.create_task(_delayed_recheck(bot, order.id))
        return FulfillmentResult(order=order, auto_delivered=False)

    async with get_session() as session:
        fresh_order = await get_order(session, order_id)
    return FulfillmentResult(order=fresh_order, auto_delivered=True)


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

    await _finish_progress_bar(bot, order, order.progress_message_id)

    if len(delivered) > 1:
        summary = "\n".join(f"{_item_line(o, products[o.id])} → {o.ff_player_id}" for o in delivered)
        await bot.send_message(
            order.user_id,
            f"🎉 Ҳама маҳсулоти фармоиши шумо ирсол шуд:\n{summary}",
            reply_markup=receipt_keyboard(delivered[0].id),
        )
    else:
        product = products[delivered[0].id]
        await bot.send_message(
            order.user_id,
            f"🎉 {product.display_name} ба аккаунти шумо ({delivered[0].ff_player_id}) ирсол шуд!",
            reply_markup=receipt_keyboard(delivered[0].id),
        )
    await prompt_for_review(bot, delivered[0])
    return delivered[0]
