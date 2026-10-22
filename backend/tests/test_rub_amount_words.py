from decimal import Decimal

import pytest

from app.services.rub_amount_words import amount_in_words_rub


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (Decimal("1490"), "Одна тысяча четыреста девяносто рублей"),
        (Decimal("2980"), "Две тысячи девятьсот восемьдесят рублей"),
        (Decimal("1001"), "Одна тысяча один рубль"),
        (Decimal("21"), "Двадцать один рубль"),
        (Decimal("11"), "Одиннадцать рублей"),
        (Decimal("0.50"), "Ноль рублей 50 копеек"),
        (Decimal("0"), "Ноль рублей"),
        (Decimal("1"), "Один рубль"),
        (Decimal("2"), "Два рубля"),
        (Decimal("112"), "Сто двенадцать рублей"),
        (Decimal("1014"), "Одна тысяча четырнадцать рублей"),
        (Decimal("22000"), "Двадцать две тысячи рублей"),
        (Decimal("1000000"), "Один миллион рублей"),
        (Decimal("2000000"), "Два миллиона рублей"),
        (Decimal("5000000"), "Пять миллионов рублей"),
        (Decimal("2000000000"), "Два миллиарда рублей"),
        (Decimal("1490.01"), "Одна тысяча четыреста девяносто рублей 1 копейка"),
        (Decimal("10.05"), "Десять рублей 5 копеек"),
    ],
)
def test_amount_in_words_rub(amount: Decimal, expected: str) -> None:
    assert amount_in_words_rub(amount) == expected


def test_amount_in_words_rejects_negative() -> None:
    with pytest.raises(ValueError):
        amount_in_words_rub(Decimal("-1"))
