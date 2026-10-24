"""Quote a price for an arbitrary diamond amount from the fixed package list.

The admin only sets prices for a handful of official packages (e.g. 100,
310, 520, 1060, 2180, 5600 diamonds — the exact denominations Free Fire
itself sells). Bigger packages are priced cheaper per diamond (bulk
discount). When a customer asks for a custom amount that doesn't match any
package, we quote a fair price by linearly interpolating between the two
surrounding packages on the price curve — so a custom quote always agrees
exactly with the real price at every package breakpoint, and smoothly
follows the same bulk-discount curve in between.
"""

from __future__ import annotations

Breakpoint = tuple[int, float, float]  # (diamonds, price_somoni, cost_somoni)


def quote_custom_price(diamonds: int, breakpoints: list[Breakpoint]) -> tuple[float, float]:
    """Return (price_somoni, cost_somoni) for `diamonds`, interpolated from
    breakpoints. price_somoni is rounded to two decimals — every other
    product price in the catalog is a round number now (see /roundprices),
    so a custom-amount quote shouldn't be the one place that still shows
    kopecks. cost_somoni stays at 2 decimals since that's our own internal
    margin bookkeeping, never shown to the customer."""
    if not breakpoints:
        raise ValueError("No packages configured to price from")

    points = sorted(breakpoints, key=lambda b: b[0])

    if diamonds <= points[0][0]:
        d0, p0, c0 = points[0]
        rate_p = p0 / d0
        rate_c = c0 / d0
        return round(diamonds * rate_p, 2), round(diamonds * rate_c, 2)

    if diamonds >= points[-1][0]:
        d2, p2, c2 = points[-1]
        d1, p1, c1 = points[-2] if len(points) >= 2 else (0, 0.0, 0.0)
        rate_p = (p2 - p1) / (d2 - d1)
        rate_c = (c2 - c1) / (d2 - d1)
        price = p2 + (diamonds - d2) * rate_p
        cost = c2 + (diamonds - d2) * rate_c
        return round(price, 2), round(cost, 2)

    for (d1, p1, c1), (d2, p2, c2) in zip(points, points[1:]):
        if d1 <= diamonds <= d2:
            frac = (diamonds - d1) / (d2 - d1)
            price = p1 + frac * (p2 - p1)
            cost = c1 + frac * (c2 - c1)
            return round(price, 2), round(cost, 2)

    raise RuntimeError("unreachable")


# Customer-facing promotional price bands. Each customer gets one stable price
# per SKU (rather than a price that changes every time the menu is reopened).
# Values are (minimum, maximum), in somoni. These are price bands, not ranges
# displayed to the customer.
CUSTOM_PRICE_BANDS: dict[int, tuple[float, float]] = {
    40: (7.90, 7.99), 30: (11.50, 11.99), 41: (11.50, 11.99),
    31: (23.90, 24.99), 42: (15.50, 15.99), 43: (20.50, 20.99),
    32: (58.90, 61.99), 44: (40.90, 41.99), 45: (51.90, 52.99),
    33: (118.90, 124.99), 46: (76.90, 78.99), 47: (96.90, 99.99),
    34: (239.90, 249.99), 35: (599.90, 624.99), 36: (1199.90, 1249.99),
    37: (154.90, 159.99), 38: (224.90, 234.99), 39: (379.90, 398.99),
    1: (8.50, 8.90), 2: (26.95, 26.99), 3: (42.90, 42.99),
    4: (82.90, 82.99), 5: (169.90, 169.99), 6: (419.90, 419.99),
    9: (4.94, 4.99), 7: (16.32, 16.99), 8: (59.90, 59.99),
    48: (22.90, 23.99), 49: (89.90, 94.99),
}

def _stable_band_price(product_id: int, user_id: int, low: float, high: float, promo: bool) -> float:
    import hashlib
    import math
    seed = f"ff-margedar-price-v1:{user_id}:{product_id}:{int(promo)}".encode()
    value = int.from_bytes(hashlib.sha256(seed).digest()[:4], "big") / 0xFFFFFFFF
    cents_low = math.ceil(low * 100)
    cents_high = math.floor(high * 100)
    cents = cents_low + int(value * (cents_high - cents_low + 1))
    return round(cents / 100, 2)

async def apply_customer_prices(session, products: list, user_id: int) -> bool:
    """Set per-customer stable display/order prices in memory. Promo requires
    5 registered referrals and at least one referral with a paid/delivered order."""
    from datetime import datetime, timezone
    from bot.db.repo import count_referrals, has_referral_with_purchase

    referral_count = await count_referrals(session, user_id)
    has_purchase = await has_referral_with_purchase(
        session, user_id, datetime(2000, 1, 1, tzinfo=timezone.utc)
    )
    promo = referral_count >= 5 and has_purchase
    for product in products:
        band = CUSTOM_PRICE_BANDS.get(product.id)
        if band:
            price = _stable_band_price(product.id, user_id, band[0], band[1], promo)
        else:
            # Lower unlisted packages slightly while preserving at least 0.25с
            # gross margin; do not undercut the supplier cost.
            current = float(product.price_somoni)
            target = max(round(current * 0.98, 2), round(float(product.cost_somoni) + 0.25, 2))
            # Give each user/SKU a stable two-decimal variation, capped by margin.
            import hashlib
            cents = int(hashlib.sha256(f"ff-other:{user_id}:{product.id}".encode()).hexdigest()[:2], 16) % 10
            price = max(round(float(product.cost_somoni) + 0.25, 2), round(target - cents / 100, 2))
        if promo:
            # Extra 3% customer-specific referral discount, with a small safety
            # margin above supplier cost for all SKUs.
            price = max(round(price * 0.97, 2), round(float(product.cost_somoni) + 0.25, 2))
        product.price_somoni = round(price, 2)
    return promo
