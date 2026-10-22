"""Tlumaczenie kodow targetowania na nazwy z panelu Amazona.

Ads API zwraca kody techniczne (QUERY_HIGH_REL_MATCHES, close-match,
TARGETING_EXPRESSION_PREDEFINED), a konsola reklamowa pokazuje nazwy opisowe.
Bez tego mapowania panel wyglada inaczej niz Amazon i nie da sie ich zestawic.

Zrodlo prawdy o rodzaju targetu to `match_type` z raportu:
  * TARGETING_EXPRESSION_PREDEFINED - targetowanie automatyczne
  * TARGETING_EXPRESSION            - targetowanie produktowe (reczne)
  * EXACT / PHRASE / BROAD          - slowo kluczowe
"""
from __future__ import annotations

import json
import re
from typing import Any

# Targetowanie automatyczne - cztery grupy, ktore Amazon pokazuje w kampanii AUTO
AUTO_LABELS = {
    "QUERY_HIGH_REL_MATCHES": "Ściśle powiązane",
    "QUERY_BROAD_REL_MATCHES": "Luźno powiązane",
    "ASIN_SUBSTITUTE_RELATED": "Zamienniki",
    "ASIN_ACCESSORY_RELATED": "Uzupełniające",
    # te same grupy bywaja zwracane pod nazwami z raportu
    "close-match": "Ściśle powiązane",
    "loose-match": "Luźno powiązane",
    "substitutes": "Zamienniki",
    "complements": "Uzupełniające",
}

# Targetowanie produktowe - prefiks opisujacy rodzaj warunku
EXPRESSION_LABELS = {
    "ASIN_SAME_AS": "Produkt",
    "ASIN_EXPANDED_FROM": "Produkt (rozszerzony)",
    "ASIN_CATEGORY_SAME_AS": "Kategoria",
    "ASIN_BRAND_SAME_AS": "Marka",
    "ASIN_PRICE_LESS_THAN": "Cena poniżej",
    "ASIN_PRICE_GREATER_THAN": "Cena powyżej",
    "ASIN_PRICE_BETWEEN": "Cena w przedziale",
    "ASIN_REVIEW_RATING_LESS_THAN": "Ocena poniżej",
    "ASIN_REVIEW_RATING_GREATER_THAN": "Ocena powyżej",
    "ASIN_REVIEW_RATING_BETWEEN": "Ocena w przedziale",
    "ASIN_IS_PRIME_SHIPPING_ELIGIBLE": "Dostawa Prime",
    "ASIN_AGE_RANGE_SAME_AS": "Przedział wiekowy",
    "ASIN_GENRE_SAME_AS": "Gatunek",
    "KEYWORD_GROUP_SAME_AS": "Grupa fraz",
}

MATCH_LABELS = {
    "EXACT": "ścisłe",
    "PHRASE": "wyrażenie",
    "BROAD": "przybliżone",
    "TARGETING_EXPRESSION": "produktowe",
    "TARGETING_EXPRESSION_PREDEFINED": "automatyczne",
}

AUTO_MATCH = "TARGETING_EXPRESSION_PREDEFINED"
PRODUCT_MATCH = "TARGETING_EXPRESSION"


def target_kind(match_type: str | None) -> str:
    """KEYWORD dla slow kluczowych, TARGET dla targetowania produktowego i auto.

    Wczesniej rodzaj wnioskowalismy z obecnosci pola `keyword` w raporcie - ale
    Amazon wypelnia je takze dla auto-targetow (wartoscia "close-match"), przez
    co wszystko ladowalo jako slowo kluczowe.
    """
    mt = (match_type or "").upper()
    if mt in (AUTO_MATCH, PRODUCT_MATCH):
        return "TARGET"
    return "KEYWORD"


def match_label(match_type: str | None) -> str | None:
    if not match_type:
        return None
    return MATCH_LABELS.get(match_type.upper(), match_type)


