from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import config
from bot.db.models import Product
from bot.db.repo import create_order, get_product, list_active_products, upsert_user
from bot.db.session import get_session
from bot.keyboards import (
    admin_order_keyboard,
    back_to_menu_keyboard,
    confirm_order_keyboard,
    contact_keyboard,
    main_menu_keyboard,
    products_keyboard,
)
from bot.services.payments import get_payment_provider
from bot.services.pricing import quote_custom_price
from bot.states import OrderFlow

MIN_CUSTOM_DIAMONDS = 10
MAX_CUSTOM_DIAMONDS = 200_000

router = Router(name="customer")

WELCOME_TEXT = "Хуш омадед ба ALMAZ TJ! 💎\nМагазини фурӯши алмази Free Fire.\n\nЧиро интихоб мекунед?"


async def _show_main_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(WELCOME_TEXT, reply_markup=main_menu_keyboard())


async def _format_orders_text(user_id: int) -> str:
    from sqlalchemy import select

    from bot.db.models import Order

    async with get_session() as session:
        result = await session.execute(
            select(Order).where(Order.user_id == user_id).order_by(Order.created_at.desc()).limit(10)
        )
        orders = list(result.scalars().all())

    if not orders:
        return "Шумо то ҳол фармоише надоред."

    lines = [f"#{o.id} — {o.amount_somoni:.0f} сомонӣ — {o.status.value}" for o in orders]
    return "📦 Фармоишҳои охирини шумо:\n" + "\n".join(lines)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    async with get_session() as session:
        await upsert_user(session, message.from_user.id, message.from_user.username, message.from_user.full_name)
    await _show_main_menu(message, state)


