"""Shared display formatting helpers — kept dependency-free so both
bot/handlers/admin.py, bot/handlers/customer.py, and bot/keyboards.py can
import it without any risk of circular imports.
"""

from __future__ import annotations


def format_price(amount: float) -> str:
    """Product prices are meant to always be whole soms now (see the
    /roundprices admin command and the rounding in pricing.py /
    repo.set_product_price) — showing "9.00" instead of "9" for something
    that's always a round number just adds visual noise. Falls back to two
    decimals for anything that genuinely isn't whole, so a legacy row that
    hasn't been rounded yet still displays correctly instead of silently
    truncating real cents."""
    if amount == round(amount):
        return f"{amount:.0f}"
    return f"{amount:.2f}"
