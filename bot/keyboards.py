from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from bot.config import config
from bot.db.models import Order, Product, ProductCategory
from bot.formatting import format_price


def _button(*, text: str, style: str = "primary", **kwargs) -> InlineKeyboardButton:
    """Create a Telegram button with native blue/green/red styling (Bot API 9.4+)."""
    return _button(text=text, style=style, **kwargs)


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
            [_button(text="📢 Ба канали отзивҳо ҳамроҳ шудан", url=config.review_channel_url)],
            [_button(text="✅ Санҷидан", callback_data="sponsor:check")],
        ]
    )


def terms_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_button(text="✅ Қабул мекунам", callback_data="terms:accept")]]
    )


def main_menu_keyboard() -> InlineKeyboardMarkup:
    # Keep the original compact two-column layout; only nested/navigation
    # keyboards should use full-width buttons where needed.
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _button(text="🎮 Бозиҳо", callback_data="menu:games", style="success"),
                _button(text="✈️ Telegram", callback_data="menu:telegram"),
            ],
            [
                _button(text="👛 Ҳамён", callback_data="wallet:open"),
                _button(text="➕ Пур кардани баланс", callback_data="wallet:topup_menu", style="success"),
            ],
            [
                _button(text="🔑 API-и шахсӣ", callback_data="api:open"),
                _button(text="👤 Профил", callback_data="menu:profile"),
            ],
            [
                _button(text="🤝 Реферал", callback_data="menu:referral"),
                _button(text="🆘 Дастгирӣ", callback_data="menu:contact"),
            ],
            [
                _button(text="⭐ Отзив", callback_data="menu:reviews"),
                _button(text="❓ Саволҳои маъмул", callback_data="menu:faq"),
            ],
            [
                _button(text="ℹ️ Маълумот", callback_data="menu:about"),
            ],
        ]
    )


def review_channel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button(text="⭐ Канали отзивҳо", url=config.review_channel_url)],
            [_button(text="🔙 Ба меню", callback_data="menu:main")],
        ]
    )


def games_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button(text="💎 Free Fire — Алмазҳо", callback_data="menu:buy_diamonds")],
            [_button(text="🔙 Ба меню", callback_data="menu:main")],
        ]
    )


def telegram_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button(text="⭐ Telegram Stars", callback_data="menu:telegram_stars")],
            [_button(text="💎 Telegram Premium", callback_data="menu:telegram_premium")],
            [_button(text="🔙 Ба меню", callback_data="menu:main")],
        ]
    )


def profile_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button(text="📦 Фармоишҳои ман", callback_data="menu:myorders")],
            [_button(text="🏆 Топ харидорон", callback_data="menu:top_buyers")],
            [_button(text="🎖 Топ рефералдорон", callback_data="menu:top_referrers")],
            [_button(text="🔙 Ба меню", callback_data="menu:main")],
        ]
    )


def referral_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button(text="🎖 Топ рефералдорон", callback_data="menu:top_referrers")],
            [_button(text="🔙 Ба меню", callback_data="menu:main")],
        ]
    )


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_button(text="🔙 Ба меню", callback_data="menu:main")]]
    )


