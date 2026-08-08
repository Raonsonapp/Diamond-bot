"""Generates a branded "чек" (receipt) card image for a completed order —
visually inspired by a rival bot's polished post-delivery card (glowing
checkmark, tiled watermark, bordered card), so a customer has something
worth forwarding as proof of purchase (and free word-of-mouth advertising
for ALMAZ TJ BOT).

Needs a Cyrillic-capable TTF font on disk — the Dockerfile installs
`fonts-dejavu-core` so /usr/share/fonts/truetype/dejavu/DejaVuSans*.ttf is
always present in production. Falls back to Pillow's built-in bitmap font
(no Cyrillic glyphs — renders as boxes) only if that's somehow missing, so
a missing font package degrades the image instead of crashing the bot.
"""

from __future__ import annotations

import io
import re

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# DejaVu Sans has no color-emoji glyphs — an emoji character (product unit
# labels like 💎/💠/⭐, or anything an admin typed into a product name)
# would render as a hollow "tofu" box. Strip pictographic ranges before
# drawing any dynamic text; the explicit "✓" checkmark is drawn separately
# via its own literal string, so it's untouched by this.
_EMOJI_RE = re.compile("[\U0001F000-\U0001FFFF☀-➿⬀-⯿️]")


def _clean(text: str) -> str:
    return _EMOJI_RE.sub("", text).strip()


_WIDTH = 720
_BG_TOP = (16, 24, 51)
_BG_BOTTOM = (10, 15, 34)
# Green accent per the admin's explicit request (was blue) — used for the
# checkmark circle/glow, the big product-quantity line, the card border,
# and the bot-name footer.
_ACCENT = (52, 199, 89)
_WHITE = (240, 244, 255)
_MUTED = (150, 162, 196)
_BADGE_BG = (34, 150, 74)
_LINE = (52, 62, 94)

_FONT_DIR = "/usr/share/fonts/truetype/dejavu"


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(f"{_FONT_DIR}/{name}", size)
    except OSError:
        return ImageFont.load_default()


