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
    """Return (price_somoni, cost_somoni) for `diamonds`, interpolated from breakpoints."""
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
