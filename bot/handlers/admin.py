import asyncio
import re

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.config import config
from bot.db.models import OrderStatus, Product, ProductCategory
from bot.db.repo import (
    contest_leaderboard,
    count_proofs_submitted,
    create_contest,
    get_active_contest,
    get_order,
    get_orders_by_group,
    get_product,
    has_referral_with_purchase,
    list_active_products,
    list_all_user_ids,
    list_orders_by_status,
    list_proofs_submitted,
    set_order_status,
    set_product_bonus,
    set_product_fzr_mapping,
    set_product_name,
    set_product_telegram_kind,
    set_product_price,
)
from bot.db.session import get_session
from bot.keyboards import admin_order_keyboard
from bot.services.fulfillment import clear_awaiting_review, confirm_and_deliver, mark_delivered_and_notify

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

    reports = []
    for line in _batch_lines(message.text, usage_example):
        # The name can contain spaces ("Ваучери лайт"), so take the last
        # three tokens as amount/price/cost and everything between the
        # command and those as the name, rather than a fixed maxsplit that
        # breaks multi-word names.
        parts = line.split()
        if len(parts) < 5:
            reports.append(f"⚠️ Формат нодуруст: {line}")
            continue

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
            reports.append(f"⚠️ Миқдор ва нарх бояд рақам бошанд: {line}")
            continue

        async with get_session() as session:
            session.add(product)
            await session.commit()
            await session.refresh(product)

        extra = f" ({product.display_name})" if product.name[:1].isdigit() else ""
        reports.append(
            f"✅ #{product.id} {product.name}{extra} ба {product.price_somoni:.2f} сомонӣ "
            f"(фоида {product.margin_somoni:.2f} сомонӣ)"
        )

    if not reports:
        await message.answer(
            f"Истифода: {usage_example} <ном> <миқдор> <нарх_фурӯш> <нарх_харид>\n"
            f"Мисол: {usage_example} Starter 100 10 8"
        )
        return

    await message.answer("\n".join(reports))


@router.message(Command("addproduct"))
async def add_product(message: Message) -> None:
    await _add_product(message, ProductCategory.DIAMONDS, "/addproduct")


@router.message(Command("addstars"))
async def add_stars(message: Message) -> None:
    await _add_product(message, ProductCategory.TELEGRAM, "/addstars")


@router.message(Command("addbigo"))
async def add_bigo(message: Message) -> None:
    await _add_product(message, ProductCategory.BIGO_LIVE, "/addbigo")


@router.message(Command("addffid"))
async def add_ff_indonesia(message: Message) -> None:
    await _add_product(message, ProductCategory.FF_INDONESIA, "/addffid")


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

    lines = []
    for p in products:
        qty = f": {p.display_name}" if p.name[:1].isdigit() else ""
        lines.append(
            f"#{p.id} [{p.category.value}] {p.name}{qty} = {p.price_somoni:.2f}с "
            f"(харид {p.cost_somoni:.2f}с, фоида {p.margin_somoni:.2f}с)"
        )
    await message.answer("\n".join(lines))