def _vertical_gradient(width: int, height: int, top: tuple, bottom: tuple) -> Image.Image:
    base = Image.new("RGB", (width, height), top)
    draw = ImageDraw.Draw(base)
    for y in range(height):
        t = y / max(height - 1, 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        draw.line([(0, y), (width, y)], fill=color)
    return base


def _rounded_panel(draw: ImageDraw.ImageDraw, box, radius, fill) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def _watermark_layer(width: int, height: int, text: str, font: ImageFont.FreeTypeFont) -> Image.Image:
    """A faint, diagonally-tiled repeat of `text` across the whole card —
    the same "can't crop out the branding" watermark trick the reference
    receipt uses, built by stamping+rotating one tile and repeating it in
    a brick pattern rather than trying to rotate individual text draws
    (Pillow can't rotate ImageDraw.text directly)."""
    stamp = Image.new("RGBA", (420, 130), (0, 0, 0, 0))
    ImageDraw.Draw(stamp).text((10, 35), text, font=font, fill=(255, 255, 255, 22))
    stamp = stamp.rotate(-28, expand=True, resample=Image.BICUBIC)
    sw, sh = stamp.size

    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    step_x, step_y = sw - 60, sh - 20
    row = 0
    y = -sh
    while y < height + sh:
        x_offset = (row % 2) * (step_x // 2)
        x = -sw + x_offset
        while x < width + sw:
            layer.alpha_composite(stamp, (x, y))
            x += step_x
        y += step_y
        row += 1
    return layer


def _glow_layer(width: int, height: int, cx: int, cy: int, radius: int, color: tuple) -> Image.Image:
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse(
        (cx - radius, cy - radius, cx + radius, cy + radius), fill=(*color, 130)
    )
    return layer.filter(ImageFilter.GaussianBlur(radius / 2))


def generate_receipt_image(
    *,
    order_id: int,
    title: str,
    items: list[str],
    recipient_id: str,
    payment_method: str,
    when_text: str,
    amount_somoni: float,
    bot_name: str = "ALMAZ TJ BOT",
) -> bytes:
    """Returns PNG bytes for a receipt card. `items` is one or more
    "product name" lines (multiple for a cart order)."""
    row_h = 46
    # Generous upper bound — the image is cropped to the real content
    # height at the end, this just needs to never run out of canvas.
    height = 620 + len(items) * 50

    img = _vertical_gradient(_WIDTH, height, _BG_TOP, _BG_BOTTOM).convert("RGBA")

    title_font = _font("DejaVuSans-Bold.ttf", 30)
    big_font = _font("DejaVuSans-Bold.ttf", 40)
    label_font = _font("DejaVuSans.ttf", 24)
    value_font = _font("DejaVuSans-Bold.ttf", 24)
    small_font = _font("DejaVuSans.ttf", 20)
    badge_font = _font("DejaVuSans-Bold.ttf", 22)
    watermark_font = _font("DejaVuSans-Bold.ttf", 26)

    cx = _WIDTH // 2
    circle_r = 46
    circle_cy = 100

    # Watermark + glow are both composited onto the background before any
    # crisp foreground content is drawn on top, so text stays fully legible.
    img = Image.alpha_composite(img, _watermark_layer(_WIDTH, height, _clean(bot_name), watermark_font))
    img = Image.alpha_composite(img, _glow_layer(_WIDTH, height, cx, circle_cy, circle_r + 30, _ACCENT))

    draw = ImageDraw.Draw(img)

    draw.ellipse(
        (cx - circle_r, circle_cy - circle_r, cx + circle_r, circle_cy + circle_r),
        fill=_ACCENT,
    )
    check_font = _font("DejaVuSans-Bold.ttf", 46)
    _draw_centered(draw, "✓", cx, circle_cy - 4, check_font, _WHITE)

    y = circle_cy + circle_r + 24
    _draw_centered(draw, _clean(title), cx, y, title_font, _WHITE)
    y += 50

    for line in items:
        _draw_centered(draw, _clean(line), cx, y, big_font, _ACCENT)
        y += 46

    y += 10
    draw.line([(48, y), (_WIDTH - 48, y)], fill=_LINE, width=2)
    y += 30

    def field(label: str, value: str) -> None:
        nonlocal y
        draw.text((60, y), label, font=label_font, fill=_MUTED)
        clean_value = _clean(value)
        w = draw.textlength(clean_value, font=value_font)
        draw.text((_WIDTH - 60 - w, y), clean_value, font=value_font, fill=_WHITE)
        y += row_h

    field("Фармоиш №", f"#{order_id}")
    field("ID аккаунт", recipient_id)
    field("Усули пардохт", payment_method)
    field("Сана", when_text)
    field("Маблағ", f"{amount_somoni:.2f} сомонӣ")

    y += 8
    badge_text = "МУВАФФАҚ"
    badge_w = draw.textlength(badge_text, font=badge_font) + 40
    badge_h = 42
    badge_box = (cx - badge_w / 2, y, cx + badge_w / 2, y + badge_h)
    _rounded_panel(draw, badge_box, radius=21, fill=_BADGE_BG)
    draw.rounded_rectangle(badge_box, radius=21, outline=_ACCENT, width=2)
    _draw_centered(draw, badge_text, cx, y + badge_h / 2 - 14, badge_font, _WHITE)
    y += badge_h + 26

    draw.line([(48, y), (_WIDTH - 48, y)], fill=_LINE, width=2)
    y += 22
    _draw_centered(draw, _clean(bot_name), cx, y, value_font, _ACCENT)
    y += 32
    _draw_centered(draw, "Ташаккур барои интихоби шумо!", cx, y, small_font, _MUTED)

    final_h = min(y + 50, height)
    # Card border, drawn last so it frames the exact final (cropped) size.
    draw.rounded_rectangle((10, 10, _WIDTH - 10, final_h - 10), radius=26, outline=_ACCENT, width=3)

    buf = io.BytesIO()
    img.convert("RGB").crop((0, 0, _WIDTH, final_h)).save(buf, format="PNG")
    return buf.getvalue()


def _draw_centered(draw: ImageDraw.ImageDraw, text: str, cx: int, y: int, font, fill) -> None:
    w = draw.textlength(text, font=font)
    draw.text((cx - w / 2, y), text, font=font, fill=fill)
