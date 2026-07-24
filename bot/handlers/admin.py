import re

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.config import config
from bot.db.models import OrderStatus, Product, ProductCategory
from bot.db.repo import (
    get_order,
    get_orders_by_group,
    get_product,
    list_active_products,
    list_orders_by_status,
    set_order_status,
    set_product_bonus,
    set_product_fzr_mapping,
    set_product_price,
)
from bot.db.session import get_session
from bot.keyboards import admin_order_keyboard
from bot.services.fulfillment import confirm_and_deliver, mark_delivered_and_notify

router = Router(name="admin")


def is_admin(user_id: int) -> bool:
    return user_id in config.admin_user_ids


async def _reject_non_admin(message: Message) -> None:
    await message.answer(
        f"⛔ Шумо ҳамчун админ шинохта нашудед (ID-и шумо: {message.from_user.id}).\n"
        f"Дар Render, дар ADMIN_USER_IDS ҳамин рақамро илова кунед ва хидматро "
        f"аз нав деплой кунед."
    )


async def _add_product(message: Message, category: ProductCategory, usage_example: str) -> None:
    if not is_admin(message.from_user.id):
        await _reject_non_admin(message)
        return

    # The name can contain spaces ("Ваучери лайт"), so take the last three
    # tokens as amount/price/cost and everything between the command and
    # those as the name, rather than a fixed maxsplit that breaks multi-word
    # names.
    parts = message.text.split()
    if len(parts) < 5:
        await message.answer(
            f"Истифода: {usage_example} <ном> <миқдор> <нарх_фурӯш> <нарх_харид>\n"
            f"Мисол: {usage_example} Starter 100 10 8"
        )
        return

    name = " ".join(parts[1:-3])
    amount, price, cost = parts[-3:]
    try:
        product = Product(
            name=name,
            category=category,
            diamonds=int(amount),
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
        f"Маҳсулот сохта шуд: #{product.id} {product.name} — {product.diamonds}{product.unit_label} "
        f"ба {product.price_somoni:.2f} сомонӣ (фоида {product.margin_somoni:.2f} сомонӣ)"
    )


@router.message(Command("addproduct"))
async def add_product(message: Message) -> None:
    await _add_product(message, ProductCategory.DIAMONDS, "/addproduct")


@router.message(Command("addstars"))
async def add_stars(message: Message) -> None:
    await _add_product(message, ProductCategory.TELEGRAM, "/addstars")


