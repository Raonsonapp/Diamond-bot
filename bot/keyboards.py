from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from bot.config import config
from bot.db.models import Order, Product, ProductCategory
from bot.formatting import format_price


def persistent_menu_keyboard() -> ReplyKeyboardMarkup:
    """A single persistent bottom button, like the competitor bot's Menu button."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📋 Меню")]],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="📋 Менюро пахш кунед",
    )


def sponsor_gate_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📢 Ба канали отзивҳо ҳамроҳ шудан", url=config.review_channel_url)],
            [InlineKeyboardButton(text="✅ Санҷидан", callback_data="sponsor:check")],
        ]
    )


def terms_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✅ Қабул мекунам", callback_data="terms:accept")]]
    )


def main_menu_keyboard() -> InlineKeyboardMarkup:
    # Keep the original compact two-column layout; only nested/navigation
    # keyboards should use full-width buttons where needed.
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎮 Бозиҳо", callback_data="menu:games"),
                InlineKeyboardButton(text="✈️ Telegram", callback_data="menu:telegram"),
            ],
            [
                InlineKeyboardButton(text="👛 Ҳамён", callback_data="wallet:open"),
                InlineKeyboardButton(text="➕ Пур кардани баланс", callback_data="wallet:topup_menu"),
            ],
            [
                InlineKeyboardButton(text="🔑 API-и шахсӣ", callback_data="api:open"),
                InlineKeyboardButton(text="👤 Профил", callback_data="menu:profile"),
            ],
            [
                InlineKeyboardButton(text="🤝 Реферал", callback_data="menu:referral"),
                InlineKeyboardButton(text="🆘 Дастгирӣ", callback_data="menu:contact"),
            ],
            [
                InlineKeyboardButton(text="⭐ Отзив", callback_data="menu:reviews"),
                InlineKeyboardButton(text="❓ Саволҳои маъмул", callback_data="menu:faq"),
            ],
            [
                InlineKeyboardButton(text="ℹ️ Маълумот", callback_data="menu:about"),
            ],
        ]
    )


def review_channel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⭐ Канали отзивҳо", url=config.review_channel_url)],
            [InlineKeyboardButton(text="🔙 Ба меню", callback_data="menu:main")],
        ]
    )


def games_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💎 Free Fire — Алмазҳо", callback_data="menu:buy_diamonds")],
            [InlineKeyboardButton(text="🔙 Ба меню", callback_data="menu:main")],
        ]
    )


def telegram_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⭐ Telegram Stars", callback_data="menu:telegram_stars")],
            [InlineKeyboardButton(text="💎 Telegram Premium", callback_data="menu:telegram_premium")],
            [InlineKeyboardButton(text="🔙 Ба меню", callback_data="menu:main")],
        ]
    )


def profile_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📦 Фармоишҳои ман", callback_data="menu:myorders")],
            [InlineKeyboardButton(text="🏆 Топ харидорон", callback_data="menu:top_buyers")],
            [InlineKeyboardButton(text="🎖 Топ рефералдорон", callback_data="menu:top_referrers")],
            [InlineKeyboardButton(text="🔙 Ба меню", callback_data="menu:main")],
        ]
    )


def referral_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎖 Топ рефералдорон", callback_data="menu:top_referrers")],
            [InlineKeyboardButton(text="🔙 Ба меню", callback_data="menu:main")],
        ]
    )


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔙 Ба меню", callback_data="menu:main")]]
    )


def contact_keyboard() -> InlineKeyboardMarkup:
    """Offer Telegram-only support; never expose phone or WhatsApp details."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💬 Навиштан ба дастгирӣ дар Telegram", url=config.support_telegram_url)],
            [InlineKeyboardButton(text="📢 Канали мағоза", url=config.shop_channel_url)],
            [InlineKeyboardButton(text="🔙 Ба меню", callback_data="menu:main")],
        ]
    )

