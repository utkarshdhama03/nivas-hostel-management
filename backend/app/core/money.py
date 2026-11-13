"""Money in the API (docs/04 §1, 05 FIN-10): decimal STRINGS with two places, never JSON numbers.

JavaScript numbers are binary floats (0.1 + 0.2 = 0.30000000000000004), so a JSON number in a
money field is rejected with 422, and every amount leaves the API as "1250.00". Inside, money
is Decimal and numeric(12,2) in the database.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BeforeValidator, PlainSerializer
from pydantic_core import PydanticCustomError

CENT = Decimal("0.01")
MAX = Decimal("9999999999.99")  # numeric(12,2)
_MONEY_RE = re.compile(r"^[0-9]{1,10}(\.[0-9]{1,2})?$")


def parse_money(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value.quantize(CENT)
    if not isinstance(value, str) or not _MONEY_RE.match(value.strip()):
        raise PydanticCustomError("decimal_string_required", 'Send money as a string like "1250.00"')
    return Decimal(value.strip()).quantize(CENT)


def _positive(value: Any) -> Decimal:
    amount = parse_money(value)
    if amount <= 0:
        raise PydanticCustomError("amount_not_positive", "The amount must be more than zero")
    return amount


def format_money(value: Decimal) -> str:
    return f"{value.quantize(CENT)}"


MoneyIn = Annotated[Decimal, BeforeValidator(_positive), PlainSerializer(format_money, return_type=str)]
MoneyInOrZero = Annotated[Decimal, BeforeValidator(parse_money), PlainSerializer(format_money, return_type=str)]
MoneyOut = Annotated[Decimal, PlainSerializer(format_money, return_type=str)]
