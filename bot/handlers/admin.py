from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import CallbackQuery, Message

from bot.config import config
from bot.db.models import OrderStatus
from bot.db.repo import get_order, get_product, list_orders_by_status, set_order_status
from bot.db.session import get_session
from bot.fsm_storage import storage
from bot.keyboards import admin_order_keyboard, review_prompt_keyboard
from bot.services.delivery import get_delivery_provider
from bot.states import OrderFlow

router = Router(name="admin")


def is_admin(user_id: int) -> bool:
    return user_id in config.admin_user_ids


async def _prompt_for_review(bot: Bot, order) -> None:
    """After delivery, ask the customer for a short review and remember
    which order it's for — customer.py posts it to the shop channel once
    they reply (or skip)."""
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


async def _reject_non_admin(message: Message) -> None:
    await message.answer(
        f"⛔ Шумо ҳамчун админ шинохта нашудед (ID-и шумо: {message.from_user.id}).\n"
        f"Дар Render, дар ADMIN_USER_IDS ҳамин рақамро илова кунед ва хидматро "
        f"аз нав деплой кунед."
    )


@router.message(Command("addproduct"))
async def add_product(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await _reject_non_admin(message)
        return
    # /addproduct <name> <diamonds> <price_somoni> <cost_somoni>
    parts = message.text.split(maxsplit=4)
    if len(parts) != 5:
        await message.answer(
            "Истифода: /addproduct <ном> <миқдори_алмаз> <нарх_фурӯш> <нарх_харид>\n"
            "Мисол: /addproduct Starter 100 10 8"
        )
        return

    from bot.db.models import Product

    _, name, diamonds, price, cost = parts
    try:
        product = Product(
            name=name,
            diamonds=int(diamonds),
            price_somoni=float(price),
            cost_somoni=float(cost),
        )
    except ValueError:
        await message.answer("Миқдор ва нарх бояд рақам бошанд.")
        return

    async with get_session() as session:
        session.add(product)
        await session.commit()
        await session.refresh(product)

    await message.answer(
        f"Маҳсулот сохта шуд: #{product.id} {product.name} — {product.diamonds}💎 "
        f"ба {product.price_somoni:.0f} сомонӣ (фоида {product.margin_somoni:.2f} сомонӣ)"
    )


@router.message(Command("products"))
async def list_products(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await _reject_non_admin(message)
        return
    from bot.db.repo import list_active_products

    async with get_session() as session:
        products = await list_active_products(session)

    if not products:
        await message.answer("Ягон маҳсулот нест. Бо /addproduct илова кунед.")
        return

    lines = [
        f"#{p.id} {p.name}: {p.diamonds}💎 = {p.price_somoni:.0f}с (харид {p.cost_somoni:.0f}с, фоида {p.margin_somoni:.2f}с)"
        for p in products
    ]
    await message.answer("\n".join(lines))


@router.message(Command("delproduct"))
async def delete_product(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await _reject_non_admin(message)
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip().isdigit():
        await message.answer("Истифода: /delproduct <ID>\nID-ро аз /products гиред.")
        return

    async with get_session() as session:
        product = await get_product(session, int(parts[1].strip()))
        if product is None:
            await message.answer("Маҳсулот ёфт нашуд.")
            return
        product.is_active = False
        await session.commit()

    await message.answer(f"Маҳсулот #{product.id} ({product.name}) хомӯш карда шуд.")


@router.message(Command("pending"))
async def pending_orders(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await _reject_non_admin(message)
        return

    async with get_session() as session:
        awaiting = await list_orders_by_status(session, OrderStatus.AWAITING_PAYMENT)
        paid = await list_orders_by_status(session, OrderStatus.PAID)

    if not awaiting and not paid:
        await message.answer("Фармоиши боқимонда нест.")
        return

    lines = ["⏳ Дар интизори пардохт:"]
    lines += [f"#{o.id} — {o.amount_somoni:.0f}с — player {o.ff_player_id}" for o in awaiting] or ["(нест)"]
    lines.append("\n💰 Пардохт шуда, дар интизори ирсол:")
    lines += [f"#{o.id} — {o.amount_somoni:.0f}с — player {o.ff_player_id}" for o in paid] or ["(нест)"]
    await message.answer("\n".join(lines))


@router.callback_query(F.data.startswith("admin:paid:"))
async def confirm_payment(callback: CallbackQuery, bot: Bot) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Танҳо админ метавонад ин корро кунад.", show_alert=True)
        return

    order_id = int(callback.data.split(":")[2])
    async with get_session() as session:
        order = await get_order(session, order_id)
        if order is None:
            await callback.answer("Фармоиш ёфт нашуд.", show_alert=True)
            return
        order = await set_order_status(session, order, OrderStatus.PAID)
        product = await get_product(session, order.product_id)

    await callback.message.edit_reply_markup(reply_markup=admin_order_keyboard(order))
    await bot.send_message(
        order.user_id,
        f"✅ Пардохти фармоиши #{order.id} тасдиқ шуд. {product.diamonds}💎 ба зудӣ ба ҳисоби шумо ирсол мешавад.",
    )

    delivery = get_delivery_provider()
    try:
        result = await delivery.deliver(order.id, order.ff_player_id, product.diamonds)
    except NotImplementedError:
        result = None

    if result and result.success:
        async with get_session() as session:
            order = await get_order(session, order_id)
            await set_order_status(session, order, OrderStatus.DELIVERED, payment_reference=result.reference)
        await bot.send_message(
            order.user_id,
            f"🎉 {product.diamonds}💎 ба аккаунти шумо (ID: {order.ff_player_id}) ирсол шуд!",
        )
        await _prompt_for_review(bot, order)
        await callback.answer("Автоматӣ ирсол шуд.")
    else:
        await callback.answer("Тасдиқ шуд. Лутфан алмазро дастӣ ирсол карда, 'Delivered' -ро зер кунед.")


@router.callback_query(F.data.startswith("admin:reject:"))
async def reject_payment(callback: CallbackQuery, bot: Bot) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Танҳо админ метавонад ин корро кунад.", show_alert=True)
        return

    order_id = int(callback.data.split(":")[2])
    async with get_session() as session:
        order = await get_order(session, order_id)
        if order is None:
            await callback.answer("Фармоиш ёфт нашуд.", show_alert=True)
            return
        order = await set_order_status(session, order, OrderStatus.CANCELLED)

    await callback.message.edit_reply_markup(reply_markup=None)
    await bot.send_message(
        order.user_id,
        f"❌ Фармоиши #{order.id} рад карда шуд. Агар ин хато бошад, бо админ тамос гиред.",
    )
    await callback.answer("Рад карда шуд.")


@router.callback_query(F.data.startswith("admin:delivered:"))
async def mark_delivered(callback: CallbackQuery, bot: Bot) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Танҳо админ метавонад ин корро кунад.", show_alert=True)
        return

    order_id = int(callback.data.split(":")[2])
    async with get_session() as session:
        order = await get_order(session, order_id)
        if order is None:
            await callback.answer("Фармоиш ёфт нашуд.", show_alert=True)
            return
        order = await set_order_status(session, order, OrderStatus.DELIVERED)
        product = await get_product(session, order.product_id)

    await callback.message.edit_reply_markup(reply_markup=None)
    await bot.send_message(
        order.user_id,
        f"🎉 {product.diamonds}💎 ба аккаунти шумо (ID: {order.ff_player_id}) ирсол шуд!",
    )
    await _prompt_for_review(bot, order)
    await callback.answer("Қайд шуд ҳамчун ирсолшуда.")