@router.message(Command("products"))
async def list_products(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await _reject_non_admin(message)
        return

    async with get_session() as session:
        products = await list_active_products(session)

    if not products:
        await message.answer("Ягон маҳсулот нест. Бо /addproduct ё /addstars илова кунед.")
        return

    lines = [
        f"#{p.id} [{p.category.value}] {p.name}: {p.diamonds}"
        + (f"(+{p.bonus_diamonds})" if p.bonus_diamonds else "")
        + f"{p.unit_label} = {p.price_somoni:.2f}с (харид {p.cost_somoni:.2f}с, фоида {p.margin_somoni:.2f}с)"
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
    lines += [f"#{o.id} — {o.amount_somoni:.2f}с — recipient {o.ff_player_id}" for o in awaiting] or ["(нест)"]
    lines.append("\n💰 Пардохт шуда, дар интизори ирсол:")
    lines += [f"#{o.id} — {o.amount_somoni:.2f}с — recipient {o.ff_player_id}" for o in paid] or ["(нест)"]
    await message.answer("\n".join(lines))


@router.callback_query(F.data.startswith("admin:paid:"))
async def confirm_payment(callback: CallbackQuery, bot: Bot) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Танҳо админ метавонад ин корро кунад.", show_alert=True)
        return

    order_id = int(callback.data.split(":")[2])
    result = await confirm_and_deliver(bot, order_id)
    if result is None:
        await callback.answer("Фармоиш ёфт нашуд.", show_alert=True)
        return

    await callback.message.edit_reply_markup(reply_markup=admin_order_keyboard(result.order))
    if result.auto_delivered:
        await callback.answer("Автоматӣ ирсол шуд.")
    else:
        await callback.answer("Тасдиқ шуд. Лутфан дастӣ ирсол карда, 'Delivered' -ро зер кунед.")


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
        group = await get_orders_by_group(session, order.cart_group_id) if order.cart_group_id else [order]
        for o in group:
            await set_order_status(session, o, OrderStatus.CANCELLED)

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
    order = await mark_delivered_and_notify(bot, order_id)
    if order is None:
        await callback.answer("Фармоиш ёфт нашуд.", show_alert=True)
        return

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Қайд шуд ҳамчун ирсолшуда.")


@router.message(Command("fzr_categories"))
async def fzr_categories(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await _reject_non_admin(message)
        return

    from bot.services.fazercards import FazerCardsError, list_topup_categories

    query = message.text.split(maxsplit=1)
    search = query[1].strip().lower() if len(query) > 1 else None

    try:
        data = await list_topup_categories(limit=500)
    except FazerCardsError as exc:
        await message.answer(f"⚠️ Хатои FazerCards: {exc}")
        return

    items = data.get("items", [])
    if search:
        items = [i for i in items if search in (i.get("name") or "").lower()]

    if not items:
        await message.answer("Ягон категория ёфт нашуд. Истифода: /fzr_categories free fire")
        return

    lines = [f"{i['category_id']} — {i.get('name', '?')}" for i in items[:40]]
    suffix = f"\n\n(... ва {len(items) - 40} дигар, ҷустуҷӯро дақиқтар кунед)" if len(items) > 40 else ""
    await message.answer(
        f"Категорияҳо (Total: {data.get('meta', {}).get('total', len(items))}):\n" + "\n".join(lines) + suffix
    )


@router.message(Command("fzr_offers"))
async def fzr_offers(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await _reject_non_admin(message)
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Истифода: /fzr_offers <category_id>\ncategory_id-ро аз /fzr_categories гиред.")
        return

    from bot.services.fazercards import FazerCardsError, get_topup_offers

    try:
        data = await get_topup_offers(parts[1].strip())
    except FazerCardsError as exc:
        await message.answer(f"⚠️ Хатои FazerCards: {exc}")
        return

    offers = data.get("offers", [])
    offer_lines = [f"{o['offer_id']} — {o.get('name', '?')} — ${o.get('price_usd', '?')}" for o in offers]
    fields = data.get("fields", [])
    field_lines = [f"  key={f.get('key')} label={f.get('label')} type={f.get('type')}" for f in fields]

    text = (
        f"📦 {data.get('name', '?')} ({parts[1].strip()})\n\n"
        f"Offers:\n" + "\n".join(offer_lines[:40]) + "\n\n"
        f"Fields (барои /mapproduct лозим нест, худкор муайян мешавад):\n" + "\n".join(field_lines)
    )
    await message.answer(text[:4000])


@router.message(Command("fzr_validate_id"))
async def fzr_validate_id(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await _reject_non_admin(message)
        return

    from bot.services.fazercards import FazerCardsError, list_validate_id_categories

    try:
        data = await list_validate_id_categories()
    except FazerCardsError as exc:
        await message.answer(f"⚠️ Хатои FazerCards: {exc}")
        return

    items = data.get("items", [])
    lines = [f"{i['category_id']} — {i.get('name', '?')}" for i in items[:60]]
    await message.answer("Бозиҳое, ки санҷиши ID доранд:\n" + "\n".join(lines) if lines else "Рӯйхат холист.")


def _batch_lines(message_text: str, command: str) -> list[str]:
    """A pasted block of several "/command ..." lines arrives from Telegram
    as ONE message (there's no client-side way to split it into separate
    updates), so a handler that only looks at the first line silently
    drops the rest with no error — the exact "nothing happened" confusion
    that kept coming up. Instead, treat every non-empty line that starts
    with this command as its own instance to process, and fall back to
    the whole text as a single line for a normal one-line invocation."""
    lines = [ln.strip() for ln in message_text.splitlines() if ln.strip()]
    matching = [ln for ln in lines if ln.split(maxsplit=1)[0].split("@")[0].lower() == command]
    return matching or lines[:1]


@router.message(Command("mapproduct"))
async def map_product(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await _reject_non_admin(message)
        return

    lines = _batch_lines(message.text, "/mapproduct")
    processed = 0
    for line in lines:
        parts = line.split(maxsplit=3)
        if len(parts) != 4:
            await message.answer(f"⚠️ Формат нодуруст: {line}")
            processed += 1
            continue

        _, product_id_str, category_id, offer_id = parts
        if not product_id_str.isdigit():
            await message.answer(f"⚠️ product_id бояд рақам бошад: {line}")
            processed += 1
            continue

        # Each line makes a live call to FazerCards for the bonus lookup —
        # with several lines in one message that's several sequential HTTP
        # calls in one handler run. Report on each line as soon as it's
        # done (not all at the end) and never let one bad/slow line take
        # down the rest of the batch silently.
        try:
            async with get_session() as session:
                product = await get_product(session, int(product_id_str))
                if product is None:
                    await message.answer(f"⚠️ Маҳсулот #{product_id_str} ёфт нашуд.")
                    processed += 1
                    continue
                product = await set_product_fzr_mapping(session, product, category_id, offer_id)

                bonus_note = ""
                bonus = await _guess_bonus_from_offer(category_id, offer_id, product.diamonds)
                if bonus is not None:
                    product = await set_product_bonus(session, product, bonus)
                    bonus_note = (
                        f", бонус +{bonus} (ҳамагӣ {product.total_diamonds}{product.unit_label})"
                        if bonus > 0
                        else ""
                    )

            await message.answer(f"✅ #{product.id} ({product.name}) → {category_id}/{offer_id}{bonus_note}")
        except Exception as exc:  # noqa: BLE001 — one bad line must not sink the batch
            await message.answer(f"⚠️ Хатои ногаҳонӣ дар «{line}»: {exc}")
        processed += 1

    if processed == 0:
        await message.answer(
            "Истифода: /mapproduct <product_id> <fzr_category_id> <fzr_offer_id>\n"
            "category_id ва offer_id-ро аз /fzr_categories ва /fzr_offers гиред.\n"
            "(метавонед якчанд сатрро дар як паём фиристед)"
        )
        return

    await message.answer(
        "Барои фаъол кардани ирсоли худкор, дар Render DELIVERY_PROVIDER=fazercards гузоред.\n"
        "Агар бонус нодуруст бошад: /setbonus <product_id> <бонус>"
    )


async def _guess_bonus_from_offer(category_id: str, offer_id: str, nominal_diamonds: int) -> int | None:
    """FazerCards offer names/ids usually encode the *total* delivered
    amount (e.g. "110_diamonds" for a nominal 100-pack, a 10% bonus) — read
    it back so the bot can advertise the same bonus the supplier's own site
    shows. Returns None (leave bonus untouched) if nothing parseable was
    found, so a weird offer_id never overwrites a manually-set bonus with 0."""
    from bot.services.fazercards import FazerCardsError, get_topup_offers

    try:
        data = await get_topup_offers(category_id)
    except FazerCardsError:
        return None

    offer = next((o for o in data.get("offers", []) if o.get("offer_id") == offer_id), None)
    if offer is None:
        return None

    for source in (offer.get("offer_id", ""), offer.get("name", "")):
        match = re.search(r"\d+", source)
        if match:
            total = int(match.group())
            if total > nominal_diamonds:
                return total - nominal_diamonds
            return 0
    return None


@router.message(Command("setbonus"))
async def set_bonus(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await _reject_non_admin(message)
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) != 3 or not parts[2].lstrip("-").isdigit():
        await message.answer("Истифода: /setbonus <product_id> <бонус_диамонд>\nМисол: /setbonus 1 10")
        return

    if not parts[1].isdigit():
        await message.answer("product_id бояд рақам бошад.")
        return

    async with get_session() as session:
        product = await get_product(session, int(parts[1]))
        if product is None:
            await message.answer("Маҳсулот ёфт нашуд.")
            return
        product = await set_product_bonus(session, product, int(parts[2]))

    await message.answer(
        f"✅ Маҳсулот #{product.id} ({product.name}): бонус = +{product.bonus_diamonds} "
        f"(ҳамагӣ {product.total_diamonds}{product.unit_label})."
    )


@router.message(Command("setprice"))
async def set_price(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await _reject_non_admin(message)
        return

    reports = []
    for line in _batch_lines(message.text, "/setprice"):
        parts = line.split(maxsplit=3)
        if len(parts) not in (3, 4):
            reports.append(f"⚠️ Формат нодуруст: {line}")
            continue

        if not parts[1].isdigit():
            reports.append(f"⚠️ product_id бояд рақам бошад: {line}")
            continue

        try:
            price = float(parts[2])
            cost = float(parts[3]) if len(parts) == 4 else None
        except ValueError:
            reports.append(f"⚠️ Нарх бояд рақам бошад: {line}")
            continue

        async with get_session() as session:
            product = await get_product(session, int(parts[1]))
            if product is None:
                reports.append(f"⚠️ Маҳсулот #{parts[1]} ёфт нашуд.")
                continue
            product = await set_product_price(session, product, price, cost)

        reports.append(
            f"✅ #{product.id} ({product.name}): нарх={product.price_somoni:.2f}с"
            + (f", харид={product.cost_somoni:.2f}с" if cost is not None else "")
            + f", фоида={product.margin_somoni:.2f}с"
        )

    if not reports:
        await message.answer(
            "Истифода: /setprice <product_id> <нархи_фурӯш> [нархи_харид]\n"
            "Мисол: /setprice 1 8.90\nМисол бо нархи харид: /setprice 1 8.90 7.21\n"
            "(метавонед якчанд сатрро дар як паём фиристед)"
        )
        return

    await message.answer("\n".join(reports))