# Ktore warunki niosa identyfikator do rozwiniecia w nazwe
LOOKUP_KIND = {
    "ASIN_CATEGORY_SAME_AS": "CATEGORY",
    "ASIN_BRAND_SAME_AS": "BRAND",
}


def _describe_condition(
    cond: dict[str, Any], labels: dict[tuple[str, str], str] | None = None
) -> str:
    kind = (cond.get("type") or "").upper()
    value = cond.get("value")

    # Grupy targetowania automatycznego nie maja wartosci - sam typ jest nazwa.
    # Bez tego przy zatwierdzaniu widac bylo QUERY_HIGH_REL_MATCHES zamiast
    # "Ściśle powiązane".
    if kind in AUTO_LABELS:
        return AUTO_LABELS[kind]

    label = EXPRESSION_LABELS.get(kind)

    # Identyfikator zamieniamy na nazwe, gdy slownik ja zna. Gdy nie -
    # zostaje identyfikator, bo lepszy niz nic i pozwala go odszukac recznie.
    lookup = LOOKUP_KIND.get(kind)
    if lookup and labels and value is not None:
        name = labels.get((lookup, str(value)))
        if name:
            value = name

    if label is None:
        return f"{kind} {value}".strip() if value else kind

    if value in (None, ""):
        return label
    if isinstance(value, dict):
        # przedzialy przychodza jako {"min": x, "max": y}
        lo, hi = value.get("min"), value.get("max")
        if lo is not None and hi is not None:
            return f"{label} {lo}–{hi}"
        return f"{label} {lo if lo is not None else hi}"
    return f"{label} {value}"


def describe_expression(
    expression: Any, labels: dict[tuple[str, str], str] | None = None
) -> str | None:
    """Zamienia wyrazenie targetowania na czytelny opis."""
    if expression is None:
        return None
    if isinstance(expression, str):
        try:
            expression = json.loads(expression)
        except (ValueError, TypeError):
            return expression

    if isinstance(expression, dict):
        expression = [expression]
    if not isinstance(expression, list) or not expression:
        return None

    parts = [_describe_condition(c, labels) for c in expression if isinstance(c, dict)]
    return " + ".join(p for p in parts if p) or None


def resolve_raw_targeting(
    text: str | None, labels: dict[tuple[str, str], str] | None
) -> str | None:
    """Podmienia identyfikatory w gotowym opisie z raportu na nazwy.

    Raport potrafi zwrocic string w rodzaju
    `category="2076158031" brand="16404400031" rating=4-5`.
    Rozwijamy w nim to, co znamy, zachowujac reszte bez zmian.
    """
    if not text or not labels:
        return text

    def repl(match: re.Match[str]) -> str:
        field, value = match.group(1), match.group(2)
        kind = {"category": "CATEGORY", "brand": "BRAND"}.get(field.lower())
        if not kind:
            return match.group(0)
        name = labels.get((kind, value))
        return f'{field}="{name}"' if name else match.group(0)

    return re.sub(r'(category|brand)="(\d+)"', repl, text, flags=re.IGNORECASE)


def display_name(
    match_type: str | None,
    raw_text: str | None,
    expression: Any = None,
    labels: dict[tuple[str, str], str] | None = None,
) -> str | None:
    """Nazwa targetu taka, jak w panelu Amazona.

    Kolejnosc zrodel: dla auto-targetow slownik grup, dla produktowych opis
    wyrazenia, dla slow kluczowych sam tekst frazy.
    """
    mt = (match_type or "").upper()

    if mt == AUTO_MATCH:
        key = (raw_text or "").strip()
        return AUTO_LABELS.get(key) or AUTO_LABELS.get(key.upper()) or key or "Automatyczne"

    if mt == PRODUCT_MATCH:
        described = describe_expression(expression, labels)
        if described:
            return described
        # raport potrafi podac gotowy opis w polu targeting
        return resolve_raw_targeting(raw_text, labels) or "Targetowanie produktowe"

    return raw_text
