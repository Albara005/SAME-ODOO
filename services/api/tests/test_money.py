from decimal import Decimal as D

import pytest

from novara.money import UnsupportedCurrency, fmt_decimal, from_minor, to_minor


def test_rounds_half_up_to_currency_minor_unit():
    assert to_minor(D("10.005"), "SAR") == 1001
    assert to_minor(D("10.004"), "SAR") == 1000
    assert to_minor(D("1.2345"), "KWD") == 1235  # Kuwaiti dinar has 3 decimals
    assert to_minor(D("2.5"), "JPY") == 3


def test_minor_round_trip():
    assert from_minor(1001, "SAR") == D("10.01")
    assert str(from_minor(5, "KWD")) == "0.005"
    assert str(from_minor(0, "SAR")) == "0.00"


def test_unknown_currency_is_rejected():
    with pytest.raises(UnsupportedCurrency):
        to_minor(D(1), "XXX")


def test_fmt_decimal_has_no_exponent():
    assert fmt_decimal(D("10.000000")) == "10"
    assert fmt_decimal(D("0.500")) == "0.5"
    assert fmt_decimal(D("0E-6")) == "0"
