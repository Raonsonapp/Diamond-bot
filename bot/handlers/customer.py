import asyncio
import html
import uuid
from datetime import timezone
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from bot.config import config
from bot.media_ids import DC_IMAGE_FILE_ID, ALIF_IMAGE_FILE_ID, FREE_FIRE_IMAGE_FILE_ID, PUBG_IMAGE_FILE_ID
from bot.db.models import OrderStatus, Product, ProductCategory, User
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
    get_setting,
    get_user,
    get_user_purchase_stats,
    list_active_products,
    top_buyers,
    top_referrers,
    upsert_user,
)
from bot.db.session import get_session
from bot.formatting import format_price
from bot.keyboards import (
    admin_order_keyboard,
    back_to_menu_keyboard,
    cart_select_keyboard,
    confirm_order_keyboard,
    free_fire_region_keyboard,
    change_payment_method_keyboard,
    contact_keyboard,
    games_menu_keyboard,
    main_menu_keyboard,
    payment_link_keyboard,
    persistent_menu_keyboard,
    payment_method_keyboard,
    PAYMENT_METHODS,
    BANK_HINT_NAMES,
    products_keyboard,
    profile_menu_keyboard,
    referral_menu_keyboard,
    review_channel_keyboard,
    reuse_recipient_keyboard,
    sponsor_gate_keyboard,
    telegram_menu_keyboard,
    terms_keyboard,
)
from bot.services.contest import maybe_declare_contest_winner
from bot.services.payments import get_payment_provider
from bot.services.pricing import quote_custom_price
from bot.services.referral_rewards import maybe_credit_referral_milestone
from bot.services.sponsor import is_subscribed_to_sponsor_channel
from bot.states import OrderFlow, WalletTopupFlow
from bot.texts import FAQ_TEXT, TERMS_TEXT

# Custom-amount ("Миқдори дигар") is only ever shown on the Telegram
# Stars view now — matches FazerCards' own /api/v2/telegram/stars
# min/max_amount exactly, so a customer can never order a quantity that
# would just fail delivery.
MIN_CUSTOM_UNITS = 50
MAX_CUSTOM_UNITS = 10_000
# How long receive_payment_proof actively polls for a matching SMS after a
# customer submits their screenshot (6 * 5s = 30s), mirroring a rival
# bot's "searching, 5-30 seconds" UX — see the SMS cross-check there.
_PROOF_SMS_POLL_ATTEMPTS = 6
_PROOF_SMS_POLL_DELAY_SECONDS = 5
# requirements.txt pulls in the "tzdata" package so this resolves on slim
# Docker images that ship no system IANA database, but a missing timezone
# database must never be able to crash the whole bot's startup — fall back
# to plain UTC display rather than an unhandled import-time exception.
try:
    DUSHANBE_TZ = ZoneInfo("Asia/Dushanbe")
except Exception:
    DUSHANBE_TZ = timezone.utc


def _format_local_datetime(dt) -> str:
    # Postgres (production) returns tz-aware UTC datetimes; sqlite (local
    # dev/tests) hands back naive ones for the same DateTime(timezone=True)
    # column — those were still written via utcnow(), so treat a naive
    # value as UTC rather than misreading it as already-local.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(DUSHANBE_TZ).strftime("%d.%m.%Y %H:%M:%S")

router = Router(name="customer")

WELCOME_TEXT = "Хуш омадед ба FF MARGEDAR! 💎\nМагазини фурӯши хидматҳои рақамӣ.\n\nЧиро интихоб мекунед?"



@router.message(F.video | F.animation | F.photo)
async def admin_get_media_file_id(message: Message, state: FSMContext) -> None:
    """Return reusable Telegram file IDs for admin-uploaded shop media.

    Video/animation IDs are reusable for the welcome experience. No binary
    media is stored in GitHub; Telegram hosts the uploaded file.
    """
    # Payment screenshots must continue to the order/wallet proof handlers.
    # Otherwise the admin sees SHOP_IMAGE_FILE_ID instead of the actual receipt workflow.
    current_state = await state.get_state()
    if current_state in {
        OrderFlow.awaiting_payment_proof.state,
        WalletTopupFlow.awaiting_proof.state,
    }:
        return

    sender_id = message.from_user.id if message.from_user else 0
    if sender_id not in config.admin_user_ids and sender_id != config.admin_chat_id:
        return

    caption_check = (message.caption or "").strip().lower()
    if any(word in caption_check for word in ("чек", "расид", "receipt", "пардохт", "payment")):
        return

    if message.video and "tutorial" in (message.caption or "").lower():
        env_key = "TUTORIAL_VIDEO_FILE_ID"
        media = message.video
        await message.reply(
            f"✅ Видеои омӯзишӣ қабул шуд. Дар Render → Environment тағйирёбандаи "
            f"<code>{env_key}</code>-ро ба ин file_id гузоред:\n\n<code>{media.file_id}</code>",
            parse_mode="HTML",
        )
        return

    if message.photo:
        media = message.photo[-1]
        media_kind = "Расм"
        caption = (message.caption or "").lower()
        if "free" in caption or "fire" in caption:
            env_key = "FREE_FIRE_IMAGE_FILE_ID"
        elif "pubg" in caption:
            env_key = "PUBG_IMAGE_FILE_ID"
        elif "alif" in caption or "алиф" in caption:
            env_key = "ALIF_IMAGE_FILE_ID"
        elif "dc" in caption or "душанбе" in caption:
            env_key = "DC_IMAGE_FILE_ID"
        elif "diamond" in caption or "алмаз" in caption:
            env_key = "DIAMOND_IMAGE_FILE_ID"
        else:
            env_key = "SHOP_IMAGE_FILE_ID"
    else:
        media = message.animation or message.video
        media_kind = "GIF/аниматсия" if message.animation else "Видео MP4"
        env_key = "WELCOME_ANIMATION_FILE_ID"

    await message.reply(
        f"✅ {media_kind} қабул шуд.\n\n"
        f"file_id барои танзими {env_key}:\n"
        f"<code>{media.file_id}</code>",
        parse_mode="HTML",
    )



async def _show_main_menu(
    message: Message, state: FSMContext, user_id: int | None = None, language: str | None = None
) -> None:
    await state.clear()
    # Sponsored campaign is shown before the shop menu so it appears at the top.
    # Campaign/AdsGram errors fail open and never block buying.
    recipient_id = user_id if user_id is not None else message.from_user.id
    recipient_language = language or getattr(message.from_user, "language_code", None) or "ru"
    from bot.services.ad_campaign import maybe_send_campaign_ad
    from bot.services.adsgram import maybe_send_ad

    await maybe_send_campaign_ad(message.bot, recipient_id)
    # Telegram cannot combine a persistent reply keyboard and inline buttons
    # on one message. Keep the bottom Menu button persistent, then show the
    # inline shop navigation as a separate, editable message.
    if config.welcome_animation_file_id:
        await message.answer_animation(
            animation=config.welcome_animation_file_id,
            caption=WELCOME_TEXT,
            reply_markup=persistent_menu_keyboard(),
        )
    else:
        await message.answer(WELCOME_TEXT, reply_markup=persistent_menu_keyboard())
    await message.answer("📋 Менюи асосӣ — бахшро интихоб кунед:", reply_markup=main_menu_keyboard())
    await maybe_send_ad(message.bot, recipient_id, recipient_language)


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

    lines = []
    for o in orders:
        when = _format_local_datetime(o.created_at)
        lines.append(f"#{o.id} — {o.amount_somoni:.2f} сомонӣ — {o.status.value} — 📅 {when}")
    return "📦 Фармоишҳои охирини шумо:\n" + "\n".join(lines)


async def _register_and_continue(message: Message, state: FSMContext, from_user, referred_by: int | None) -> None:
    async with get_session() as session:
        is_new_user = await get_user(session, from_user.id) is None
        user = await upsert_user(
            session,
            from_user.id,
            from_user.username,
            from_user.full_name,
            referred_by=referred_by,
        )

    if is_new_user and referred_by is not None:
        await maybe_declare_contest_winner(message.bot, referred_by)
        await maybe_credit_referral_milestone(message.bot, referred_by)

    await state.clear()
    if user.accepted_terms_at is None:
        await message.answer(TERMS_TEXT, reply_markup=terms_keyboard())
        return

    await _show_main_menu(message, state, from_user.id, from_user.language_code or "ru")


