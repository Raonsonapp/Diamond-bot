import uuid

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import config
from bot.db.models import Product, ProductCategory
from bot.db.repo import (
    accept_terms,
    count_referrals,
    count_total_delivered_orders,
    count_total_users,
    create_order,
    deduct_referral_balance,
    get_buyer_rank,
    get_last_recipient,
    get_product,
    get_user,
    get_user_purchase_stats,
    list_active_products,
    top_buyers,
    top_referrers,
    upsert_user,
)
from bot.db.session import get_session
from bot.keyboards import (
    admin_order_keyboard,
    back_to_menu_keyboard,
    cart_select_keyboard,
    confirm_order_keyboard,
    contact_keyboard,
    games_menu_keyboard,
    main_menu_keyboard,
    payment_link_keyboard,
    products_keyboard,
    profile_menu_keyboard,
    referral_menu_keyboard,
    reuse_recipient_keyboard,
    terms_keyboard,
)
from bot.services.payments import get_payment_provider
from bot.services.pricing import quote_custom_price
from bot.states import OrderFlow
from bot.texts import FAQ_TEXT, TERMS_TEXT

MIN_CUSTOM_UNITS = 10
MAX_CUSTOM_UNITS = 200_000

router = Router(name="customer")

WELCOME_TEXT = "Хуш омадед ба ALMAZ TJ! 💎\nМагазини фурӯши хидматҳои рақамӣ.\n\nЧиро интихоб мекунед?"


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

    lines = [f"#{o.id} — {o.amount_somoni:.2f} сомонӣ — {o.status.value}" for o in orders]
    return "📦 Фармоишҳои охирини шумо:\n" + "\n".join(lines)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    args = message.text.split(maxsplit=1)
    referred_by = None
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            referred_by = int(args[1].removeprefix("ref_"))
        except ValueError:
            referred_by = None

    async with get_session() as session:
        user = await upsert_user(
            session,
            message.from_user.id,
            message.from_user.username,
            message.from_user.full_name,
            referred_by=referred_by,
        )

    await state.clear()
    if user.accepted_terms_at is None:
        await message.answer(TERMS_TEXT, reply_markup=terms_keyboard())
        return

    await _show_main_menu(message, state)


@router.callback_query(F.data == "terms:accept")
async def accept_terms_cb(callback: CallbackQuery, state: FSMContext) -> None:
    async with get_session() as session:
        user = await get_user(session, callback.from_user.id)
        await accept_terms(session, user)

    await callback.message.edit_text(
        f"✅ Ташаккур! Шартнома қабул шуд.\n\n"
        f"👋 Хуш омадед, {callback.from_user.full_name}!\n"
        f"🆔 ID-и шумо: {callback.from_user.id}"
    )
    await _show_main_menu(callback.message, state)
    await callback.answer()