def contact_keyboard() -> InlineKeyboardMarkup:
    """Offer Telegram-only support; never expose phone or WhatsApp details."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button(text="💬 Навиштан ба дастгирӣ дар Telegram", url=config.support_telegram_url)],
            [_button(text="📢 Канали мағоза", url=config.shop_channel_url)],
            [_button(text="🔙 Ба меню", callback_data="menu:main")],
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
        [_button(text=_product_label(p), callback_data=f"product:{p.id}")]
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
            [_button(text="✏️ Миқдори дигар", callback_data=f"product:custom:{category.value}")]
        )
    if category == ProductCategory.DIAMONDS:
        rows.append(
            [_button(text="🛒 Якчанд бастаро якҷоя харидан", callback_data=f"cartmode:{category.value}")]
        )
    rows.append(
        [_button(text="⬅️ Бозгашт", callback_data="catalog:back")]
    )
    rows.append(
        [_button(text="🏠 Менюи асосӣ", callback_data="menu:main")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cart_select_keyboard(
    products: list[Product], category: ProductCategory, selected_ids: set[int]
) -> InlineKeyboardMarkup:
    rows = []
    for p in products:
        mark = "✅" if p.id in selected_ids else "⬜"
        rows.append(
            [_button(text=f"{mark} {_product_label(p)}", callback_data=f"cartitem:{p.id}")]
        )
    if selected_ids:
        total = sum(p.price_somoni for p in products if p.id in selected_ids)
        rows.append(
            [_button(text=f"🛍 Идома ({len(selected_ids)} — {format_price(total)} сомонӣ)", callback_data="cart:checkout")]
        )
    rows.append(
        [_button(text="🔙 Якто-якто харидан", callback_data=f"cartmode:exit:{category.value}")]
    )
    rows.append([_button(text="🔙 Ба меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reuse_recipient_keyboard(recipient: str, label_suffix: str = "") -> InlineKeyboardMarkup:
    # Accept either one legacy ID or a list of (player_id, in-game name).
    entries = recipient if isinstance(recipient, list) else [(recipient, None)]
    rows = []
    for player_id, player_name in entries[:3]:
        name = f" · {player_name}" if player_name else ""
        rows.append([_button(
            text=f"✅ {player_id}{name}"[:64],
            callback_data=f"reuseid:{player_id}",
        )])
    rows.extend([
        [_button(text="⬅️ Бозгашт ба бастаҳо", callback_data="catalog:back")],
        [_button(text="🏠 Менюи асосӣ", callback_data="menu:main")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_link_keyboard(pay_url: str | None = None) -> InlineKeyboardMarkup:
    rows = []
    if pay_url:
        rows.append([_button(text="💳 Кушодани пардохт", url=pay_url)])
    rows.append([_button(text="🔄 Бонки дигарро интихоб кардан", callback_data="payment:change_bank")])
    rows.append([_button(text="🏠 Ба меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def change_payment_method_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [_button(text=label, callback_data=f"bankchange:{key}")]
        for key, label in PAYMENT_METHODS
    ]
    rows.append([_button(text="⬅️ Бозгашт ба пардохт", callback_data="payment:back")])
    rows.append([_button(text="🏠 Менюи асосӣ", callback_data="menu:main")])
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
    rows = [[_button(text=label, callback_data=f"paymethod:{key}")] for key, label in PAYMENT_METHODS]
    rows.append([_button(text="⬅️ Бозгашт / бекор кардани фармоиш", callback_data="order:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def review_prompt_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button(text="⏭ Гузарондан", callback_data=f"review:skip:{order_id}")]
        ]
    )


def receipt_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button(text="🧾 Чеки муваффақ", callback_data=f"receipt:{order_id}")]
        ]
    )


def free_fire_region_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button(text="🌐 СНГ · Тоҷикистон / Россия", callback_data="region:ff_cis")],
            [_button(text="🇮🇩 Индонезия · ID Server", callback_data="region:ff_indonesia")],
            [_button(text="⬅️ Бозгашт ба бозиҳо", callback_data="menu:games")],
            [_button(text="🏠 Менюи асосӣ", callback_data="menu:main")],
        ]
    )


def confirm_order_keyboard(offer_balance_payment: bool = False, offer_wallet_payment: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if offer_balance_payment:
        rows.append(
            [_button(text="💰 Пардохт аз баланси реферал", callback_data="order:pay_balance")]
        )
    if offer_wallet_payment:
        rows.append(
            [_button(text="👛 Пардохт аз ҳамён", callback_data="order:pay_wallet")]
        )
    rows.append([_button(text="✅ Тасдиқ (бо чек)", callback_data="order:confirm", style="success")])
    rows.append([_button(text="❌ Бекор кардани фармоиш", callback_data="order:cancel", style="danger")])
    rows.append([_button(text="⬅️ Бозгашт ба бастаҳо", callback_data="catalog:back")])
    rows.append([_button(text="🏠 Менюи асосӣ", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_order_keyboard(order: Order) -> InlineKeyboardMarkup:
    rows = []
    if order.status.value == "awaiting_payment":
        rows.append([
            _button(text="✅ Тасдиқ", callback_data=f"admin:paid:{order.id}"),
            _button(text="❌ Рад", callback_data=f"admin:reject:{order.id}", style="danger"),
        ])
    elif order.status.value in ("paid", "delivering"):
        rows.append(
            [
                _button(
                    text="📦 Дода шуд (Delivered)", callback_data=f"admin:delivered:{order.id}"
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)