@router.message(Command("tutorial"))
@router.message(Command("help"))
async def tutorial_command(message: Message) -> None:
    """Customer-facing Tajik guide; sends an MP4 when configured, otherwise a readable step-by-step guide."""
    if config.tutorial_video_file_id:
        await message.answer_video(
            video=config.tutorial_video_file_id,
            caption=(
                "🎬 <b>Дастури истифодаи FF MARGEDAR</b>\n\n"
                "Агар видео ҳама чизро нишон надиҳад, қадамҳоро дар зер хонед: /tutorial"
            ),
            parse_mode="HTML",
        )
    await message.answer(
        "🎮 <b>Дастури истифодаи FF MARGEDAR</b>\n\n"
        "1️⃣ <b>Хариди алмаз:</b> «🎮 Бозиҳо» → «💎 Free Fire — Алмазҳо» → бастаро интихоб кунед.\n"
        "2️⃣ <b>ID-и бозингар:</b> ID-и дурусти Free Fire-ро ворид карда, фармоишро тасдиқ кунед.\n"
        "3️⃣ <b>Пардохт:</b> усули пардохтро интихоб кунед, маблағи нишон додашударо пардохт карда, расидро фиристед.\n"
        "4️⃣ <b>Интизорӣ:</b> расид аз ҷониби админ санҷида мешавад; то тасдиқ шудан фармоишро пардохтшуда ҳисоб накунед.\n"
        "5️⃣ <b>Фармоишҳо:</b> аз «👤 Профил» → «📦 Фармоишҳои ман» ҳолатро бинед.\n"
        "6️⃣ <b>Ҳамён ва реферал:</b> аз меню «👛 Ҳамён» ё «🤝 Реферал»-ро кушоед.\n"
        "7️⃣ <b>Кӯмак:</b> «🆘 Дастгирӣ»-ро интихоб кунед.\n\n"
        "✨ Emoji-пакҳо барои кушодан дар Telegram:\n"
        "• https://t.me/addemoji/NewsEmoji\n"
        "• https://t.me/addemoji/Alphabet_Red_Bright\n"
        "• https://t.me/addemoji/G8yg_by_fStikBot\n"
        "• https://t.me/addemoji/FinanceEmoji\n"
        "• https://t.me/addemoji/abc2394_by_TgEmojiBot\n"
        "• https://t.me/addemoji/sticks_f97eb_by_TgEmodziBot\n"
        "• https://t.me/addemoji/tj_rus_by_TgEmodziBot\n\n"
        "Барои дидани ҳамин дастур ҳар вақт /tutorial нависед.",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    """Handle /start without silently failing: log unexpected errors and tell the user to retry."""
    try:
        args = (message.text or "/start").split(maxsplit=1)
        referred_by = None
        if len(args) > 1 and args[1].startswith("ref_"):
            try:
                referred_by = int(args[1].removeprefix("ref_"))
            except ValueError:
                referred_by = None

        if not await is_subscribed_to_sponsor_channel(message.bot, message.from_user.id):
            await state.update_data(pending_referred_by=referred_by)
            await message.answer(
                "📢 Барои истифодаи бот, лутфан аввал ба канали мо ҳамроҳ шавед, "
                "баъд тугмаи \"✅ Санҷидан\"-ро зер кунед:",
                reply_markup=sponsor_gate_keyboard(),
            )
            return

        await _register_and_continue(message, state, message.from_user, referred_by)
    except Exception:
        import logging
        logging.exception("Unhandled error while processing /start for Telegram user %s",
                          message.from_user.id if message.from_user else "unknown")
        try:
            await message.answer(
                "⚠️ Ҳангоми кушодани бот хатогӣ шуд. Лутфан баъд аз чанд сония /start-ро такрор кунед. "
                "Агар мушкил боқӣ монад, ба админ хабар диҳед."
            )
        except Exception:
            logging.exception("Could not send /start error notice")


@router.callback_query(F.data == "sponsor:check")
async def sponsor_check(callback: CallbackQuery, state: FSMContext) -> None:
    if not await is_subscribed_to_sponsor_channel(callback.bot, callback.from_user.id):
        await callback.answer("Шумо ҳанӯз ба канал ҳамроҳ нашудед!", show_alert=True)
        return

    data = await state.get_data()
    referred_by = data.get("pending_referred_by")
    await callback.message.edit_text("✅ Ташаккур! Шумо ба канал ҳамроҳ шудед.", reply_markup=None)
    await _register_and_continue(callback.message, state, callback.from_user, referred_by)
    await callback.answer()


@router.callback_query(F.data == "terms:accept")
async def accept_terms_cb(callback: CallbackQuery, state: FSMContext) -> None:
    async with get_session() as session:
        user = await get_user(session, callback.from_user.id)
        await accept_terms(session, user)

    await callback.message.edit_text(
        f"✅ Ташаккур! Шартнома қабул шуд.\n\n"
        f"👋 Хуш омадед, {callback.from_user.full_name}!\n"
        f"🆔 ID-и шумо: {callback.from_user.id}",
        reply_markup=None,
    )
    await _show_main_menu(callback.message, state, callback.from_user.id, callback.from_user.language_code or "ru")
    await callback.answer()


@router.callback_query(F.data == "menu:main")
async def menu_main(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("📋 Менюи асосӣ — бахшро интихоб кунед:", reply_markup=main_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "menu:reviews")
async def menu_reviews(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "Шарҳҳои мизоҷони моро дар канал бинед:", reply_markup=review_channel_keyboard()
    )
    await callback.answer()


# The persistent bottom button is the only general-purpose text command.
# Other arbitrary text outside an active input step is ignored.
@router.message(F.text == "📋 Меню")
async def persistent_menu_button(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("📋 Менюи асосӣ — бахшро интихоб кунед:", reply_markup=main_menu_keyboard())




@router.message(F.text == "🎮 Бозиҳо")
async def reply_games(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("🎮 Бозиро интихоб кунед:", reply_markup=games_menu_keyboard())


@router.message(F.text == "✈️ Telegram")
async def reply_telegram(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("✈️ Хизматро интихоб кунед:", reply_markup=telegram_menu_keyboard())


@router.message(F.text == "👤 Профил")
async def reply_profile(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with get_session() as session:
        user = await get_user(session, message.from_user.id)
        count, total = await get_user_purchase_stats(session, message.from_user.id)

    text = (
        "👤 Профили шумо\n\n"
        f"👋 Ном: {message.from_user.full_name}\n"
        f"🆔 ID: {message.from_user.id}\n"
        f"📱 Username: @{message.from_user.username or '—'}\n\n"
        "📊 Омори харид:\n"
        f"✅ Харидҳои муваффақ: {count}\n"
        f"💰 Маблағи умумии харид: {total:.2f} сомонӣ\n"
        f"🤝 Баланси реферал: {user.referral_balance:.2f} сомонӣ"
    )
    await message.answer(text, reply_markup=profile_menu_keyboard())


@router.message(F.text == "🤝 Реферал")
async def reply_referral(message: Message, state: FSMContext) -> None:
    await state.clear()
    bot_user = await message.bot.get_me()
    link = f"https://t.me/{bot_user.username}?start=ref_{message.from_user.id}"

    async with get_session() as session:
        user = await get_user(session, message.from_user.id)
        invited = await count_referrals(session, message.from_user.id)

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
    await message.answer(text, reply_markup=referral_menu_keyboard())


@router.message(F.text == "⭐ Отзив")
async def reply_review_channel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Шарҳҳои мизоҷони моро дар канал бинед:", reply_markup=review_channel_keyboard())


@router.message(F.text == "🆘 Дастгирӣ")
async def reply_contact(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "💬 Барои тамос бо FF MARGEDAR танҳо Telegram-ро истифода баред. "
        "Рақами телефон ва WhatsApp дар бот нишон дода намешавад.",
        reply_markup=contact_keyboard(),
    )


@router.message(Command("support"))
async def support_command(message: Message, state: FSMContext) -> None:
    """Quick access to Telegram-only support from any point in the bot."""
    await state.clear()
    await message.answer(
        "💬 Дастгирии FF MARGEDAR дар Telegram:",
        reply_markup=contact_keyboard(),
    )


@router.message(F.text == "❓ Саволҳои маъмул")
async def reply_faq(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(FAQ_TEXT, reply_markup=back_to_menu_keyboard())


@router.message(F.text == "ℹ️ Маълумот")
async def reply_about(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with get_session() as session:
        users_count = await count_total_users(session)
        orders_count = await count_total_delivered_orders(session)

    text = (
        "ℹ️ Дар бораи FF MARGEDAR\n\n"
        "🤖 Боти расмии фурӯши хидматҳои рақамӣ дар Тоҷикистон\n\n"
        "🎮 Хизматҳо: Free Fire diamonds, Telegram Stars\n"
        "🚀 Афзалиятҳо: суръати баланд (1-5 дақ.), бехатар\n\n"
        f"📊 Корбарон: {users_count} | Фармоишҳои иҷрошуда: {orders_count}\n\n"
        f"📢 Канал: {config.shop_channel_url}"
    )
    await message.answer(text, reply_markup=back_to_menu_keyboard())


@router.callback_query(F.data == "menu:games")
async def menu_games(callback: CallbackQuery, state: FSMContext) -> None:
    # Acknowledge immediately to prevent Telegram's spinner from appearing stuck.
    await callback.answer()
    await state.clear()
    try:
        await callback.message.edit_text(
            "🎮 Бозиро интихоб кунед:",
            reply_markup=games_menu_keyboard(),
        )
    except Exception:
        # If the current message is a media message, send a fresh compact menu.
        await callback.message.answer(
            "🎮 Бозиро интихоб кунед:",
            reply_markup=games_menu_keyboard(),
        )


async def _open_catalog(
    callback: CallbackQuery, state: FSMContext, category: ProductCategory, title: str, telegram_kind: str | None = None
) -> None:
    # Answer the callback before any database work; Telegram requires this promptly.
    await callback.answer()
    try:
        async with get_session() as session:
            products = await list_active_products(session, category=category, telegram_kind=telegram_kind)

        if not products:
            await callback.message.answer(
                "Ҳозир маҳсулот дастрас нест. Лутфан баъдтар кӯшиш кунед ё бо админ тамос гиред.",
                reply_markup=back_to_menu_keyboard(),
            )
            return

        # Keep the catalog compact: product icons and prices are shown on buttons.
        try:
            await callback.message.edit_text(
                title,
                reply_markup=products_keyboard(products, category, telegram_kind),
            )
        except Exception:
            await callback.message.answer(
                title,
                reply_markup=products_keyboard(products, category, telegram_kind),
            )
        await state.set_state(OrderFlow.choosing_product)
    except Exception:
        await callback.message.answer(
            "⚠️ Каталог ҳоло кушода нашуд. Лутфан /start фиристед ва дубора кӯшиш кунед."
        )
        raise


@router.callback_query(F.data == "menu:buy_diamonds")
async def menu_buy_diamonds(callback: CallbackQuery, state: FSMContext) -> None:
    # Region must be chosen before showing prices; each region has its own SKU catalog.
    await callback.answer()
    await state.clear()
    await callback.message.answer(
        "🌍 <b>Минтақаи аккаунти Free Fire-ро интихоб кунед:</b>\n\n"
        "🌐 СНГ — нархҳои СНГ\n"
        "🇮🇩 Индонезия — нархҳои Индонезия",
        reply_markup=free_fire_region_keyboard(),
    )


@router.callback_query(F.data == "menu:telegram")
async def menu_telegram(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("✈️ Хизматро интихоб кунед:", reply_markup=telegram_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "menu:telegram_stars")
async def menu_telegram_stars(callback: CallbackQuery, state: FSMContext) -> None:
    await _open_catalog(
        callback, state, ProductCategory.TELEGRAM, "⭐ Миқдори Stars-ро интихоб кунед:", telegram_kind="stars"
    )


@router.callback_query(F.data == "menu:telegram_premium")
async def menu_telegram_premium(callback: CallbackQuery, state: FSMContext) -> None:
    await _open_catalog(
        callback, state, ProductCategory.TELEGRAM, "💎 Муддати Premium-ро интихоб кунед:", telegram_kind="premium"
    )


@router.callback_query(F.data == "menu:buy_bigo")
async def menu_buy_bigo(callback: CallbackQuery, state: FSMContext) -> None:
    await _open_catalog(callback, state, ProductCategory.BIGO_LIVE, "📡 Бастаи Bigo Live-ро интихоб кунед:")


@router.callback_query(F.data == "menu:buy_ff_id")
async def menu_buy_ff_indonesia(callback: CallbackQuery, state: FSMContext) -> None:
    # Legacy menu callback now follows the same one-time region selection.
    await callback.answer()
    await state.clear()
    await callback.message.answer(
        "🌍 <b>Минтақаи аккаунти Free Fire-ро интихоб кунед:</b>\n\n"
        "🌐 СНГ — нархҳои СНГ\n"
        "🇮🇩 Индонезия — нархҳои Индонезия",
        reply_markup=free_fire_region_keyboard(),
    )


@router.callback_query(F.data.regexp(r"^cartmode:(?!exit:).+$"))
async def enter_cart_mode(callback: CallbackQuery, state: FSMContext) -> None:
    category = ProductCategory(callback.data.split(":", 1)[1])
    async with get_session() as session:
        products = await list_active_products(session, category=category)

    await state.update_data(cart_category=category.value, cart_ids=[])
    await state.set_state(OrderFlow.choosing_cart)
    cart_text = "🛒 Бастаҳоеро, ки мехоҳед якҷоя харед, интихоб кунед (якчанд адад мумкин аст):"
    cart_markup = cart_select_keyboard(products, category, set())
    if callback.message.photo or callback.message.animation or callback.message.video:
        await callback.message.answer(cart_text, reply_markup=cart_markup)
        try:
            await callback.message.delete()
        except Exception:
            pass
    else:
        await callback.message.edit_text(cart_text, reply_markup=cart_markup)
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
    summary = "\n".join(f"• {p.display_name}" for p in products)
    prompt = await _recipient_prompt(category)
    text = f"Шумо интихоб кардед:\n{summary}\n\n💰 Ҳамагӣ: {format_price(total)} сомонӣ.\n\nЛутфан {prompt} ирсол кунед:"

    if last_recipient:
        text += f"\n\nШумо пештар бо ин истифода карда будед: {last_recipient}"
        markup = reuse_recipient_keyboard(last_recipient)
    else:
        markup = None

    # The catalog is now a photo message; Telegram cannot edit its photo as text.
    # Send the next step as a normal message, then remove the old catalog photo.
    if callback.message.photo or callback.message.animation or callback.message.video:
        await callback.message.answer(text, reply_markup=markup)
        try:
            await callback.message.delete()
        except Exception:
            pass
    else:
        await callback.message.edit_text(text, reply_markup=markup)
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
        "ℹ️ Дар бораи FF MARGEDAR\n\n"
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


@router.callback_query(F.data.startswith("product:custom:"))
async def choose_custom_amount(callback: CallbackQuery, state: FSMContext) -> None:
    category_value = callback.data.split(":", 2)[2]
    await state.update_data(custom_category=category_value)
    await state.set_state(OrderFlow.entering_custom_amount)
    unit = "алмаз" if category_value == ProductCategory.DIAMONDS.value else "Stars"
    prompt_text = f"Чанд адад {unit} мехоҳед? Рақамро нависед, аз {MIN_CUSTOM_UNITS} то {MAX_CUSTOM_UNITS}:"
    if callback.message.photo or callback.message.animation or callback.message.video:
        await callback.message.answer(prompt_text)
        try:
            await callback.message.delete()
        except Exception:
            pass
    else:
        await callback.message.edit_text(prompt_text, reply_markup=None)
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
        # Vouchers/bundles (Ваучери лайт, Level Up Package, Evo Access, ...)
        # aren't priced per-diamond at all — their `diamonds` field is a
        # placeholder, not a real quantity. Mixing one into this list would
        # corrupt the interpolation curve for every custom-amount quote
        # (e.g. a "Level Up Package 30" entry would get treated as a real
        # 30-diamond breakpoint). Only genuine diamond packs (digit-led
        # name, same heuristic as their button label) belong here.
        diamond_packs = [p for p in products if p.name[:1].isdigit()]
        if not diamond_packs:
            await message.answer("Ҳозир нархгузорӣ дастрас нест. Бо админ тамос гиред.")
            await state.clear()
            return

        breakpoints = [(p.diamonds, p.price_somoni, p.cost_somoni) for p in diamond_packs]
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
    text = f"{amount} {unit} — {format_price(price)} сомонӣ.\n\nЛутфан {prompt} ирсол кунед:"

    async with get_session() as session:
        last_recipient = await get_last_recipient(session, message.from_user.id, category)

    if last_recipient:
        text += f"\n\nШумо пештар бо ин истифода карда будед: {last_recipient}"
        await message.answer(text, reply_markup=reuse_recipient_keyboard(last_recipient))
    else:
        await message.answer(text)


async def _recipient_prompt(category: ProductCategory) -> str:
    if category == ProductCategory.DIAMONDS:
        return "ID-и бозингари Free Fire-и худро (рақаме, ки дар профили худ мебинед)"
    if category == ProductCategory.FF_INDONESIA:
        return "ID-и бозингари Free Fire Indonesia-и худро (рақаме, ки дар профили худ мебинед)"
    if category == ProductCategory.BIGO_LIVE:
        return "ID-и Bigo Live-и худро (рақаме, ки дар профили худ мебинед)"
    return (
        "Username-и Telegram-и худро (бе @)"
    )


@router.callback_query(F.data.regexp(r"^product:\d+$"))
async def choose_product(callback: CallbackQuery, state: FSMContext) -> None:
    # Acknowledge immediately so Telegram's button spinner never appears stuck.
    await callback.answer()
    try:
        product_id = int(callback.data.split(":", 1)[1])
        async with get_session() as session:
            product = await get_product(session, product_id)
            if product is not None:
                last_recipient = await get_last_recipient(
                    session, callback.from_user.id, product.category
                )

        if product is None or not product.is_active:
            await callback.message.answer("Ин маҳсулот дастрас нест. Лутфан рӯйхатро аз нав кушоед.")
            return

        await state.update_data(product_id=product.id)
        await state.set_state(OrderFlow.entering_player_id)
        prompt = await _recipient_prompt(product.category)
        text = (
            "✅ <b>Фармоиши шумо</b>\n"
            f"🎮 Бозӣ: <b>{html.escape('Free Fire Indonesia' if product.category == ProductCategory.FF_INDONESIA else 'Free Fire' if product.category == ProductCategory.DIAMONDS else product.category.value.replace('_', ' ').title())}</b>\n"
            f"💎 Баста: <b>{html.escape(product.display_name)}</b>\n"
            f"💰 Нарх: <b>{format_price(product.price_somoni)} сомонӣ</b>\n\n"
            f"🆔 Лутфан {prompt} худро фиристед:"
        )
        markup = None
        if last_recipient:
            text += f"\n\nШумо пештар бо ин истифода карда будед: {last_recipient}"
            markup = reuse_recipient_keyboard(last_recipient)

        # Always send the next step as a fresh text message. This works whether
        # Telegram attached the button to text, photo, or a media-group message.
        await callback.message.answer(text, reply_markup=markup)
        try:
            await callback.message.delete()
        except Exception:
            pass
    except Exception:
        # Keep the customer informed rather than leaving a silent, dead button.
        await callback.message.answer(
            "⚠️ Ҳангоми интихоби баста хато шуд. Лутфан /start фиристед ва дубора кӯшиш кунед."
        )
        raise


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
    offer_wallet = user is not None and float(user.wallet_balance_somoni or 0) >= total_price > 0
    category = products[0].category
    ff_region = data.get("ff_region")

    player_name = None
    if category == ProductCategory.DIAMONDS:
        mapped = next((p for p in products if p.fzr_category_id), None)
        if mapped:
            player_name = await _try_validate_player_id(mapped.fzr_category_id, recipient)

    recipient_label = (
        "📱 Username" if category == ProductCategory.TELEGRAM else "🆔 ID"
    )
    confirm_lines = ["🧾 <b>Тафсилоти фармоиш</b>", f"{recipient_label}: <code>{html.escape(recipient)}</code>"]
    if player_name:
        confirm_lines.append(f"👤 Номи бозигар: <b>{html.escape(player_name)}</b>")
    game_label = ("Free Fire Indonesia" if category == ProductCategory.FF_INDONESIA else "Free Fire" if category == ProductCategory.DIAMONDS else category.value.replace("_", " ").title())
    confirm_lines.append(f"🎮 Бозӣ: <b>{html.escape(game_label)}</b>")
    if category in (ProductCategory.DIAMONDS, ProductCategory.FF_INDONESIA) and ff_region:
        confirm_lines.append(f"🌍 Регион: <b>{html.escape(ff_region)}</b>")
    confirm_lines.append("")
    for p in products:
        confirm_lines.append(f"💎 Баста: <b>{html.escape(p.display_name)}</b>")
    confirm_lines.append(f"📦 Теъдод: <b>{len(products)}</b>")
    confirm_lines.append(f"💰 Ҳамагӣ: <b>{format_price(total_price)} сомонӣ</b>")
    confirm_lines.append("")
    confirm_lines.append("☑️ Маълумот дуруст аст?")
    await answer_target.answer(
        "\n".join(confirm_lines),
        reply_markup=confirm_order_keyboard(offer_balance_payment=offer_balance, offer_wallet_payment=offer_wallet),
    )


@router.message(OrderFlow.entering_player_id, F.text)
async def enter_player_id(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    cart_ids = data.get("cart_product_ids")
    async with get_session() as session:
        product = await get_product(session, cart_ids[0] if cart_ids else data["product_id"])

    recipient = message.text.strip()

    if product.category in (ProductCategory.DIAMONDS, ProductCategory.BIGO_LIVE, ProductCategory.FF_INDONESIA):
        if not recipient.isdigit() or not (5 <= len(recipient) <= 15):
            await message.answer("ID-и нодуруст. Лутфан танҳо рақамҳои ID-и худро ворид кунед.")
            return
    else:
        recipient = recipient.removeprefix("@")
        if not (5 <= len(recipient) <= 32) or not recipient.replace("_", "").isalnum():
            await message.answer("Username-и нодуруст. Лутфан username-и дурусти Telegram-ро (бе @) нависед.")
            return

    if product.category in (ProductCategory.DIAMONDS, ProductCategory.FF_INDONESIA):
        await state.update_data(
            ff_player_id=recipient,
            ff_region="СНГ" if product.category == ProductCategory.DIAMONDS else "Индонезия",
        )

    await _finalize_recipient(state, message.from_user.id, recipient, message)


@router.callback_query(OrderFlow.entering_player_id, F.data.startswith("reuseid:"))
async def reuse_saved_id(callback: CallbackQuery, state: FSMContext) -> None:
    recipient = callback.data.split(":", 1)[1]
    data = await state.get_data()
    cart_ids = data.get("cart_product_ids")
    async with get_session() as session:
        product = await get_product(session, cart_ids[0] if cart_ids else data["product_id"])
    if product and product.category in (ProductCategory.DIAMONDS, ProductCategory.FF_INDONESIA):
        await state.update_data(
            ff_player_id=recipient,
            ff_region="СНГ" if product.category == ProductCategory.DIAMONDS else "Индонезия",
        )
    await _finalize_recipient(state, callback.from_user.id, recipient, callback.message)
    await callback.answer()
    await callback.answer()


@router.callback_query(F.data.in_({"region:ff_cis", "region:ff_indonesia"}))
async def choose_free_fire_region(callback: CallbackQuery, state: FSMContext) -> None:
    # Region is selected once, before package selection and player ID entry.
    region = "СНГ" if callback.data == "region:ff_cis" else "Индонезия"
    category = (
        ProductCategory.DIAMONDS if region == "СНГ"
        else ProductCategory.FF_INDONESIA
    )
    await state.clear()
    await _open_catalog(
        callback, state, category,
        f"{'🌐' if region == 'СНГ' else '🇮🇩'} Бастаҳои Free Fire — {region}:",
    )
    await state.update_data(ff_region=region)


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
    await callback.message.edit_text("Фармоиш бекор карда шуд.", reply_markup=None)
    await callback.answer()


async def _cart_products(session, data: dict) -> list[Product]:
    cart_ids = data.get("cart_product_ids")
    if cart_ids:
        return [await get_product(session, pid) for pid in cart_ids]
    return [await get_product(session, data["product_id"])]


@router.callback_query(OrderFlow.confirming, F.data == "order:pay_balance")
async def pay_with_balance(callback: CallbackQuery, state: FSMContext) -> None:
    if await _blocked_for_maintenance(callback, state):
        return
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
    summary = "\n".join(f"📦 {p.display_name} — {format_price(p.price_somoni)} сомонӣ" for p in products)
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
        f"{summary}\n\nДар 1-5 дақиқа ба шумо мерасад.",
        reply_markup=None,
    )
    await callback.answer()


async def _blocked_for_maintenance(callback: CallbackQuery, state: FSMContext) -> bool:
    """Admin-toggled via /maintenance — blocks NEW payments (e.g. while a
    FazerCards balance top-up is stuck and new orders couldn't be
    delivered anyway) without touching catalog browsing or orders already
    in progress. Checked right at the payment step itself, not earlier in
    the flow, so browsing/product selection keeps working normally."""
    async with get_session() as session:
        maintenance = await get_setting(session, "maintenance_mode", "0")
    if maintenance != "1":
        return False

    await state.clear()
    await callback.message.edit_text(
        "🛠 Мутаассифона, ҳоло бот муваққатан дар ҳоли таъмирот аст (мушкили техникӣ бо провайдер). "
        "Лутфан баъд аз чанд соат аз нав кӯшиш кунед.\n\n"
        "ℹ️ Агар шумо АЛЛАКАЙ пардохта бошед — нигарон нашавед: фармоишатон дар навбат аст, "
        "баъд аз ҳалли мушкилот алмаз/хидмататон мерасад.",
        reply_markup=back_to_menu_keyboard(),
    )
    await callback.answer()
    return True


@router.callback_query(OrderFlow.confirming, F.data == "order:pay_wallet")
async def pay_with_wallet(callback: CallbackQuery, state: FSMContext) -> None:
    """Atomically debit the customer's prepaid wallet and create paid order(s)."""
    if await _blocked_for_maintenance(callback, state):
        return
    data = await state.get_data()
    from bot.db.models import Order, WalletTransaction

    async with get_session() as session:
        products = await _cart_products(session, data)
        if not products or any(p is None or not p.is_active for p in products):
            await callback.answer("Маҳсулот дигар дастрас нест.", show_alert=True)
            return
        total = round(sum(p.price_somoni for p in products), 2)
        locked = await session.execute(
            select(User).where(User.id == callback.from_user.id).with_for_update()
        )
        user = locked.scalar_one_or_none()
        balance = round(float(user.wallet_balance_somoni or 0), 2) if user else 0.0
        if user is None or total <= 0 or balance < total:
            await callback.answer(
                f"Баланс кофӣ нест. Баланс: {balance:.2f} сомонӣ; лозим: {total:.2f}.",
                show_alert=True,
            )
            return

        user.wallet_balance_somoni = round(balance - total, 2)
        group_id = uuid.uuid4().hex if len(products) > 1 else None
        orders = []
        for product in products:
            order = Order(
                user_id=callback.from_user.id, product_id=product.id,
                ff_player_id=data["ff_player_id"], amount_somoni=product.price_somoni,
                payment_provider="wallet_telegram", paid_with_referral_balance=False,
                status=OrderStatus.PAID, cart_group_id=group_id,
            )
            session.add(order)
            orders.append(order)
        await session.flush()
        ledger_ref = f"tg_wallet_checkout:{callback.from_user.id}:{uuid.uuid4().hex}"
        session.add(WalletTransaction(
            user_id=callback.from_user.id, amount_somoni=-total,
            kind="telegram_purchase", reference=ledger_ref,
            note="orders:" + ",".join(str(o.id) for o in orders),
        ))
        if len(orders) > 1:
            orders[0].amount_somoni = total
            for sibling in orders[1:]:
                sibling.amount_somoni = 0.0
        await session.commit()
        await session.refresh(orders[0])

    await state.clear()
    primary = orders[0]
    summary = "\n".join(f"📦 {p.display_name} — {format_price(p.price_somoni)} сомонӣ" for p in products)
    if config.admin_chat_id:
        try:
            await callback.bot.send_message(
                config.admin_chat_id,
                f"🆕 Фармоиши #{primary.id} (пардохт аз ҳамёни муштарӣ — тасдиқшуда)\n"
                f"👤 Мизоҷ: {callback.from_user.full_name} (@{callback.from_user.username or '—'}, id={callback.from_user.id})\n"
                f"{summary}\n🎮 {primary.ff_player_id}\n\n"
                "Лутфан иҷро карда, 'Delivered'-ро зер кунед.",
                reply_markup=admin_order_keyboard(primary),
            )
        except Exception:
            pass
    await callback.message.edit_text(
        f"✅ Фармоиш #{primary.id} қабул шуд. Аз баланс {total:.2f} сомонӣ кам шуд.\n"
        "Таҳвил пас аз иҷрои фармоиш тасдиқ мешавад."
    )
    await callback.answer("Пардохт аз баланс қабул шуд.")


@router.callback_query(OrderFlow.confirming, F.data == "order:confirm")
async def confirm_order(callback: CallbackQuery, state: FSMContext) -> None:
    if await _blocked_for_maintenance(callback, state):
        return
    await callback.message.edit_text(
        "💳 <b>Интихоби усули пардохт</b>\n\n"
        "🏦 Бонки худро интихоб кунед:\n"
        "• 💙 <b>Душанбе Сити</b> — пайванди пардохт, агар дастрас бошад\n"
        "• 🟢 <b>Алиф Мобайл</b> — гузаронидан тавассути Алиф\n\n"
        "📌 Баъд аз пардохт расидро (скриншот) ба ҳамин бот фиристед. "
        "Фармоиш пас аз санҷиши пардохт тасдиқ мешавад.\n\n"
        "⬅️ Агар мехоҳед баргардед, тугмаи поёнро пахш кунед.",
        reply_markup=payment_method_keyboard(),
    )
    await callback.answer()


@router.callback_query(OrderFlow.confirming, F.data.startswith("paymethod:"))
async def choose_payment_method(callback: CallbackQuery, state: FSMContext) -> None:
    bank_hint = callback.data.split(":", 1)[1]
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
                bank_hint=bank_hint,
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

    invoice = await provider.create_invoice(primary.id, total, bank_hint=bank_hint)

    await state.update_data(order_id=primary.id)
    await state.set_state(OrderFlow.awaiting_payment_proof)
    bank_label = {"dc": "💙 Душанбе Сити", "alif": "🟢 Алиф Мобайл"}.get(bank_hint, "🏦 Бонк")
    text = (
        f"🧾 <b>Фармоиш #{primary.id}</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"🏦 Усули пардохт: <b>{bank_label}</b>\n"
        f"💰 Маблағ: <b>{total:.2f} сомонӣ</b>\n\n"
        f"{invoice.instructions}\n\n"
        "📷 Пас аз пардохт расиди воқеиро фиристед. Агар бонки дигарро истифода бурдан хоҳед, фармоишро бекор карда аз нав оғоз кунед."
    )
    if invoice.pay_url:
        await callback.message.edit_text(text, reply_markup=payment_link_keyboard(invoice.pay_url))
    else:
        await callback.message.edit_text(text, reply_markup=None)
    await callback.answer()


@router.message(OrderFlow.awaiting_payment_proof)
async def receive_payment_proof(message: Message, state: FSMContext) -> None:
    import hashlib

    from bot.db.repo import (
        find_duplicate_proof,
        get_order,
        get_orders_by_group,
        set_payment_proof_hash,
        set_proof_submitted,
    )

    data = await state.get_data()
    order_id = data.get("order_id")
    if not order_id:
        return

    if not message.photo and not message.document:
        # A stray text message here (a question, "Номер?", etc.) is not a
        # receipt. Forwarding it to the admin as if it were one used to
        # silently consume this order's awaiting-proof state and clear it —
        # so the customer's *real* screenshot, sent right after, no longer
        # matched this handler and never reached the admin at all. Keep the
        # state open and ask again instead of eating it on a non-photo reply.
        await message.answer("Лутфан расми (скриншот)-и расиди пардохтро равон кунед — на матн.")
        return

    async with get_session() as session:
        order = await get_order(session, order_id)
        if order.status != OrderStatus.AWAITING_PAYMENT:
            # The SMS auto-confirm webhook (bank SMS forwarded from the
            # admin's phone) already matched and confirmed this order —
            # independently of this photo, which just arrived a little
            # late. Forwarding it to the admin now would be a confusing,
            # redundant "still reviewing" message for something already
            # done. Tell the customer it's already handled and stop.
            await state.clear()
            if order.status == OrderStatus.DELIVERED:
                await message.answer("✅ Ин фармоиш аллакай тасдиқ ва ирсол шудааст — расми чек лозим нест.")
            else:
                await message.answer(
                    "✅ Пардохти шумо аллакай худкор тасдиқ шудааст (аз рӯи SMS-и бонк). "
                    "Расми чек лозим нест — фармоиш дар ҳоли коркард аст."
                )
            return
        amount_to_match = order.amount_somoni

    # Actively cross-check against SMS the webhook already saw (or is
    # about to see) — now that we know exactly which order this
    # screenshot is for, we can poll for a few seconds instead of relying
    # entirely on the webhook's own one-shot amount match. Mirrors a rival
    # bot's "🔍 system is searching, 5-30 seconds" UX: says so up front,
    # then actually checks repeatedly instead of just claiming to.
    from datetime import datetime, timedelta, timezone as _tz

    from bot.db.repo import find_unmatched_sms_for_amount, mark_sms_matched

    await message.answer(
        "✅ Чек қабул шуд!\n\n"
        "🔍 Системаи мо ҳозир пардохти шуморо худкор ҷустуҷӯ мекунад — одатан 5-30 сония мегирад.\n"
        "Натиҷа ҳозир хабар дода мешавад..."
    )

    sms_match = None
    for attempt in range(_PROOF_SMS_POLL_ATTEMPTS):
        if attempt > 0:
            await asyncio.sleep(_PROOF_SMS_POLL_DELAY_SECONDS)
        since = datetime.now(_tz.utc) - timedelta(minutes=config.sms_match_window_minutes)
        async with get_session() as session:
            sms_match = await find_unmatched_sms_for_amount(session, amount_to_match, since)
            if sms_match is not None:
                await mark_sms_matched(session, sms_match, order_id)
        if sms_match is not None:
            break

    if sms_match is not None:
        from bot.services.fulfillment import confirm_and_deliver

        await state.clear()
        result = await confirm_and_deliver(message.bot, order_id, payment_reference=sms_match.kod)
        if config.admin_chat_id and result is not None:
            status_note = "автоматӣ ирсол шуд" if result.auto_delivered else "лутфан дастӣ ирсол кунед ва 'Delivered'-ро занед"
            await message.bot.send_message(
                config.admin_chat_id,
                f"🤖 Фармоиши #{order_id} тавассути чеки мизоҷ + СМС-и қаблан омада худкор тасдиқ шуд — {status_note}.",
            )
        return

    async with get_session() as session:
        order = await get_order(session, order_id)
        group = await get_orders_by_group(session, order.cart_group_id) if order.cart_group_id else [order]
        products = [await get_product(session, o.product_id) for o in group]
        # Always list what was actually ordered — this used to only show
        # for multi-item carts (len(group) > 1), so a single-product order
        # (the normal case) gave the admin no way to tell which package the
        # screenshot's amount was even for without looking it up by hand.
        items_summary = "\n" + "\n".join(
            f"📦 {p.display_name} — {o.amount_somoni:.2f} сомонӣ" if o.amount_somoni else f"📦 {p.display_name}"
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
        order = await set_proof_submitted(session, order)

    if config.admin_chat_id:
        if message.photo:
            await message.bot.send_photo(
                config.admin_chat_id,
                photo=message.photo[-1].file_id,
                caption=caption,
                reply_markup=admin_order_keyboard(order),
            )
        else:
            await message.bot.send_document(
                config.admin_chat_id,
                document=message.document.file_id,
                caption=caption,
                reply_markup=admin_order_keyboard(order),
            )

    await message.answer(
        "Ташаккур! Расиди шумо ба админ фиристода шуд. Пас аз тасдиқ маҳсулоти шумо ирсол мешавад."
    )
    # Don't clear the state yet — stay in "awaiting admin review" so that if
    # the customer writes back in the meantime (e.g. "паём наомад?"), the
    # handler below can reassure them instead of the message going
    # unanswered. fulfillment.py clears this once the admin actually
    # confirms/rejects the order.
    await state.set_state(OrderFlow.awaiting_admin_review)
    await state.update_data(order_id=order_id)


@router.message(OrderFlow.awaiting_admin_review)
async def awaiting_admin_review_reply(message: Message, state: FSMContext) -> None:
    await message.answer(
        "⏳ Чеки шумо аллакай ба админ фиристода шудааст ва дар навбати баррасӣ қарор дорад. "
        "Лутфан то тасдиқ интизор шавед — ин одатан 1-5 дақиқа тӯл мекашад. "
        "Агар хеле дер шуд, аз 🆘 Дастгирӣ истифода баред."
    )


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

    await callback.message.edit_text("Хуб, ташаккур барои харид! 🙏", reply_markup=None)
    await callback.answer()


@router.callback_query(F.data.regexp(r"^receipt:\d+$"))
async def show_receipt(callback: CallbackQuery) -> None:
    """A shareable proof-of-purchase, inspired by a rival bot's polished
    post-delivery "чек" card — rendered as a branded image (see
    bot/services/receipt_image.py); falls back to a formatted text message
    if image generation ever fails (missing font package, etc.) so a
    rendering glitch never breaks the customer's flow."""
    from aiogram.types import BufferedInputFile

    from bot.db.repo import get_order
    from bot.keyboards import BANK_HINT_NAMES
    from bot.services.receipt_image import generate_receipt_image

    order_id = int(callback.data.split(":", 1)[1])
    async with get_session() as session:
        order = await get_order(session, order_id)
        if order is None:
            await callback.answer("Фармоиш ёфт нашуд.", show_alert=True)
            return
        group = await _resolve_group_orders(session, order)
        products = {o.id: await get_product(session, o.product_id) for o in group}

    if order.bank_hint:
        method = BANK_HINT_NAMES.get(order.bank_hint, order.bank_hint)
    elif order.payment_provider == "referral_balance":
        method = "Баланси реферал"
    else:
        method = "Дастӣ"

    total = sum(o.amount_somoni for o in group) or order.amount_somoni
    when = order.updated_at.strftime("%d.%m.%Y • %H:%M") if order.updated_at else ""
    items = [products[o.id].display_name for o in group]

    try:
        png = generate_receipt_image(
            order_id=order.id,
            title="Фармоиш бо муваффақият анҷом ёфт!",
            items=items,
            recipient_id=order.ff_player_id,
            payment_method=method,
            when_text=when,
            amount_somoni=total,
        )
        await callback.message.answer_photo(BufferedInputFile(png, filename=f"receipt_{order.id}.png"))
    except Exception:
        items_block = "\n".join(f"  • {name}" for name in items) if len(items) > 1 else items[0]
        receipt = (
            "🧾 <b>Чеки фармоиш</b>\n\n"
            "✅ <b>МУВАФФАҚ</b>\n\n"
            f"Фармоиш №: <code>{order.id}</code>\n"
            f"Маҳсулот: {items_block}\n"
            f"ID: <code>{order.ff_player_id}</code>\n"
            f"Усули пардохт: {method}\n"
            f"Сана: {when}\n"
            f"Маблағ: {total:.2f} сомонӣ\n\n"
            "🙏 Ташаккур барои харид!"
        )
        await callback.message.answer(receipt)
    await callback.answer()


async def _resolve_group_orders(session, order) -> list:
    if not order.cart_group_id:
        return [order]
    from bot.db.repo import get_orders_by_group

    return await get_orders_by_group(session, order.cart_group_id)


@router.message(Command("myorders"))
async def my_orders(message: Message) -> None:
    text = await _format_orders_text(message.from_user.id)
    await message.answer(text)


@router.callback_query(~F.data.startswith("wallet:"), ~F.data.startswith("api:"), ~F.data.startswith("payment:"), ~F.data.startswith("bankchange:"))
async def stale_callback_fallback(callback: CallbackQuery, state: FSMContext) -> None:
    """Registered last on purpose: only reached when no handler above
    matched. That happens when a button belongs to a screen the user has
    since navigated away from (its FSM-state filter no longer matches) —
    without this, Telegram leaves that tap's loading spinner stuck forever
    since nothing ever calls callback.answer() on it. Answer it and send a
    fresh main menu instead of leaving the user stuck."""
    await callback.answer("Ин тугма кӯҳна шудааст, лутфан аз нав кушоед 👇", show_alert=True)
    await state.clear()
    await callback.message.answer(WELCOME_TEXT)


# --- Prepaid wallet and customer API access ---------------------------------

@router.message(Command("wallet"))
async def wallet_command(message: Message) -> None:
    """Show wallet balance and create a manual top-up request."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    from bot.db.models import WalletTransaction
    from bot.db.session import get_session

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"➕ Пур кардан: {amount} сомонӣ", callback_data=f"wallet:topup:{amount}")]
        for amount in (50, 100, 200, 500)
    ])
    async with get_session() as session:
        await upsert_user(session, message.from_user.id, message.from_user.username, message.from_user.full_name)
        user = await get_user(session, message.from_user.id)
        balance = float(user.wallet_balance_somoni or 0) if user else 0.0
        rows = await session.execute(
            select(WalletTransaction).where(WalletTransaction.user_id == message.from_user.id)
            .order_by(WalletTransaction.created_at.desc()).limit(5)
        )
        history = list(rows.scalars().all())
    history_text = "\n".join(
        f"• {tx.amount_somoni:+.2f}с — {tx.kind} — {tx.created_at.strftime('%d.%m %H:%M')}"
        for tx in history
    ) or "Ҳоло амалиёт нест."
    await message.answer(
        f"👛 <b>Ҳамёни FF MARGEDAR</b>\nБаланс: <b>{balance:.2f} сомонӣ</b>\n\n"
        f"<b>Амалиёти охирин:</b>\n{history_text}\n\n"
        "Маблағи пуркуниро интихоб кунед. Пуркунӣ дастӣ тасдиқ мешавад: "
        "админ бояд ворид шудани маблағро дар ҳисоби қабулкунанда санҷад.",
        reply_markup=keyboard,
    )


@router.callback_query(F.data.startswith("wallet:topup:"))
async def wallet_topup_request(callback: CallbackQuery, state: FSMContext) -> None:
    from datetime import datetime, timezone
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    from bot.db.models import WalletTopupRequest
    from bot.db.session import get_session

    try:
        amount = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Маблағ нодуруст аст.", show_alert=True)
        return
    if amount not in (50, 100, 200, 500):
        await callback.answer("Маблағ иҷозат дода нашудааст.", show_alert=True)
        return
    async with get_session() as session:
        await upsert_user(session, callback.from_user.id, callback.from_user.username, callback.from_user.full_name)
        req = WalletTopupRequest(
            user_id=callback.from_user.id, amount_somoni=float(amount),
            status="pending", created_at=datetime.now(timezone.utc),
        )
        session.add(req)
        await session.commit()
        await session.refresh(req)
        req_id = req.id

    if config.receiving_card_number:
        payment_details = f"💳 Рақам/ҳисоби қабул: <code>{html.escape(config.receiving_card_number)}</code>\n"
    else:
        payment_details = (
            "⚠️ Рақами қабул дар Render Environment (RECEIVING_CARD_NUMBER) танзим нашудааст. "
            "Пеш аз пардохт бо дастгирӣ тамос гиред.\n"
        )
    await state.set_state(WalletTopupFlow.awaiting_proof)
    await state.update_data(wallet_topup_request_id=req_id)
    await callback.message.answer(
        f"👛 <b>Пур кардани баланс — {amount} сомонӣ</b>\n"
        f"Дархост: <code>#{req_id}</code>\n\n"
        f"{payment_details}\n"
        "1) Маблағи дақиқро ба ҳисоби қабулкунанда гузаронед.\n"
        "2) Скриншот ё файли чеки пардохтро ҳамин ҷо фиристед.\n"
        "3) Админ пардохтро месанҷад; баланс танҳо баъди тасдиқи воқеии маблағ зиёд мешавад.\n\n"
        "Агар ҳоло пардохт кардан нахоҳед, /start фиристед.",
    )
    await callback.answer("Дархост сохта шуд.")


@router.message(WalletTopupFlow.awaiting_proof)
async def wallet_topup_receive_proof(message: Message, state: FSMContext) -> None:
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    from bot.db.models import WalletTopupRequest
    from bot.db.session import get_session

    data = await state.get_data()
    req_id = data.get("wallet_topup_request_id")
    if not req_id:
        await state.clear()
        await message.answer("Дархост ёфт нашуд. Аз нав /wallet-ро кушоед.")
        return
    if not message.photo and not message.document:
        await message.answer("Лутфан скриншот ё файли чеки пардохтро фиристед.")
        return

    async with get_session() as session:
        req = await session.get(WalletTopupRequest, int(req_id))
        if req is None or req.user_id != message.from_user.id or req.status != "pending":
            await state.clear()
            await message.answer("Ин дархост дигар фаъол нест. Барои пуркунии нав /wallet-ро кушоед.")
            return
        amount = req.amount_somoni

    admin_keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Тасдиқ ва пур кардани баланс", callback_data=f"wallet:approve:{req_id}"),
        InlineKeyboardButton(text="❌ Рад", callback_data=f"wallet:reject:{req_id}"),
    ]])
    caption = (
        f"🧾 <b>Чеки пуркунии баланс #{req_id}</b>\n"
        f"💰 Маблағ: {amount:.2f} сомонӣ\n"
        f"👤 Корбар: {message.from_user.full_name} (@{message.from_user.username or '—'})\n"
        f"🆔 User ID: <code>{message.from_user.id}</code>\n\n"
        "❗️ Пеш аз тасдиқ, маблағи воқеан воридшударо дар барномаи бонк санҷед."
    )
    if not config.admin_chat_id:
        await message.answer("⚠️ Админ дар бот танзим нашудааст (ADMIN_CHAT_ID). Чек қабул шуд, аммо ба админ фиристода нашуд.")
        return
    try:
        if message.photo:
            await message.bot.send_photo(config.admin_chat_id, message.photo[-1].file_id, caption=caption, reply_markup=admin_keyboard)
        else:
            await message.bot.send_document(config.admin_chat_id, message.document.file_id, caption=caption, reply_markup=admin_keyboard)
    except Exception:
        await message.answer("Фиристодани чек ба админ муваффақ нашуд. Лутфан баъдтар дубора кӯшиш кунед.")
        return

    await state.clear()
    await message.answer(f"✅ Чек барои дархости #{req_id} ба админ фиристода шуд. Баланс баъди санҷиш пур мешавад.")


@router.message(Command("apikey"))
async def api_key_command(message: Message) -> None:
    """Generate or rotate a personal API secret; display it once only."""
    import hashlib
    import secrets
    from bot.db.models import CustomerApiKey
    from bot.db.session import get_session

    raw_key = "almt_" + secrets.token_urlsafe(32)
    async with get_session() as session:
        await upsert_user(session, message.from_user.id, message.from_user.username, message.from_user.full_name)
    digest = hashlib.sha256(raw_key.encode()).hexdigest()
    prefix = raw_key[:12]
    async with get_session() as session:
        row = await session.execute(
            select(CustomerApiKey).where(CustomerApiKey.user_id == message.from_user.id)
        )
        api_key = row.scalar_one_or_none()
        if api_key is None:
            api_key = CustomerApiKey(user_id=message.from_user.id, key_hash=digest, key_prefix=prefix)
            session.add(api_key)
        else:
            api_key.key_hash = digest
            api_key.key_prefix = prefix
            api_key.revoked_at = None
        await session.commit()

    await message.answer(
        "🔐 <b>API-ключи шахсӣ</b>\n\n"
        "Калиди нави API сохта шуд. Онро танҳо як бор мебинед — дар ҷои махфӣ нигоҳ доред "
        "ва ба дигарон/скриншот нафиристед. Калиди пешина бекор шуд.\n\n"
        f"<code>{raw_key}</code>\n\n"
        "API-и шумо баланс ва каталоги маҳсулотро мехонад ва метавонад аз ҳамён фармоиш созад. "
        "Ҳангоми истифода калидро танҳо дар сервери шахсии худ нигоҳ доред."
    )


@router.callback_query(F.data == "wallet:open")
async def wallet_open_button(callback: CallbackQuery) -> None:
    """Open the same wallet/top-up choices as /wallet from the main menu."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    from bot.db.models import WalletTransaction
    from bot.db.session import get_session

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"➕ Пур кардан: {amount} сомонӣ", callback_data=f"wallet:topup:{amount}")]
        for amount in (50, 100, 200, 500)
    ])
    async with get_session() as session:
        await upsert_user(
            session, callback.from_user.id, callback.from_user.username,
            callback.from_user.full_name,
        )
        user = await get_user(session, callback.from_user.id)
        balance = float(user.wallet_balance_somoni or 0) if user else 0.0
        rows = await session.execute(
            select(WalletTransaction)
            .where(WalletTransaction.user_id == callback.from_user.id)
            .order_by(WalletTransaction.created_at.desc()).limit(5)
        )
        history = list(rows.scalars().all())
    history_text = "\\n".join(
        f"• {tx.amount_somoni:+.2f}с — {tx.kind} — {tx.created_at.strftime('%d.%m %H:%M')}"
        for tx in history
    ) or "Ҳоло амалиёт нест."
    await callback.message.answer(
        f"👛 <b>Ҳамёни FF MARGEDAR</b>\\nБаланс: <b>{balance:.2f} сомонӣ</b>\\n\\n"
        f"<b>Амалиёти охирин:</b>\\n{history_text}\\n\\n"
        "Маблағро интихоб кунед. Пуркунӣ ҳоло дастӣ баррасӣ мешавад; "
        "баланс танҳо баъди тасдиқи админ зиёд мешавад.",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(F.data == "api:open")
async def api_open_button(callback: CallbackQuery) -> None:
    await callback.message.answer(
        "🔑 Барои сохтани API-ключи шахсии худ фармони /apikey-ро пахш кунед.\\n"
        "Калид танҳо як бор нишон дода мешавад; онро ба касе нафиристед."
    )
    await callback.answer()


@router.callback_query(OrderFlow.awaiting_payment_proof, F.data == "payment:change_bank")
async def payment_change_bank(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    order_id = data.get("order_id")
    if not order_id:
        await callback.answer("Фармоиш ёфт нашуд. Аз нав оғоз кунед.", show_alert=True)
        return
    await callback.message.edit_text(
        "🔄 <b>Иваз кардани бонк</b>\n\n"
        "🏦 Бонки дигарро интихоб кунед. Фармоиши шумо ҳамон мемонад — "
        "фармоиши нав сохта намешавад.\n\n"
        "💙 Душанбе Сити\n🟢 Алиф Мобайл\n💳 Ҳумо\n🏦 Эсхата Бонк\n🏦 Спитамен Бонк",
        reply_markup=change_payment_method_keyboard(),
    )
    await callback.answer()


@router.callback_query(OrderFlow.awaiting_payment_proof, F.data.startswith("bankchange:"))
async def payment_change_bank_selected(callback: CallbackQuery, state: FSMContext) -> None:
    bank_hint = callback.data.split(":", 1)[1]
    if bank_hint not in {key for key, _label in PAYMENT_METHODS}:
        await callback.answer("Ин бонк дастрас нест.", show_alert=True)
        return
    data = await state.get_data()
    order_id = data.get("order_id")
    if not order_id:
        await callback.answer("Фармоиш ёфт нашуд.", show_alert=True)
        return

    from bot.db.repo import get_order
    async with get_session() as session:
        order = await get_order(session, int(order_id))
        if order is None or order.status != OrderStatus.AWAITING_PAYMENT:
            await callback.answer("Ин фармоиш дигар интизори пардохт нест.", show_alert=True)
            return
        order.bank_hint = bank_hint
        await session.commit()
        amount = float(order.amount_somoni)

    invoice = await get_payment_provider().create_invoice(int(order_id), amount, bank_hint=bank_hint)
    await state.update_data(bank_hint=bank_hint)
    bank_label = BANK_HINT_NAMES.get(bank_hint, "🏦 Бонк")
    text = (
        f"🧾 <b>Фармоиш #{order_id}</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"🏦 Усули пардохт: <b>{bank_label}</b>\n"
        f"💰 Маблағ: <b>{amount:.2f} сомонӣ</b>\n\n"
        f"{invoice.instructions}\n\n"
        "📷 Пас аз пардохт расиди воқеиро фиристед."
    )
    await callback.message.edit_text(
        text,
        reply_markup=payment_link_keyboard(invoice.pay_url) if invoice.pay_url else payment_link_keyboard(),
    )
    await callback.answer("Бонк иваз шуд.")


@router.callback_query(OrderFlow.awaiting_payment_proof, F.data == "payment:back")
async def payment_change_bank_back(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    bank_hint = data.get("bank_hint", "dc")
    order_id = data.get("order_id")
    if not order_id:
        await callback.answer("Фармоиш ёфт нашуд.", show_alert=True)
        return
    from bot.db.repo import get_order
    async with get_session() as session:
        order = await get_order(session, int(order_id))
        if order is None:
            await callback.answer("Фармоиш ёфт нашуд.", show_alert=True)
            return
        amount = float(order.amount_somoni)
        bank_hint = order.bank_hint or bank_hint
    invoice = await get_payment_provider().create_invoice(int(order_id), amount, bank_hint=bank_hint)
    bank_label = {"dc": "💙 Душанбе Сити", "alif": "🟢 Алиф Мобайл"}.get(bank_hint, "🏦 Бонк")
    text = (
        f"🧾 <b>Фармоиш #{order_id}</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"🏦 Усули пардохт: <b>{bank_label}</b>\n"
        f"💰 Маблағ: <b>{amount:.2f} сомонӣ</b>\n\n"
        f"{invoice.instructions}\n\n"
        "📷 Пас аз пардохт расиди воқеиро фиристед."
    )
    await callback.message.edit_text(
        text,
        reply_markup=payment_link_keyboard(invoice.pay_url) if invoice.pay_url else payment_link_keyboard(),
    )
    await callback.answer()


@router.message(F.text)
async def ignore_unexpected_text(message: Message, state: FSMContext) -> None:
    """Ignore free-form chat text unless an earlier order/API handler expects it."""
    if await state.get_state() is None:
        # Users can still type in Telegram, but the shop does not process random
        # text. Navigation is through the persistent Menu button.
        return