@router.message(Command("delproduct"))
async def delete_product(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await _reject_non_admin(message)
        return

    reports = []
    for line in _batch_lines(message.text, "/delproduct"):
        parts = line.split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip().isdigit():
            reports.append(f"⚠️ Формат нодуруст: {line}")
            continue

        async with get_session() as session:
            product = await get_product(session, int(parts[1].strip()))
            if product is None:
                reports.append(f"⚠️ Маҳсулот #{parts[1].strip()} ёфт нашуд.")
                continue
            product.is_active = False
            await session.commit()

        reports.append(f"✅ Маҳсулот #{product.id} ({product.name}) хомӯш карда шуд.")

    if not reports:
        await message.answer("Истифода: /delproduct <ID>\nID-ро аз /products гиред.\n(метавонед якчанд сатрро дар як паём фиристед)")
        return

    await message.answer("\n".join(reports))


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


@router.message(Command("proofs"))
async def proofs_submitted(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await _reject_non_admin(message)
        return

    async with get_session() as session:
        total = await count_proofs_submitted(session)
        rows = await list_proofs_submitted(session, limit=30)

    if not rows:
        await message.answer("То ҳол ягон мизоҷ чек (расиди пардохт) нафиристодааст.")
        return

    lines = [f"🧾 Ҳамагӣ {total} чек фиристода шудааст. Охирин {len(rows)}-то:\n"]
    for order, user in rows:
        name = f"@{user.username}" if user.username else (user.full_name or f"ID{user.id}")
        if order.proof_submitted_at:
            when = order.proof_submitted_at.strftime("%d.%m.%Y %H:%M")
        else:
            # Sent before /proofs tracking existed — created_at is the
            # closest thing we have, not the exact submit time.
            when = order.created_at.strftime("%d.%m.%Y %H:%M") + " (тахминӣ)"
        lines.append(
            f"#{order.id} — {name} (id={user.id}) — {order.amount_somoni:.2f}с — {order.status.value} — {when}"
        )
    await message.answer("\n".join(lines)[:4000])


@router.message(Command("contest_start"))
async def contest_start(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await _reject_non_admin(message)
        return

    # Name can have spaces ("Ваучери лайт"), same trailing-args trick as
    # /addproduct: last two tokens are target_referrals/duration_days,
    # everything between the command and those is the prize name.
    parts = message.text.split()
    if len(parts) < 4:
        await message.answer(
            "Истифода: /contest_start <номи ҷоиза> <шумораи реферал> <рӯз>\n"
            "Мисол: /contest_start Ваучери лайт 5 2"
        )
        return

    prize_name = " ".join(parts[1:-2])
    target_str, days_str = parts[-2:]
    try:
        target_referrals = int(target_str)
        duration_days = float(days_str)
    except ValueError:
        await message.answer("Шумораи реферал бояд рақами бутун бошад, рӯз бояд рақам бошад.")
        return

    async with get_session() as session:
        contest = await create_contest(session, prize_name, target_referrals, duration_days)

    await message.answer(
        f"🏁 Мусобиқа сар шуд!\n\n"
        f"🎁 Ҷоиза: {contest.prize_name}\n"
        f"🎯 Ҳадаф: аввалин каси {contest.target_referrals} дӯсти НАВ даъват кунад ва ҳадди ақал 1-тои онҳо харид кунад\n"
        f"⏰ Анҷом: {contest.ends_at.strftime('%d.%m.%Y %H:%M')} (то {duration_days:g} рӯз)\n\n"
        f"Танҳо касоне ҳисоб мешаванд, ки БАЪД аз ҳозир тавассути линки реферал ба бот ворид шаванд. "
        f"Ғолиб худкор муайян ва огоҳ карда мешавад."
    )


@router.message(Command("contest_status"))
async def contest_status(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await _reject_non_admin(message)
        return

    async with get_session() as session:
        contest = await get_active_contest(session)
        if contest is None:
            await message.answer("Ҳозир ягон мусобиқаи фаъол нест. /contest_start истифода баред.")
            return
        rows = await contest_leaderboard(session, contest)
        purchase_flags = {
            user.id: await has_referral_with_purchase(session, user.id, contest.started_at)
            for user, _ in rows
        }

    lines = [
        f"🏁 Мусобиқаи фаъол: {contest.prize_name}",
        f"🎯 Ҳадаф: {contest.target_referrals} реферал (ҳадди ақал 1 харид) | ⏰ Анҷом: {contest.ends_at.strftime('%d.%m.%Y %H:%M')}\n",
    ]
    if not rows:
        lines.append("Ҳанӯз ҳеҷ кас дӯст даъват накардааст.")
    else:
        medals = ["🥇", "🥈", "🥉"]
        for i, (user, count) in enumerate(rows):
            icon = medals[i] if i < 3 else f"{i + 1}."
            name = f"@{user.username}" if user.username else (user.full_name or f"ID{user.id}")
            left = max(contest.target_referrals - count, 0)
            bought = "✅ харид кард" if purchase_flags.get(user.id) else "❌ ҳанӯз харид накард"
            lines.append(f"{icon} {name} — {count}/{contest.target_referrals} (боқӣ {left}) — {bought}")
    await message.answer("\n".join(lines))


@router.message(Command("broadcast"))
async def broadcast(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await _reject_non_admin(message)
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Истифода: /broadcast <матн>\n(Матн метавонад якчанд сатр дошта бошад.)")
        return

    text = parts[1]
    async with get_session() as session:
        user_ids = await list_all_user_ids(session)

    sent = 0
    failed = 0
    for user_id in user_ids:
        try:
            await message.bot.send_message(user_id, text)
            sent += 1
        except Exception:
            # Blocked the bot, deactivated account, etc. — one bad
            # recipient must never stop the rest of the broadcast.
            failed += 1
        await asyncio.sleep(0.05)  # stay well under Telegram's rate limit

    await message.answer(
        f"📢 Эълон фиристода шуд: ба {sent} корбар расид"
        + (f", ба {failed} нафар нарасид (эҳтимол боти моро баста бошанд)." if failed else ".")
    )


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
    await clear_awaiting_review(bot, order)
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


@router.message(Command("paid"))
async def paid_command(message: Message) -> None:
    """For when the admin has verified payment themselves (checked their
    own bank app/balance) and neither the photo-review flow nor the SMS
    auto-confirm caught it — same effect as tapping "✅ Пардохт тасдиқ
    шуд" on the order's original message, just callable directly by order
    number without scrolling back to find it."""
    if not is_admin(message.from_user.id):
        await _reject_non_admin(message)
        return

    reports = []
    for line in _batch_lines(message.text, "/paid"):
        parts = line.split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip().isdigit():
            reports.append(f"⚠️ Формат нодуруст: {line}")
            continue

        order_id = int(parts[1].strip())
        async with get_session() as session:
            order = await get_order(session, order_id)
            if order is None:
                reports.append(f"⚠️ Фармоиши #{order_id} ёфт нашуд.")
                continue
            if order.status != OrderStatus.AWAITING_PAYMENT:
                reports.append(
                    f"⚠️ Фармоиши #{order_id} аллакай '{order.status.value}' аст — такрор намекунам."
                )
                continue

        result = await confirm_and_deliver(message.bot, order_id)
        if result is None:
            reports.append(f"⚠️ Фармоиши #{order_id} ёфт нашуд.")
            continue
        status_note = (
            "автоматӣ ирсол шуд" if result.auto_delivered
            else "лутфан дастӣ иҷро карда, 'Delivered' ё /delivered занед"
        )
        reports.append(f"✅ Фармоиши #{order_id} ҳамчун пардохтшуда тасдиқ шуд — {status_note}.")

    if not reports:
        await message.answer(
            "Истифода: /paid <рақами фармоиш>\nМисол: /paid 21\n"
            "(метавонед якчанд сатрро дар як паём фиристед)"
        )
        return

    await message.answer("\n".join(reports))


@router.message(Command("underpaid"))
async def underpaid_command(message: Message) -> None:
    """A customer sent less than the order's price (real observed case:
    order #41 was 15.90с, customer's bank transfer was exactly 15с — the
    SMS webhook correctly refused to auto-confirm a mismatched amount, but
    nobody told the customer why nothing happened next). This tells them
    the exact shortfall and points at cheaper in-stock alternatives.
    Never touches order.status — the order is genuinely still unpaid; the
    customer has to act (top up the difference, or pick something
    cheaper) before anything gets confirmed."""
    if not is_admin(message.from_user.id):
        await _reject_non_admin(message)
        return

    reports = []
    for line in _batch_lines(message.text, "/underpaid"):
        parts = line.split()
        if len(parts) != 3:
            reports.append(f"⚠️ Формат нодуруст: {line}")
            continue
        if not parts[1].isdigit():
            reports.append(f"⚠️ order_id бояд рақам бошад: {line}")
            continue
        try:
            received = float(parts[2])
        except ValueError:
            reports.append(f"⚠️ Маблағ бояд рақам бошад: {line}")
            continue

        order_id = int(parts[1])
        async with get_session() as session:
            order = await get_order(session, order_id)
            if order is None:
                reports.append(f"⚠️ Фармоиши #{order_id} ёфт нашуд.")
                continue
            if order.status != OrderStatus.AWAITING_PAYMENT:
                reports.append(f"⚠️ Фармоиши #{order_id} аллакай '{order.status.value}' аст.")
                continue
            if received >= order.amount_somoni:
                reports.append(
                    f"⚠️ #{order_id}: {received:.2f}с камтар нест (фармоиш {order.amount_somoni:.2f}с) — норасоӣ надорад."
                )
                continue

            shortfall = order.amount_somoni - received
            product = await get_product(session, order.product_id)
            cheaper = []
            if product is not None:
                candidates = await list_active_products(session, category=product.category)
                cheaper = [p for p in candidates if p.price_somoni <= received and p.id != product.id]

        lines = [
            f"⚠️ Мо {received:.2f} сомонӣ гирифтем, аммо фармоиши шумо #{order_id} {order.amount_somoni:.2f} сомонӣ буд.",
            f"Норасоӣ: {shortfall:.2f} сомонӣ.",
            "",
            f"Лутфан ё боқимондаи {shortfall:.2f} сомониро бо ҳамон корт/усул фиристед,",
        ]
        if cheaper:
            lines.append("ё бастаи арзонтарро интихоб кунед:")
            for p in cheaper[:5]:
                lines.append(f"  • {p.display_name} — {p.price_somoni:.2f} сомонӣ")
        else:
            lines.append("ё бо админ тамос гиред барои бастаи мувофиқ.")

        try:
            await message.bot.send_message(order.user_id, "\n".join(lines))
        except Exception:
            reports.append(f"⚠️ Фармоиши #{order_id}: ба мизоҷ фиристода нашуд (шояд ботро блок кардааст).")
            continue

        reports.append(f"✅ Фармоиши #{order_id}: мизоҷ огоҳ карда шуд (норасоӣ {shortfall:.2f}с).")

    if not reports:
        await message.answer(
            "Истифода: /underpaid <order_id> <маблағи гирифташуда>\n"
            "Мисол: /underpaid 41 15\n"
            "(метавонед якчанд сатрро дар як паём фиристед)"
        )
        return

    await message.answer("\n".join(reports))


@router.message(Command("underpaidok"))
async def underpaid_ok_command(message: Message) -> None:
    """The opposite of /underpaid: the admin already went ahead and
    fulfilled a short order as a one-time courtesy (already paid/delivered
    by the time this runs — unlike /underpaid, it does NOT check or touch
    order.status) and just wants the customer told not to repeat it. Takes
    the same <order_id> <received_amount> shape as /underpaid so the
    shortfall math is identical either way."""
    if not is_admin(message.from_user.id):
        await _reject_non_admin(message)
        return

    reports = []
    for line in _batch_lines(message.text, "/underpaidok"):
        parts = line.split()
        if len(parts) != 3:
            reports.append(f"⚠️ Формат нодуруст: {line}")
            continue
        if not parts[1].isdigit():
            reports.append(f"⚠️ order_id бояд рақам бошад: {line}")
            continue
        try:
            received = float(parts[2])
        except ValueError:
            reports.append(f"⚠️ Маблағ бояд рақам бошад: {line}")
            continue

        order_id = int(parts[1])
        async with get_session() as session:
            order = await get_order(session, order_id)
            if order is None:
                reports.append(f"⚠️ Фармоиши #{order_id} ёфт нашуд.")
                continue

        shortfall = order.amount_somoni - received
        if shortfall <= 0:
            text = f"✅ Фармоиши шумо #{order_id} гузаронида шуд! Ташаккур барои харид."
        else:
            text = (
                f"✅ Фармоиши шумо #{order_id} гузаронида шуд — гарчанде ки {shortfall:.2f} сомонӣ "
                f"камтар фиристода будед (бояд {order.amount_somoni:.2f} сомонӣ мебуд, шумо "
                f"{received:.2f} сомонӣ фиристодед). Ин дафъа мо истисно кардем.\n\n"
                f"⚠️ Лутфан дар фармоишҳои оянда маблағи ПУРРАРО фиристед — вагарна фармоиш "
                f"тасдиқ намешавад."
            )

        try:
            await message.bot.send_message(order.user_id, text)
        except Exception:
            reports.append(f"⚠️ Фармоиши #{order_id}: ба мизоҷ фиристода нашуд (шояд ботро блок кардааст).")
            continue

        reports.append(f"✅ Фармоиши #{order_id}: мизоҷ огоҳ карда шуд (гузашт, бо огоҳии оянда).")

    if not reports:
        await message.answer(
            "Истифода: /underpaidok <order_id> <маблағи гирифташуда>\n"
            "Мисол: /underpaidok 41 15\n"
            "(метавонед якчанд сатрро дар як паём фиристед)"
        )
        return

    await message.answer("\n".join(reports))


@router.message(Command("delivered"))
async def delivered_command(message: Message) -> None:
    """For orders delivered by hand outside the normal flow (e.g. FazerCards
    auto-delivery failed, admin fulfilled it manually on the site/app
    directly) — marks the order DELIVERED and triggers the review prompt,
    exactly like tapping the "Delivered" button, without having to scroll
    back to find that original message."""
    if not is_admin(message.from_user.id):
        await _reject_non_admin(message)
        return

    reports = []
    for line in _batch_lines(message.text, "/delivered"):
        parts = line.split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip().isdigit():
            reports.append(f"⚠️ Формат нодуруст: {line}")
            continue

        order_id = int(parts[1].strip())
        async with get_session() as session:
            order = await get_order(session, order_id)
            if order is None:
                reports.append(f"⚠️ Фармоиши #{order_id} ёфт нашуд.")
                continue
            if order.status == OrderStatus.DELIVERED:
                reports.append(
                    f"⚠️ Фармоиши #{order_id} аллакай 'ирсолшуда' қайд шудааст — "
                    f"такрор намекунам (то дучандкунии бонуси реферал набошад)."
                )
                continue

        await mark_delivered_and_notify(message.bot, order_id)
        reports.append(f"✅ Фармоиши #{order_id} ирсолшуда қайд шуд, дархости шарҳ ба мизоҷ фиристода шуд.")

    if not reports:
        await message.answer(
            "Истифода: /delivered <рақами фармоиш>\nМисол: /delivered 21\n"
            "(метавонед якчанд сатрро дар як паём фиристед)"
        )
        return

    await message.answer("\n".join(reports))


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
        items = [
            i
            for i in items
            if search in (i.get("name") or "").lower() or search in (i.get("category_id") or "").lower()
        ]

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


@router.message(Command("fzr_raw"))
async def fzr_raw(message: Message) -> None:
    """Diagnostic escape hatch: not every FazerCards product line lives
    under /api/v2/topups (e.g. Telegram Stars/Premium showed up on
    reseller.fazercards.com but never in /fzr_categories) — this probes
    an arbitrary GET path with the real API key so we can find the real
    endpoint interactively instead of guessing blind."""
    if not is_admin(message.from_user.id):
        await _reject_non_admin(message)
        return

    if len(message.text.split(maxsplit=1)) < 2:
        await message.answer(
            "Истифода: /fzr_raw <path>\n"
            "Мисол: /fzr_raw /api/v2/telegram/stars\n"
            "Мисол: /fzr_raw /api/v2/telegram/premium\n\n"
            "Метавонед якчанд хатро якҷоя фиристед (ҳар кадом бо /fzr_raw)."
        )
        return

    import json

    from bot.services.fazercards import FazerCardsError, _request

    for line in _batch_lines(message.text, "/fzr_raw"):
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            await message.answer(f"⚠️ Формат нодуруст: {line}")
            continue
        path = parts[1].strip()
        try:
            data = await _request("GET", path)
        except FazerCardsError as exc:
            await message.answer(f"⚠️ GET {path} — Хатои FazerCards [{exc.code}]: {exc}"[:1500])
            continue
        await message.answer((f"GET {path}\n" + json.dumps(data, ensure_ascii=False, indent=2))[:3800])


@router.message(Command("fzr_docs_search"))
async def fzr_docs_search(message: Message) -> None:
    """Searches the full FazerCards OpenAPI spec for matching paths —
    needed to find e.g. the real order-placement endpoint for Telegram
    Stars/Premium (POST .../order?) without dumping the whole multi-MB doc,
    which is far bigger than a Telegram message and would just get cut off
    uselessly by /fzr_raw's plain truncation."""
    if not is_admin(message.from_user.id):
        await _reject_non_admin(message)
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Истифода: /fzr_docs_search telegram")
        return
    term = parts[1].strip().lower()

    from bot.services.fazercards import FazerCardsError, fetch_openapi_spec

    try:
        spec = await fetch_openapi_spec()
    except FazerCardsError as exc:
        await message.answer(f"⚠️ Хатои FazerCards: {exc}")
        return

    paths = spec.get("paths", {}) if isinstance(spec, dict) else {}
    matches = []
    for path, methods in paths.items():
        if term not in path.lower():
            continue
        for method, op in (methods or {}).items():
            if method.lower() not in ("get", "post", "put", "patch", "delete"):
                continue
            summary = (op or {}).get("summary") or (op or {}).get("description") or ""
            matches.append(f"{method.upper()} {path} — {summary}")

    if not matches:
        await message.answer(f"Ягон роҳ бо '{term}' дар OpenAPI docs ёфт нашуд.")
        return
    await message.answer("\n".join(matches)[:3800])


@router.message(Command("fzr_docs_path"))
async def fzr_docs_path(message: Message) -> None:
    """Full operation definition (params/requestBody/responses schema) for
    one exact path — for when /fzr_docs_search's one-line summaries aren't
    enough to confirm exact request/response field names."""
    if not is_admin(message.from_user.id):
        await _reject_non_admin(message)
        return

    import json
    import re

    from bot.services.fazercards import FazerCardsError, fetch_openapi_spec

    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Истифода: /fzr_docs_path /api/v2/telegram/stars/buy")
        return
    path = parts[1].strip()

    try:
        spec = await fetch_openapi_spec()
    except FazerCardsError as exc:
        await message.answer(f"⚠️ Хатои FazerCards: {exc}")
        return

    op = (spec.get("paths", {}) if isinstance(spec, dict) else {}).get(path)
    if op is None:
        await message.answer(f"Роҳи '{path}' дар docs ёфт нашуд.")
        return

    op_json = json.dumps(op, ensure_ascii=False, indent=2)
    ref_names = sorted(set(re.findall(r'"\$ref":\s*"#/components/schemas/([^"]+)"', op_json)))
    components = (spec.get("components") or {}).get("schemas", {})
    chunks = [f"{path}:\n{op_json}"]
    for name in ref_names:
        schema = components.get(name)
        if schema is not None:
            chunks.append(f"Schema {name}:\n" + json.dumps(schema, ensure_ascii=False, indent=2))

    full = "\n\n".join(chunks)
    for i in range(0, len(full), 3800):
        await message.answer(full[i : i + 3800])


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


@router.message(Command("setname"))
async def set_name(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await _reject_non_admin(message)
        return

    reports = []
    for line in _batch_lines(message.text, "/setname"):
        parts = line.split(maxsplit=2)
        if len(parts) != 3:
            reports.append(f"⚠️ Формат нодуруст: {line}")
            continue

        if not parts[1].isdigit():
            reports.append(f"⚠️ product_id бояд рақам бошад: {line}")
            continue

        async with get_session() as session:
            product = await get_product(session, int(parts[1]))
            if product is None:
                reports.append(f"⚠️ Маҳсулот #{parts[1]} ёфт нашуд.")
                continue
            old_name = product.name
            product = await set_product_name(session, product, parts[2])

        reports.append(f'✅ #{product.id}: "{old_name}" → "{product.name}"')

    if not reports:
        await message.answer(
            "Истифода: /setname <product_id> <номи нав>\n"
            "Мисол: /setname 16 Бастаи навкорон\n"
            "(метавонед якчанд сатрро дар як паём фиристед)"
        )
        return

    await message.answer("\n".join(reports))


@router.message(Command("settgkind"))
async def set_tg_kind(message: Message) -> None:
    """Wires a TELEGRAM-category product to FazerCards' Telegram
    Stars/Premium API (see bot/services/fazercards.py) — "diamonds" then
    means star count for kind=stars, or month count (must be 3/6/12) for
    kind=premium. Without this, the product falls back to manual delivery
    like any unmapped product — same safe default as /mapproduct."""
    if not is_admin(message.from_user.id):
        await _reject_non_admin(message)
        return

    reports = []
    for line in _batch_lines(message.text, "/settgkind"):
        parts = line.split()
        if len(parts) != 3 or parts[2] not in ("stars", "premium"):
            reports.append(f"⚠️ Формат нодуруст: {line}")
            continue
        if not parts[1].isdigit():
            reports.append(f"⚠️ product_id бояд рақам бошад: {line}")
            continue

        async with get_session() as session:
            product = await get_product(session, int(parts[1]))
            if product is None:
                reports.append(f"⚠️ Маҳсулот #{parts[1]} ёфт нашуд.")
                continue
            if product.category != ProductCategory.TELEGRAM:
                reports.append(f"⚠️ #{product.id} категорияи Telegram надорад.")
                continue
            product = await set_product_telegram_kind(session, product, parts[2])

        reports.append(f"✅ #{product.id} ({product.name}) → {product.telegram_kind}")

    if not reports:
        await message.answer(
            "Истифода: /settgkind <product_id> <stars|premium>\n"
            "Мисол: /settgkind 20 stars\n"
            "(метавонед якчанд сатрро дар як паём фиристед)"
        )
        return

    await message.answer("\n".join(reports))