def _product_label(p: Product) -> str:
    # Keep compact, recognizable icons on every product button, similar to
    # the competitor's list: diamond packs use a diamond, vouchers a ticket.
    if p.name[:1].isdigit():
        bonus = f" (+{p.bonus_diamonds} бонус)" if p.bonus_diamonds else ""
        return f"💎 {p.diamonds}{bonus} {p.unit_label} — {format_price(p.price_somoni)} сомонӣ"
    name = p.name.lower()
    if "лайт" in name or "lite" in name or "ваучер" in name:
        icon = "🎟️"
    elif "ҳафта" in name or "weekly" in name:
        icon = "🗓️"
    elif "моҳ" in name or "monthly" in name:
        icon = "📅"
    else:
        icon = "🎁"
    return f"{icon} {p.name} — {format_price(p.price_somoni)} сомонӣ"


def products_keyboard(
    products: list[Product], category: ProductCategory, telegram_kind: str | None = None
) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=_product_label(p), callback_data=f"product:{p.id}")]
        for p in products
    ]
    # Free Fire (fixed official denominations only, per the admin) and
    # Bigo Live (FazerCards only sells fixed package sizes for it — no
    # sensible "any amount" quote to interpolate) both skip this button.
    # Telegram Premium is fixed 3/6/12-month plans too (any other number of
    # months isn't a real FazerCards offer) — only Stars is a flat rate
    # that any quantity in range can be quoted from.
    if category == ProductCategory.TELEGRAM and telegram_kind == "stars":
        rows.append(
            [InlineKeyboardButton(text="✏️ Миқдори дигар", callback_data=f"product:custom:{category.value}")]
        )
    if category == ProductCategory.DIAMONDS:
        rows.append(
            [InlineKeyboardButton(text="🛒 Якчанд бастаро якҷоя харидан", callback_data=f"cartmode:{category.value}")]
        )
    rows.append(
        [InlineKeyboardButton(text="⬅️ Бозгашт", callback_data="catalog:back")]
    )
    rows.append(
        [InlineKeyboardButton(text="🏠 Менюи асосӣ", callback_data="menu:main")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cart_select_keyboard(
    products: list[Product], category: ProductCategory, selected_ids: set[int]
) -> InlineKeyboardMarkup:
    rows = []
    for p in products:
        mark = "✅" if p.id in selected_ids else "⬜"
        rows.append(
            [InlineKeyboardButton(text=f"{mark} {_product_label(p)}", callback_data=f"cartitem:{p.id}")]
        )
    if selected_ids:
        total = sum(p.price_somoni for p in products if p.id in selected_ids)
        rows.append(
            [InlineKeyboardButton(text=f"🛍 Идома ({len(selected_ids)} — {format_price(total)} сомонӣ)", callback_data="cart:checkout")]
        )
    rows.append(
        [InlineKeyboardButton(text="🔙 Якто-якто харидан", callback_data=f"cartmode:exit:{category.value}")]
    )
    rows.append([InlineKeyboardButton(text="🔙 Ба меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reuse_recipient_keyboard(recipient: str, label_suffix: str = "") -> InlineKeyboardMarkup:
    # Accept either one legacy ID or a list of (player_id, in-game name).
    entries = recipient if isinstance(recipient, list) else [(recipient, None)]
    rows = []
    buttons = []
    for player_id, player_name in entries[:3]:
        name = f" · {player_name}" if player_name else ""
        buttons.append(InlineKeyboardButton(
            text=f"✅ {player_id}{name}"[:64],
            callback_data=f"reuseid:{player_id}",
        ))
    for i in range(0, len(buttons), 2):
        rows.append(buttons[i:i + 2])
    rows.extend([
        [InlineKeyboardButton(text="⬅️ Бозгашт ба бастаҳо", callback_data="catalog:back")],
        [InlineKeyboardButton(text="🏠 Менюи асосӣ", callback_data="menu:main")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_link_keyboard(pay_url: str | None = None) -> InlineKeyboardMarkup:
    rows = []
    if pay_url:
        rows.append([InlineKeyboardButton(text="💳 Кушодани пардохт", url=pay_url)])
    rows.append([InlineKeyboardButton(text="🔄 Бонки дигарро интихоб кардан", callback_data="payment:change_bank")])
    rows.append([InlineKeyboardButton(text="🏠 Ба меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def change_payment_method_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"bankchange:{key}")]
        for key, label in PAYMENT_METHODS
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Бозгашт ба пардохт", callback_data="payment:back")])
    rows.append([InlineKeyboardButton(text="🏠 Менюи асосӣ", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# All four route to the exact same receiving card (config.receiving_card_number)
# — Tajikistan's interbank card-to-card transfer works from any bank's app to
# any other bank's card, so there's no real difference in *where the money
# goes*, only in which bank the customer already has open. Keeping them as
# separate buttons (labelled with each bank's own name, same as a competitor
# bot's payment picker) rather than one generic "Пардохт" is purely so a
# customer immediately recognizes their own bank instead of wondering whether
# a "DC" card accepts a transfer from, say, their Eskhata app.
PAYMENT_METHODS = [
    ("dc", "💙 Душанбе Сити"),
    ("alif", "🟢 Алиф Мобайл"),
]
# Plain bank names (no emoji) for reuse outside the picker button labels —
# e.g. the post-delivery receipt's "Усули пардохт" line (see
# customer.show_receipt).
BANK_HINT_NAMES = {key: label.split(" ", 1)[1] for key, label in PAYMENT_METHODS}


def payment_method_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=label, callback_data=f"paymethod:{key}")] for key, label in PAYMENT_METHODS]
    rows.append([InlineKeyboardButton(text="⬅️ Бозгашт / бекор кардани фармоиш", callback_data="order:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def review_prompt_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏭ Гузарондан", callback_data=f"review:skip:{order_id}")]
        ]
    )


def receipt_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🧾 Чеки муваффақ", callback_data=f"receipt:{order_id}")]
        ]
    )


def free_fire_region_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🌐 СНГ · Тоҷикистон / Россия", callback_data="region:ff_cis")],
            [InlineKeyboardButton(text="🇮🇩 Индонезия · ID Server", callback_data="region:ff_indonesia")],
            [InlineKeyboardButton(text="⬅️ Бозгашт ба бозиҳо", callback_data="menu:games")],
            [InlineKeyboardButton(text="🏠 Менюи асосӣ", callback_data="menu:main")],
        ]
    )


def confirm_order_keyboard(offer_balance_payment: bool = False, offer_wallet_payment: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if offer_balance_payment:
        rows.append(
            [InlineKeyboardButton(text="💰 Пардохт аз баланси реферал", callback_data="order:pay_balance")]
        )
    if offer_wallet_payment:
        rows.append(
            [InlineKeyboardButton(text="👛 Пардохт аз ҳамён", callback_data="order:pay_wallet")]
        )
    rows.append([InlineKeyboardButton(text="✅ Тасдиқ (бо чек)", callback_data="order:confirm")])
    rows.append([InlineKeyboardButton(text="❌ Бекор кардани фармоиш", callback_data="order:cancel")])
    rows.append([InlineKeyboardButton(text="⬅️ Бозгашт ба бастаҳо", callback_data="catalog:back")])
    rows.append([InlineKeyboardButton(text="🏠 Менюи асосӣ", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_order_keyboard(order: Order) -> InlineKeyboardMarkup:
    rows = []
    if order.status.value == "awaiting_payment":
        rows.append(
            [InlineKeyboardButton(text="✅ Пардохт тасдиқ шуд", callback_data=f"admin:paid:{order.id}")]
        )
        rows.append(
            [InlineKeyboardButton(text="❌ Рад кардани пардохт", callback_data=f"admin:reject:{order.id}")]
        )
    elif order.status.value in ("paid", "delivering"):
        rows.append(
            [
                InlineKeyboardButton(
                    text="📦 Дода шуд (Delivered)", callback_data=f"admin:delivered:{order.id}"
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)
