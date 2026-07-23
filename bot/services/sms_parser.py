"""Parse Dushanbe City Bank "Zachislenie" (deposit) SMS notifications.

Real example (admin-provided):

    Zachislenie
    Summa 30.00 TJS
    Komis 0.00 TJS
    Zachislenie 30.00 TJS
    Data 22:37 14.06.26
    Otpravitel 9920361***5353
    Kod 17259099237
    Karta 9762000126345841
    Balans 30.90 TJS

Only deposit notifications matter here — anything without "Zachislenie" and
a parseable "Summa ... TJS" is ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SUMMA_RE = re.compile(r"Summa\s+([\d]+(?:[.,]\d+)?)\s*TJS", re.IGNORECASE)
_KOD_RE = re.compile(r"Kod\s+(\S+)", re.IGNORECASE)


@dataclass
class ParsedDeposit:
    amount_somoni: float
    kod: str


def parse_deposit_sms(text: str) -> ParsedDeposit | None:
    if "zachislenie" not in text.lower():
        return None

    summa_match = _SUMMA_RE.search(text)
    if not summa_match:
        return None

    kod_match = _KOD_RE.search(text)
    if not kod_match:
        return None

    amount = float(summa_match.group(1).replace(",", "."))
    return ParsedDeposit(amount_somoni=amount, kod=kod_match.group(1))
