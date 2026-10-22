"""Сумма прописью для счёта на оплату: «Две тысячи девятьсот восемьдесят рублей».

Склонение и род числительных считаются по правилам русского языка:
«одна тысяча» (женский род у слова «тысяча»), «один рубль» (мужской род),
«11 рублей» (числа 11–19 всегда стоят в родительном множественном).
Копейки выводятся цифрами, как в образце счёта: «рублей 50 копеек».
"""

from decimal import ROUND_HALF_UP, Decimal

_ONES_MASCULINE = ("", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять")
_ONES_FEMININE = ("", "одна", "две", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять")
_TEENS = (
    "десять",
    "одиннадцать",
    "двенадцать",
    "тринадцать",
    "четырнадцать",
    "пятнадцать",
    "шестнадцать",
    "семнадцать",
    "восемнадцать",
    "девятнадцать",
)
_TENS = ("", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят", "девяносто")
_HUNDREDS = ("", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот", "восемьсот", "девятьсот")

_RUBLE_FORMS = ("рубль", "рубля", "рублей")
_KOPECK_FORMS = ("копейка", "копейки", "копеек")

# (величина разряда, формы слова, женский ли род у слова разряда)
_SCALES = (
    (1_000_000_000, ("миллиард", "миллиарда", "миллиардов"), False),
    (1_000_000, ("миллион", "миллиона", "миллионов"), False),
    (1_000, ("тысяча", "тысячи", "тысяч"), True),
)


def _plural_form(count: int, forms: tuple[str, str, str]) -> str:
    """Выбрать форму слова по числу: 1 рубль, 2 рубля, 5 рублей, 21 рубль, 11 рублей."""
    last_two = count % 100
    if 11 <= last_two <= 19:
        return forms[2]
    last = count % 10
    if last == 1:
        return forms[0]
    if 2 <= last <= 4:
        return forms[1]
    return forms[2]


def _triad_words(number: int, feminine: bool) -> list[str]:
    """Слова для числа от 1 до 999 (сотни, десятки, единицы)."""
    words: list[str] = []
    hundreds, rest = divmod(number, 100)
    if hundreds:
        words.append(_HUNDREDS[hundreds])
    if 10 <= rest <= 19:
        words.append(_TEENS[rest - 10])
    else:
        tens, ones = divmod(rest, 10)
        if tens:
            words.append(_TENS[tens])
        if ones:
            words.append((_ONES_FEMININE if feminine else _ONES_MASCULINE)[ones])
    return words


def _integer_words(number: int, feminine: bool) -> list[str]:
    """Слова для целого числа больше нуля. feminine относится к последней группе (единицам)."""
    words: list[str] = []
    rest = number
    for value, forms, scale_feminine in _SCALES:
        count, rest = divmod(rest, value)
        if count:
            words.extend(_integer_words(count, scale_feminine))
            words.append(_plural_form(count, forms))
    if rest:
        words.extend(_triad_words(rest, feminine))
    return words


def amount_in_words_rub(amount: Decimal) -> str:
    """Сумма прописью с заглавной буквы: «Одна тысяча четыреста девяносто рублей»."""
    if amount < 0:
        raise ValueError("Сумма счёта не может быть отрицательной")
    total = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    rubles = int(total)
    kopecks = int((total - rubles) * 100)
    rubles_words = " ".join(_integer_words(rubles, feminine=False)) if rubles else "ноль"
    text = f"{rubles_words} {_plural_form(rubles, _RUBLE_FORMS)}"
    if kopecks:
        text = f"{text} {kopecks} {_plural_form(kopecks, _KOPECK_FORMS)}"
    return text[0].upper() + text[1:]