@router.callback_query(F.data == "menu:main")
async def menu_main(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(WELCOME_TEXT, reply_markup=main_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "menu:buy")
async def menu_buy(callback: CallbackQuery, state: FSMContext) -> None:
    async with get_session() as session:
        products = await list_active_products(session)

    if not products:
        await callback.message.edit_text(
            "Ҳозир маҳсулот дастрас нест. Лутфан баъдтар кӯшиш кунед ё бо админ тамос гиред.",
            reply_markup=back_to_menu_keyboard(),
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        "💎 Бастаи алмази Free Fire-ро интихоб кунед:",
        reply_markup=products_keyboard(products),
    )
    await state.set_state(OrderFlow.choosing_product)
    await callback.answer()


@router.callback_query(F.data == "menu:contact")
async def menu_contact(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "📞 Тамос бо мо — тугмаро зер кунед, мустақим кушода мешавад:\n\n"
        "🛡 Бехатар · 🎧 Дастгирии 24/7 · ⏱ Дар 1-5 дақиқа",
        reply_markup=contact_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:myorders")
async def menu_myorders(callback: CallbackQuery) -> None:
    text = await _format_orders_text(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=back_to_menu_keyboard())
    await callback.answer()


@router.callback_query(OrderFlow.choosing_product, F.data == "product:custom")
async def choose_custom_amount(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(OrderFlow.entering_custom_amount)
    await callback.message.edit_text(
        f"Чанд адад алмаз мехоҳед? Рақамро нависед (масалан 5000), аз {MIN_CUSTOM_DIAMONDS} то {MAX_CUSTOM_DIAMONDS}:"
    )
    await callback.answer()


@router.message(OrderFlow.entering_custom_amount, F.text)
async def enter_custom_amount(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    if not text.isdigit():
        await message.answer("Лутфан танҳо рақам нависед, масалан 5000.")
        return

    diamonds = int(text)
    if not (MIN_CUSTOM_DIAMONDS <= diamonds <= MAX_CUSTOM_DIAMONDS):
        await message.answer(
            f"Миқдор бояд аз {MIN_CUSTOM_DIAMONDS} то {MAX_CUSTOM_DIAMONDS} бошад. Боз кӯшиш кунед:"
        )
        return

    async with get_session() as session:
        products = await list_active_products(session)
        if not products:
            await message.answer("Ҳозир нархгузорӣ дастрас нест. Бо админ тамос гиред.")
            await state.clear()
            return

        breakpoints = [(p.diamonds, p.price_somoni, p.cost_somoni) for p in products]
        price, cost = quote_custom_price(diamonds, breakpoints)

        custom_product = Product(
            name=f"Дилхоҳ — {diamonds}",
            diamonds=diamonds,
            price_somoni=price,
            cost_somoni=cost,
            is_active=False,
        )
        session.add(custom_product)
        await session.commit()
        await session.refresh(custom_product)

    await state.update_data(product_id=custom_product.id)
    await state.set_state(OrderFlow.entering_player_id)
    await message.answer(
        f"💎 {diamonds} — {price:.0f} сомонӣ.\n\n"
        f"Лутфан ID-и бозингари Free Fire-и худро ирсол кунед (рақаме, ки дар профили худ мебинед):"
    )


@router.callback_query(OrderFlow.choosing_product, F.data.regexp(r"^product:\d+$"))
async def choose_product(callback: CallbackQuery, state: FSMContext) -> None:
    product_id = int(callback.data.split(":", 1)[1])
    async with get_session() as session:
        product = await get_product(session, product_id)

    if product is None or not product.is_active:
        await callback.answer("Ин маҳсулот дастрас нест.", show_alert=True)
        return

    await state.update_data(product_id=product.id)
    await state.set_state(OrderFlow.entering_player_id)
    await callback.message.edit_text(
        f"Шумо интихоб кардед: {product.diamonds} 💎 — {product.price_somoni:.0f} сомонӣ.\n\n"
        f"Лутфан ID-и бозингари Free Fire-и худро ирсол кунед (рақаме, ки дар профили худ мебинед):"
    )
    await callback.answer()


@router.message(OrderFlow.entering_player_id, F.text)
async def enter_player_id(message: Message, state: FSMContext) -> None:
    player_id = message.text.strip()
    if not player_id.isdigit() or not (5 <= len(player_id) <= 15):
        await message.answer("ID-и нодуруст. Лутфан танҳо рақамҳои ID-и бозингари Free Fire-ро ворид кунед.")
        return

    data = await state.get_data()
    async with get_session() as session:
        product = await get_product(session, data["product_id"])

    await state.update_data(ff_player_id=player_id)
    await state.set_state(OrderFlow.confirming)
    await message.answer(
        f"Тасдиқ кунед:\n\n"
        f"📦 Маҳсулот: {product.diamonds} 💎\n"
        f"💰 Нарх: {product.price_somoni:.0f} сомонӣ\n"
        f"🎮 ID-и бозингар: {player_id}\n\n"
        f"Ҳама дуруст аст?",
        reply_markup=confirm_order_keyboard(),
    )


@router.callback_query(OrderFlow.confirming, F.data == "order:cancel")
async def cancel_order(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Фармоиш бекор карда шуд.")
    await callback.answer()


@router.callback_query(OrderFlow.confirming, F.data == "order:confirm")
async def confirm_order(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    provider = get_payment_provider()

    async with get_session() as session:
        product = await get_product(session, data["product_id"])
        order = await create_order(
            session,
            user_id=callback.from_user.id,
            product=product,
            ff_player_id=data["ff_player_id"],
            payment_provider=config.payment_provider,
        )

    invoice = await provider.create_invoice(order.id, order.amount_somoni)

    await state.update_data(order_id=order.id)
    await state.set_state(OrderFlow.awaiting_payment_proof)
    await callback.message.edit_text(
        f"Фармоиши #{order.id} сабт шуд.\n\n{invoice.instructions}"
    )
    await callback.answer()


@router.message(OrderFlow.awaiting_payment_proof)
async def receive_payment_proof(message: Message, state: FSMContext) -> None:
    import hashlib

    from bot.db.repo import find_duplicate_proof, get_order, set_payment_proof_hash

    data = await state.get_data()
    order_id = data.get("order_id")
    if not order_id:
        return

    caption = (
        f"🆕 Фармоиши #{order_id}\n"
        f"👤 Мизоҷ: {message.from_user.full_name} (@{message.from_user.username or '—'}, id={message.from_user.id})\n"
        f"Расиди пардохт замима шуд.\n\n"
        f"❗️ Пеш аз тасдиқ, ҳатман дар аппи бонки худ маблағи воқеиро санҷед — расм танҳо кофӣ нест."
    )

    async with get_session() as session:
        order = await get_order(session, order_id)

        if message.photo:
            file_bytes = await message.bot.download(message.photo[-1].file_id)
            proof_hash = hashlib.sha256(file_bytes.read()).hexdigest()
            duplicate = await find_duplicate_proof(session, proof_hash, order_id)
            order = await set_payment_proof_hash(session, order, proof_hash)
            if duplicate is not None:
                caption = (
                    f"⚠️⚠️ ДИҚҚАТ: ҳамин расм қаблан барои фармоиши #{duplicate.id} "
                    f"истифода шуда буд! Эҳтимоли фиреб — бодиққат санҷед.\n\n{caption}"
                )

    if config.admin_chat_id:
        if message.photo:
            await message.bot.send_photo(
                config.admin_chat_id,
                photo=message.photo[-1].file_id,
                caption=caption,
                reply_markup=admin_order_keyboard(order),
            )
        else:
            await message.bot.send_message(
                config.admin_chat_id,
                f"{caption}\n\nМатн: {message.text or '(бе матн)'}",
                reply_markup=admin_order_keyboard(order),
            )

    await message.answer(
        "Ташаккур! Расиди шумо ба админ фиристода шуд. Пас аз тасдиқ 💎-и шумо ирсол мешавад."
    )
    await state.clear()


@router.message(OrderFlow.awaiting_review, F.text)
async def receive_review(message: Message, state: FSMContext) -> None:
    from bot.db.repo import get_order
    from bot.services.announcements import post_review_announcement

    data = await state.get_data()
    order_id = data.get("order_id")
    await state.clear()
    if not order_id:
        return

    async with get_session() as session:
        order = await get_order(session, order_id)
        product = await get_product(session, order.product_id)
        await post_review_announcement(message.bot, session, order, product, message.text.strip())

    await message.answer("Ташаккур барои шарҳи шумо! 🙏")


@router.callback_query(F.data.startswith("review:skip:"))
async def skip_review(callback: CallbackQuery, state: FSMContext) -> None:
    from bot.db.repo import get_order
    from bot.services.announcements import post_review_announcement

    order_id = int(callback.data.split(":")[2])
    await state.clear()

    async with get_session() as session:
        order = await get_order(session, order_id)
        product = await get_product(session, order.product_id)
        await post_review_announcement(callback.bot, session, order, product, None)

    await callback.message.edit_text("Хуб, ташаккур барои харид! 🙏")
    await callback.answer()


@router.message(Command("myorders"))
async def my_orders(message: Message) -> None:
    text = await _format_orders_text(message.from_user.id)
    await message.answer(text)
