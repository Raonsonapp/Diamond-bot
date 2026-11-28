"""Default catalog and one-time-compatible ALMAZ TJ price migration."""
from sqlalchemy import select

from bot.db.models import Product, ProductCategory

# (display name, final diamond count, price, legacy counts to fold into this pack)
DEFAULT_PRODUCTS = [
    ("110 алмаз", 110, 8.90, {100, 110}),
    ("341 алмаз", 341, 25.95, {310, 341}),
    ("572 алмаз", 572, 44.90, {520, 572}),
    ("1166 алмаз", 1166, 85.50, {1060, 1166}),
    ("2398 алмаз", 2398, 163.16, {2180, 2398}),
    ("6160 алмаз", 6160, 405.90, {5600, 6160}),
    ("Ваучер Lite 90 алмаз", 90, 4.90, {90}),
    ("Ваучери ҳафтагӣ 450 алмаз", 450, 15.90, {450}),
    ("Ваучери моҳона 2600 алмаз", 2600, 60.00, {2600}),
]


# Fixed customer-facing prices for Free Fire Level Up packages (Tajik somoni).
# These are bundle prices, not per-diamond prices.
PUBG_PRODUCTS = [
    ("60 UC", 60, 9.71),
    ("325 UC", 325, 45.45),
    ("660 UC", 660, 90.64),
    ("1800 UC", 1800, 223.55),
    ("3850 UC", 3850, 424.23),
    ("8100 UC", 8100, 872.20),
    ("First Purchase Pack", 0, 10.90),
    ("Prime (1 Month)", 0, 10.90),
    ("Weekly Deal Pack 1", 0, 10.90),
    ("60 WOW Coins", 60, 11.40),
    ("Upgradable Firearm Materials Pack", 0, 30.21),
    ("Prime (3 Months)", 0, 30.21),
    ("Weekly Mythic Emblem Value Pack", 0, 30.21),
    ("Weekly Deal Pack 2", 0, 30.21),
    ("Mythic Emblem Pack", 0, 49.02),
    ("325 WOW Coins", 325, 51.99),
    ("Prime (6 Months)", 0, 58.92),
    ("Elite Pass LV1-50", 0, 58.92),
    ("Prime Plus (1 Month)", 0, 97.03),
    ("660 WOW Coins", 660, 102.97),
    ("Prime (12 Months)", 0, 116.34),
    ("Elite Pass LV1-100", 0, 118.81),
    ("1800 WOW Coins", 1800, 256.42),
    ("Elite Pass Plus LV1-100", 0, 289.09),
    ("Prime Plus (3 Months)", 0, 289.59),
    ("3850 WOW Coins", 3850, 511.35),
    ("Prime Plus (6 Months)", 0, 577.68),
    ("8100 WOW Coins", 8100, 1021.69),
    ("Prime Plus (12 Months)", 0, 1153.86),
]


LEVEL_UP_PRODUCTS = [
    ("Level Up Package 6", 6, 5.95),
    ("Level Up Package 10", 10, 8.92),
    ("Level Up Package 15", 15, 8.92),
    ("Level Up Package 20", 20, 8.92),
    ("Level Up Package 25", 25, 8.92),
    ("Level Up Package 30", 30, 10.90),
]


async def seed_default_products(session) -> None:
    """Migrate old bonus+base packs into one final pack and deactivate duplicates.

    Prefer legacy base packs (100/310/520/1060/2180/5600) so existing
    supplier mapping stays on the surviving product. Never overwrite unrelated
    categories or voucher products.
    """
    result = await session.execute(select(Product))
    products = list(result.scalars().all())

    changed = False
    for name, units, price, legacy_units in DEFAULT_PRODUCTS:
        is_voucher = "ваучер" in name.lower()
        candidates = [
            p for p in products
            if p.category == ProductCategory.DIAMONDS
            and p.diamonds in legacy_units
            and (("ваучер" in p.name.lower()) == is_voucher)
        ]
        if not candidates:
            product = Product(
                name=name, category=ProductCategory.DIAMONDS, diamonds=units,
                bonus_diamonds=0, price_somoni=price, cost_somoni=price,
            )
            session.add(product)
            products.append(product)
            changed = True
            continue

        # Prefer the original base pack, which is most likely to carry supplier mapping.
        legacy_base = {110: 100, 341: 310, 572: 520, 1166: 1060, 2398: 2180, 6160: 5600}
        preferred_units = legacy_base.get(units, units)
        candidates.sort(key=lambda p: (p.diamonds != preferred_units, p.id))
        survivor = candidates[0]
        for attr, value in (
            ("name", name), ("diamonds", units), ("bonus_diamonds", 0),
            ("price_somoni", price), ("is_active", True),
        ):
            if getattr(survivor, attr) != value:
                setattr(survivor, attr, value)
                changed = True
        # Deactivate duplicates so users only see one pack; retain their rows for old orders.
        for duplicate in candidates[1:]:
            if duplicate.is_active:
                duplicate.is_active = False
                changed = True

    # Add/update Level Up SKUs in the СНГ Free Fire catalog. Preserve any
    # existing FazerCards category/offer mapping and supplier cost.
    for name, units, price in LEVEL_UP_PRODUCTS:
        matches = [
            p for p in products
            if p.category == ProductCategory.DIAMONDS
            and p.name.strip().casefold() == name.casefold()
        ]
        if not matches:
            session.add(Product(
                name=name,
                category=ProductCategory.DIAMONDS,
                diamonds=units,
                bonus_diamonds=0,
                price_somoni=price,
                cost_somoni=0.0,
                is_active=True,
            ))
            changed = True
            continue

        matches.sort(key=lambda p: p.id)
        product = matches[0]
        for attr, value in (
            ("diamonds", units),
            ("price_somoni", price),
            ("is_active", True),
        ):
            if getattr(product, attr) != value:
                setattr(product, attr, value)
                changed = True
        for duplicate in matches[1:]:
            if duplicate.is_active:
                duplicate.is_active = False
                changed = True

    # Add/update PUBG Mobile catalog; all prices include a 1.00 somoni markup.
    for name, units, price in PUBG_PRODUCTS:
        matches = [
            p for p in products
            if p.category == ProductCategory.PUBG_MOBILE
            and p.name.strip().casefold() == name.casefold()
        ]
        if not matches:
            session.add(Product(
                name=name, category=ProductCategory.PUBG_MOBILE,
                diamonds=units, bonus_diamonds=0, price_somoni=price,
                cost_somoni=0.0, is_active=True,
            ))
            changed = True
            continue
        matches.sort(key=lambda p: p.id)
        product = matches[0]
        for attr, value in (("diamonds", units), ("price_somoni", price), ("is_active", True)):
            if getattr(product, attr) != value:
                setattr(product, attr, value)
                changed = True
        for duplicate in matches[1:]:
            if duplicate.is_active:
                duplicate.is_active = False
                changed = True

    if changed or session.new:
        await session.commit()