@router.callback_query(F.data == "menu:main")
async def menu_main(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(WELCOME_TEXT, reply_markup=main_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "menu:games")
async def menu_games(callback: CallbackQuery) -> None:
    await callback.message.edit_text("🎮 Бозиро интихоб кунед:", reply_markup=games_menu_keyboard())
    await callback.answer()


async def _open_catalog(callback: CallbackQuery, state: FSMContext, category: ProductCategory, title: str) -> None:
    async with get_session() as session:
        products = await list_active_products(session, category=category)

    if not products:
        await callback.message.edit_text(
            "Ҳозир маҳсулот дастрас нест. Лутфан баъдтар кӯшиш кунед ё бо админ тамос гиред.",
            reply_markup=back_to_menu_keyboard(),
        )
        await callback.answer()
        return

    await callback.message.edit_text(title, reply_markup=products_keyboard(products, category))
    await state.set_state(OrderFlow.choosing_product)
    await callback.answer()


@router.callback_query(F.data == "menu:buy_diamonds")
async def menu_buy_diamonds(callback: CallbackQuery, state: FSMContext) -> None:
    await _open_catalog(callback, state, ProductCategory.DIAMONDS, "💎 Бастаи алмази Free Fire-ро интихоб кунед:")


@router.callback_query(F.data == "menu:telegram")
async def menu_telegram(callback: CallbackQuery, state: FSMContext) -> None:
    await _open_catalog(callback, state, ProductCategory.TELEGRAM, "✈️ Бастаи Telegram Stars-ро интихоб кунед:")


@router.callback_query(F.data.regexp(r"^cartmode:(?!exit:).+$"))
async def enter_cart_mode(callback: CallbackQuery, state: FSMContext) -> None:
    category = ProductCategory(callback.data.split(":", 1)[1])
    async with get_session() as session:
        products = await list_active_products(session, category=category)

    await state.update_data(cart_category=category.value, cart_ids=[])
    await state.set_state(OrderFlow.choosing_cart)
    await callback.message.edit_text(
        "🛒 Бастаҳоеро, ки мехоҳед якҷоя харед, интихоб кунед (якчанд адад мумкин аст):",
        reply_markup=cart_select_keyboard(products, category, set()),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cartmode:exit:"))
async def exit_cart_mode(callback: CallbackQuery, state: FSMContext) -> None:
    category = ProductCategory(callback.data.split(":", 2)[2])
    title = (
        "💎 Бастаи алмази Free Fire-ро интихоб кунед:"
        if category == ProductCategory.DIAMONDS
        else "✈️ Бастаи Telegram Stars-ро интихоб кунед:"
    )
    await _open_catalog(callback, state, category, title)


@router.callback_query(OrderFlow.choosing_cart, F.data.startswith("cartitem:"))
async def toggle_cart_item(callback: CallbackQuery, state: FSMContext) -> None:
    product_id = int(callback.data.split(":", 1)[1])
    data = await state.get_data()
    category = ProductCategory(data["cart_category"])
    selected = set(data.get("cart_ids", []))

    if product_id in selected:
        selected.discard(product_id)
    else:
        selected.add(product_id)
    await state.update_data(cart_ids=list(selected))

    async with get_session() as session:
        products = await list_active_products(session, category=category)

    await callback.message.edit_reply_markup(reply_markup=cart_select_keyboard(products, category, selected))
    await callback.answer()


@router.callback_query(OrderFlow.choosing_cart, F.data == "cart:checkout")
async def cart_checkout(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    cart_ids = data.get("cart_ids", [])
    if not cart_ids:
        await callback.answer("Аввал ҳадди ақал як маҳсулот интихоб кунед.", show_alert=True)
        return

    category = ProductCategory(data["cart_category"])
    async with get_session() as session:
        products = [await get_product(session, pid) for pid in cart_ids]
        last_recipient = await get_last_recipient(session, callback.from_user.id, category)

    await state.update_data(cart_product_ids=cart_ids)
    await state.set_state(OrderFlow.entering_player_id)

    total = sum(p.price_somoni for p in products)
    summary = "\n".join(f"• {p.diamonds}{p.unit_label}" for p in products)
    prompt = await _recipient_prompt(category)
    text = f"Шумо интихоб кардед:\n{summary}\n\n💰 Ҳамагӣ: {total:.2f} сомонӣ.\n\nЛутфан {prompt} ирсол кунед:"

    if last_recipient:
        text += f"\n\nШумо пештар бо ин истифода карда будед: {last_recipient}"
        await callback.message.edit_text(text, reply_markup=reuse_recipient_keyboard(last_recipient))
    else:
        await callback.message.edit_text(text)
    await callback.answer()


@router.callback_query(F.data == "menu:contact")
async def menu_contact(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "📞 Тамос бо мо — тугмаро зер кунед, мустақим кушода мешавад:\n\n"
        "🛡 Бехатар · 🎧 Дастгирии 24/7 · ⏱ Дар 1-5 дақиқа",
        reply_markup=contact_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:faq")
async def menu_faq(callback: CallbackQuery) -> None:
    await callback.message.edit_text(FAQ_TEXT, reply_markup=back_to_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "menu:about")
async def menu_about(callback: CallbackQuery) -> None:
    async with get_session() as session:
        users_count = await count_total_users(session)
        orders_count = await count_total_delivered_orders(session)

    text = (
        "ℹ️ Дар бораи ALMAZ TJ\n\n"
        "🤖 Боти расмии фурӯши хидматҳои рақамӣ дар Тоҷикистон\n\n"
        "🎮 Хизматҳо: Free Fire diamonds, Telegram Stars\n"
        "🚀 Афзалиятҳо: суръати баланд (1-5 дақ.), бехатар\n\n"
        f"📊 Корбарон: {users_count} | Фармоишҳои иҷрошуда: {orders_count}\n\n"
        f"📢 Канал: {config.shop_channel_url}"
    )
    await callback.message.edit_text(text, reply_markup=back_to_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "menu:profile")
async def menu_profile(callback: CallbackQuery) -> None:
    async with get_session() as session:
        user = await get_user(session, callback.from_user.id)
        count, total = await get_user_purchase_stats(session, callback.from_user.id)

    text = (
        "👤 Профили шумо\n\n"
        f"👋 Ном: {callback.from_user.full_name}\n"
        f"🆔 ID: {callback.from_user.id}\n"
        f"📱 Username: @{callback.from_user.username or '—'}\n\n"
        "📊 Омори харид:\n"
        f"✅ Харидҳои муваффақ: {count}\n"
        f"💰 Маблағи умумии харид: {total:.2f} сомонӣ\n"
        f"🤝 Баланси реферал: {user.referral_balance:.2f} сомонӣ"
    )
    await callback.message.edit_text(text, reply_markup=profile_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "menu:referral")
async def menu_referral(callback: CallbackQuery) -> None:
    bot_user = await callback.bot.get_me()
    link = f"https://t.me/{bot_user.username}?start=ref_{callback.from_user.id}"

    async with get_session() as session:
        user = await get_user(session, callback.from_user.id)
        invited = await count_referrals(session, callback.from_user.id)

    text = (
        "🤝 Барномаи рефералӣ\n\n"
        f"🔗 Линки даъвати шумо:\n{link}\n\n"
        f"👥 Даъватшудагон: {invited} нафар\n"
        f"💰 Балансӣ рефералӣ: {user.referral_balance:.2f} сомонӣ\n\n"
        "🎁 Барои ҳар дӯсте, ки тавассути линки шумо ба бот ворид шуда, харидро анҷом медиҳад "
        "(ва он аз ҷониби админ тасдиқ мешавад), шумо 5% аз маблағи хариди ӯро ҳамчун бонус мегиред.\n\n"
        "💳 Бонуси ҷамъшуда ба балансии шумо илова мешавад ва метавонед онро барои пардохти "
        "харидҳо дар бот истифода баред."
    )
    await callback.message.edit_text(text, reply_markup=referral_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "menu:top_buyers")
async def menu_top_buyers(callback: CallbackQuery) -> None:
    medals = ["🥇", "🥈", "🥉"]
    async with get_session() as session:
        rows = await top_buyers(session, limit=10)
        rank = await get_buyer_rank(session, callback.from_user.id)

    lines = ["🏆 Топ харидорон\n"]
    for i, (user, count, total) in enumerate(rows):
        icon = medals[i] if i < 3 else f"{i + 1}."
        name = f"@{user.username}" if user.username else (user.full_name or f"ID{user.id}")
        lines.append(f"{icon} {name} — {count} харид · {total:.2f} сомонӣ")

    if rank:
        lines.append(f"\n👤 Шумо: {rank}-ҷой")

    await callback.message.edit_text("\n".join(lines), reply_markup=back_to_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "menu:top_referrers")
async def menu_top_referrers(callback: CallbackQuery) -> None:
    medals = ["🥇", "🥈", "🥉"]
    async with get_session() as session:
        rows = await top_referrers(session, limit=10)

    lines = ["🎖 Топ рефералдорон\n"]
    if not rows:
        lines.append("Ҳанӯз ҳеҷ кас дӯст даъват накардааст.")
    for i, (user, count) in enumerate(rows):
        icon = medals[i] if i < 3 else f"{i + 1}."
        name = f"@{user.username}" if user.username else (user.full_name or f"ID{user.id}")
        lines.append(f"{icon} {name} — {count} даъват")

    await callback.message.edit_text("\n".join(lines), reply_markup=back_to_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "menu:myorders")
async def menu_myorders(callback: CallbackQuery) -> None:
    text = await _format_orders_text(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=back_to_menu_keyboard())
    await callback.answer()


@router.callback_query(OrderFlow.choosing_product, F.data.startswith("product:custom:"))
async def choose_custom_amount(callback: CallbackQuery, state: FSMContext) -> None:
    category_value = callback.data.split(":", 2)[2]
    await state.update_data(custom_category=category_value)
    await state.set_state(OrderFlow.entering_custom_amount)
    unit = "алмаз" if category_value == ProductCategory.DIAMONDS.value else "Stars"
    await callback.message.edit_text(
        f"Чанд адад {unit} мехоҳед? Рақамро нависед, аз {MIN_CUSTOM_UNITS} то {MAX_CUSTOM_UNITS}:"
    )
    await callback.answer()


@router.message(OrderFlow.entering_custom_amount, F.text)
async def enter_custom_amount(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    if not text.isdigit():
        await message.answer("Лутфан танҳо рақам нависед, масалан 5000.")
        return

    amount = int(text)
    if not (MIN_CUSTOM_UNITS <= amount <= MAX_CUSTOM_UNITS):
        await message.answer(
            f"Миқдор бояд аз {MIN_CUSTOM_UNITS} то {MAX_CUSTOM_UNITS} бошад. Боз кӯшиш кунед:"
        )
        return

    data = await state.get_data()
    category = ProductCategory(data.get("custom_category", ProductCategory.DIAMONDS.value))

    async with get_session() as session:
        products = await list_active_products(session, category=category)
        if not products:
            await message.answer("Ҳозир нархгузорӣ дастрас нест. Бо админ тамос гиред.")
            await state.clear()
            return

        breakpoints = [(p.diamonds, p.price_somoni, p.cost_somoni) for p in products]
        price, cost = quote_custom_price(amount, breakpoints)

        custom_product = Product(
            name=f"Дилхоҳ — {amount}",
            category=category,
            diamonds=amount,
            price_somoni=price,
            cost_somoni=cost,
            is_active=False,
        )
        session.add(custom_product)
        await session.commit()
        await session.refresh(custom_product)

    await state.update_data(product_id=custom_product.id)
    await state.set_state(OrderFlow.entering_player_id)
    unit = custom_product.unit_label
    prompt = await _recipient_prompt(category)
    text = f"{amount} {unit} — {price:.2f} сомонӣ.\n\nЛутфан {prompt} ирсол кунед:"

    async with get_session() as session:
        last_recipient = await get_last_recipient(session, message.from_user.id, category)

    if last_recipient:
        text += f"\n\nШумо пештар бо ин истифода карда будед: {last_recipient}"
        await message.answer(text, reply_markup=reuse_recipient_keyboard(last_recipient))
    else:
        await message.answer(text)


async def _recipient_prompt(category: ProductCategory) -> str:
    return (
        "ID-и бозингари Free Fire-и худро (рақаме, ки дар профили худ мебинед)"
        if category == ProductCategory.DIAMONDS
        else "Username-и Telegram-и худро (бе @)"
    )


@router.callback_query(OrderFlow.choosing_product, F.data.regexp(r"^product:\d+$"))
async def choose_product(callback: CallbackQuery, state: FSMContext) -> None:
    product_id = int(callback.data.split(":", 1)[1])
    async with get_session() as session:
        product = await get_product(session, product_id)
        if product is not None:
            last_recipient = await get_last_recipient(session, callback.from_user.id, product.category)

    if product is None or not product.is_active:
        await callback.answer("Ин маҳсулот дастрас нест.", show_alert=True)
        return

    await state.update_data(product_id=product.id)
    await state.set_state(OrderFlow.entering_player_id)
    prompt = await _recipient_prompt(product.category)
    text = (
        f"Шумо интихоб кардед: {product.diamonds} {product.unit_label} — {product.price_somoni:.2f} сомонӣ.\n\n"
        f"Лутфан {prompt} ирсол кунед:"
    )
    if last_recipient:
        text += f"\n\nШумо пештар бо ин истифода карда будед: {last_recipient}"
        await callback.message.edit_text(text, reply_markup=reuse_recipient_keyboard(last_recipient))
    else:
        await callback.message.edit_text(text)
    await callback.answer()


async def _finalize_recipient(state: FSMContext, user_id: int, recipient: str, answer_target) -> None:
    """Shared by both the free-text ID/username entry and the "use my
    previous one" quick button — builds the confirmation screen. Handles
    both a single product (data["product_id"]) and a multi-pack cart
    checkout (data["cart_product_ids"])."""
    data = await state.get_data()
    cart_ids = data.get("cart_product_ids")

    async with get_session() as session:
        user = await get_user(session, user_id)
        if cart_ids:
            products = [await get_product(session, pid) for pid in cart_ids]
        else:
            products = [await get_product(session, data["product_id"])]

    await state.update_data(ff_player_id=recipient)
    await state.set_state(OrderFlow.confirming)

    total_price = sum(p.price_somoni for p in products)
    offer_balance = user is not None and user.referral_balance >= total_price > 0
    category = products[0].category

    recipient_label = "ID-и бозингар" if category == ProductCategory.DIAMONDS else "Username"
    confirm_lines = ["Тасдиқ кунед:\n"]
    for p in products:
        bonus = f" (+{p.bonus_diamonds} бонус)" if p.bonus_diamonds else ""
        confirm_lines.append(f"📦 {p.diamonds}{bonus} {p.unit_label} — {p.price_somoni:.2f} сомонӣ")
    if len(products) > 1:
        confirm_lines.append(f"💰 Ҳамагӣ: {total_price:.2f} сомонӣ")
    confirm_lines.append(f"🎮 {recipient_label}: {recipient}")

    if category == ProductCategory.DIAMONDS:
        mapped = next((p for p in products if p.fzr_category_id), None)
        if mapped:
            player_name = await _try_validate_player_id(mapped.fzr_category_id, recipient)
            if player_name:
                confirm_lines.append(f"✅ Ном дар бозӣ: {player_name}")

    confirm_lines.append("\nҲама дуруст аст?")
    await answer_target.answer(
        "\n".join(confirm_lines),
        reply_markup=confirm_order_keyboard(offer_balance_payment=offer_balance),
    )


@router.message(OrderFlow.entering_player_id, F.text)
async def enter_player_id(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    cart_ids = data.get("cart_product_ids")
    async with get_session() as session:
        product = await get_product(session, cart_ids[0] if cart_ids else data["product_id"])

    recipient = message.text.strip()

    if product.category == ProductCategory.DIAMONDS:
        if not recipient.isdigit() or not (5 <= len(recipient) <= 15):
            await message.answer("ID-и нодуруст. Лутфан танҳо рақамҳои ID-и бозингари Free Fire-ро ворид кунед.")
            return
    else:
        recipient = recipient.removeprefix("@")
        if not (5 <= len(recipient) <= 32) or not recipient.replace("_", "").isalnum():
            await message.answer("Username-и нодуруст. Лутфан username-и дурусти Telegram-ро (бе @) нависед.")
            return

    await _finalize_recipient(state, message.from_user.id, recipient, message)


@router.callback_query(OrderFlow.entering_player_id, F.data.startswith("reuseid:"))
async def reuse_saved_id(callback: CallbackQuery, state: FSMContext) -> None:
    recipient = callback.data.split(":", 1)[1]
    await _finalize_recipient(state, callback.from_user.id, recipient, callback.message)
    await callback.answer()


async def _try_validate_player_id(fzr_category_id: str, player_id: str) -> str | None:
    """Best-effort player-ID check via FazerCards. Returns the confirmed
    in-game name, or None if unsupported/unavailable/couldn't confirm —
    callers must treat None as "couldn't verify", never as "definitely
    wrong", since this must not block a purchase on its own.

    /api/v2/topups/validate-id uses its own category namespace, separate
    from the /api/v2/topups (offers/order) one — it lists one entry per
    *game* ("free_fire"), not per regional top-up SKU ("free_fire_cis",
    "free_fire_bd", ...). Player-ID validation is the same Garena lookup
    regardless of which regional SKU is used to actually deliver the
    diamonds, so match by game family (prefix) and call validate-id with
    *its own* category_id — never the product's fzr_category_id, which
    that endpoint doesn't recognise (confirmed: it 404s as "unknown
    category_id" there)."""
    from bot.services.delivery import guess_id_field_key
    from bot.services.fazercards import FazerCardsError, list_validate_id_categories, validate_player_id

    try:
        supported = await list_validate_id_categories()
    except FazerCardsError:
        return None

    item = next(
        (
            i
            for i in supported.get("items", [])
            if i.get("category_id")
            and (
                fzr_category_id == i["category_id"]
                or fzr_category_id.startswith(i["category_id"] + "_")
            )
        ),
        None,
    )
    if item is None:
        return None

    field_key = guess_id_field_key(item.get("fields", []))
    if field_key is None:
        return None

    try:
        result = await validate_player_id(item["category_id"], {field_key: player_id})
    except FazerCardsError:
        return None

    return result.get("player_name") if result.get("valid") else None


@router.callback_query(OrderFlow.confirming, F.data == "order:cancel")
async def cancel_order(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Фармоиш бекор карда шуд.")
    await callback.answer()


async def _cart_products(session, data: dict) -> list[Product]:
    cart_ids = data.get("cart_product_ids")
    if cart_ids:
        return [await get_product(session, pid) for pid in cart_ids]
    return [await get_product(session, data["product_id"])]


@router.callback_query(OrderFlow.confirming, F.data == "order:pay_balance")
async def pay_with_balance(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()

    async with get_session() as session:
        products = await _cart_products(session, data)
        total = sum(p.price_somoni for p in products)
        user = await get_user(session, callback.from_user.id)

        if user is None or user.referral_balance < total:
            await callback.answer("Баланси реферал кофӣ нест.", show_alert=True)
            return

        await deduct_referral_balance(session, user, total)
        group_id = uuid.uuid4().hex if len(products) > 1 else None
        orders = [
            await create_order(
                session,
                user_id=callback.from_user.id,
                product=p,
                ff_player_id=data["ff_player_id"],
                payment_provider="referral_balance",
                paid_with_referral_balance=True,
                cart_group_id=group_id,
            )
            for p in products
        ]

    primary = orders[0]
    summary = "\n".join(f"📦 {p.diamonds}{p.unit_label} — {p.price_somoni:.2f} сомонӣ" for p in products)
    if config.admin_chat_id:
        await callback.bot.send_message(
            config.admin_chat_id,
            f"🆕 Фармоиши #{primary.id} (пардохт аз баланси реферал — тасдиқшуда)\n"
            f"👤 Мизоҷ: {callback.from_user.full_name} (@{callback.from_user.username or '—'}, id={callback.from_user.id})\n"
            f"{summary}\n"
            f"🎮 {primary.ff_player_id}\n\n"
            f"Лутфан иҷро карда, 'Delivered'-ро зер кунед.",
            reply_markup=admin_order_keyboard(primary),
        )

    await state.clear()
    await callback.message.edit_text(
        f"✅ Фармоиши #{primary.id} бо баланси реферал пардохт шуд!\n"
        f"{summary}\n\nДар 1-5 дақиқа ба шумо мерасад."
    )
    await callback.answer()


@router.callback_query(OrderFlow.confirming, F.data == "order:confirm")
async def confirm_order(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    provider = get_payment_provider()

    async with get_session() as session:
        products = await _cart_products(session, data)
        group_id = uuid.uuid4().hex if len(products) > 1 else None
        orders = [
            await create_order(
                session,
                user_id=callback.from_user.id,
                product=p,
                ff_player_id=data["ff_player_id"],
                payment_provider=config.payment_provider,
                cart_group_id=group_id,
            )
            for p in products
        ]
        primary = orders[0]
        total = sum(o.amount_somoni for o in orders)
        if len(orders) > 1:
            # One payment covers the whole cart — the total lives on the
            # primary order so SMS/manual confirmation matches on it;
            # siblings carry 0 so stats aren't double-counted.
            primary.amount_somoni = total
            for o in orders[1:]:
                o.amount_somoni = 0.0
            await session.commit()
            await session.refresh(primary)

    invoice = await provider.create_invoice(primary.id, total)

    await state.update_data(order_id=primary.id)
    await state.set_state(OrderFlow.awaiting_payment_proof)
    text = f"Фармоиши #{primary.id} сабт шуд.\n\n{invoice.instructions}"
    if invoice.pay_url:
        await callback.message.edit_text(text, reply_markup=payment_link_keyboard(invoice.pay_url))
    else:
        await callback.message.edit_text(text)
    await callback.answer()


@router.message(OrderFlow.awaiting_payment_proof)
async def receive_payment_proof(message: Message, state: FSMContext) -> None:
    import hashlib

    from bot.db.repo import find_duplicate_proof, get_order, get_orders_by_group, set_payment_proof_hash

    data = await state.get_data()
    order_id = data.get("order_id")
    if not order_id:
        return

    async with get_session() as session:
        order = await get_order(session, order_id)
        group = await get_orders_by_group(session, order.cart_group_id) if order.cart_group_id else [order]
        items_summary = ""
        if len(group) > 1:
            products = [await get_product(session, o.product_id) for o in group]
            items_summary = "\n" + "\n".join(
                f"📦 {p.diamonds}{p.unit_label} — {o.amount_somoni:.2f} сомонӣ" if o.amount_somoni else f"📦 {p.diamonds}{p.unit_label}"
                for o, p in zip(group, products)
            )

    caption = (
        f"🆕 Фармоиши #{order_id}{items_summary}\n"
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
        "Ташаккур! Расиди шумо ба админ фиристода шуд. Пас аз тасдиқ маҳсулоти шумо ирсол мешавад."
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
