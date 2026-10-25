"""Exact money handling.

* Monetary amounts are stored as integers in the currency's minor unit
  (halalas, fils, cents), so sums in SQL are exact on every database.
* Quantities, unit prices and rates are ``Decimal`` and stored exactly
  (NUMERIC on PostgreSQL, text on SQLite, never floating point).
* Rounding is ROUND_HALF_UP to the currency's minor unit.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import Numeric, String
from sqlalchemy.types import TypeDecorator

# ISO 4217 minor-unit digits for the currencies the platform accepts.
CURRENCY_DIGITS: dict[str, int] = {
    "SAR": 2, "AED": 2, "QAR": 2, "EGP": 2, "MAD": 2, "USD": 2, "EUR": 2, "GBP": 2, "TRY": 2,
    "KWD": 3, "BHD": 3, "OMR": 3, "JOD": 3, "IQD": 3, "LYD": 3, "TND": 3,
    "JPY": 0,
}


class UnsupportedCurrency(ValueError):
    pass


def digits(currency: str) -> int:
    try:
        return CURRENCY_DIGITS[currency]
    except KeyError:
        raise UnsupportedCurrency(f"unsupported currency: {currency}") from None


def quantum(currency: str) -> Decimal:
    return Decimal(1).scaleb(-digits(currency))


def round_money(amount: Decimal, currency: str) -> Decimal:
    return amount.quantize(quantum(currency), rounding=ROUND_HALF_UP)


def to_minor(amount: Decimal, currency: str) -> int:
    """Round to the currency's minor unit and return it as an integer (e.g. 10.005 SAR -> 1001)."""
    return int(round_money(amount, currency).scaleb(digits(currency)))


def from_minor(minor: int, currency: str) -> Decimal:
    return (Decimal(minor).scaleb(-digits(currency))).quantize(quantum(currency))


def convert(minor: int, currency: str, rate: Decimal, to_currency: str) -> int:
    """An amount in another currency at rate (units of to_currency per unit of currency), rounded once."""
    if currency == to_currency and rate == 1:
        return minor
    return to_minor(from_minor(minor, currency) * rate, to_currency)


def parse_decimal(value: str | int | Decimal) -> Decimal:
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"not a number: {value!r}") from None
    if not d.is_finite():
        raise ValueError("number must be finite")
    return d


class ExactDecimal(TypeDecorator):
    """Decimal column that never round-trips through float.

    On SQLite the value is stored as text, so CHECK constraints must compare
    ``CAST(col AS NUMERIC)`` (which is also valid on PostgreSQL).
    """

    impl = Numeric
    cache_ok = True

    def __init__(self, precision: int = 18, scale: int = 6):
        super().__init__()
        self.precision = precision
        self.scale = scale

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(self.precision + 2))
        return dialect.type_descriptor(Numeric(self.precision, self.scale, asdecimal=True))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        value = parse_decimal(value).quantize(Decimal(1).scaleb(-self.scale), rounding=ROUND_HALF_UP)
        return str(value) if dialect.name == "sqlite" else value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value if isinstance(value, Decimal) else Decimal(str(value))


def fmt_decimal(value: Decimal) -> str:
    """Plain string without trailing zeros or exponent: Decimal('10.500000') -> '10.5'."""
    text = format(value.normalize(), "f")
    return "0" if text in ("-0", "") else text
