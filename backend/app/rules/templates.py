"""Gotowe szablony regul.

To punkty wyjscia, nie sztywne przepisy - kazdy prog jest polem w formularzu
i mozna go zmienic. Szablony pokazuja typowe wzorce i podpowiadaja rozsadne
wartosci startowe.
"""
from __future__ import annotations

from typing import Any

TEMPLATES: list[dict[str, Any]] = [
    {
        "key": "kill_no_convert",
        "name": "Wyklucz frazy bez konwersji",
        "description": (
            "Fraza zebrała dużo kliknięć i ani jednego zamówienia — wykluczamy ją "
            "jako negatyw exact, żeby budżet poszedł do fraz, które sprzedają."
        ),
        "scope": "SEARCH_TERM",
        "lookback_days": 30,
        "conditions": {
            "all": [
                {"metric": "clicks", "op": ">=", "value": 50},
                {"metric": "orders", "op": "==", "value": 0},
            ]
        },
        "action": {"type": "ADD_NEGATIVE_KEYWORD", "match_type": "NEGATIVE_EXACT"},
        "limits": {"max_items": 100},
        "schedule_hours": 24,
    },
    {
        "key": "kill_expensive_no_convert",
        "name": "Wyklucz frazy przepalające budżet",
        "description": (
            "Wariant kosztowy zamiast klikowego: liczy się wydatek bez zwrotu, "
            "nie liczba kliknięć. Łapie drogie frazy szybciej niż próg kliknięć."
        ),
        "scope": "SEARCH_TERM",
        "lookback_days": 30,
        "conditions": {
            "all": [
                {"metric": "cost", "op": ">=", "value": 25},
                {"metric": "orders", "op": "==", "value": 0},
            ]
        },
        "action": {"type": "ADD_NEGATIVE_KEYWORD", "match_type": "NEGATIVE_EXACT"},
        "limits": {"max_items": 100},
        "schedule_hours": 24,
    },
    {
        "key": "block_asin",
        "name": "Wyklucz ASIN-y, które nie sprzedają",
        "description": (
            "Targetowanie produktowe trafia na cudzą kartę, zbiera kliknięcia i nic "
            "z tego nie ma. Wykluczamy konkretny ASIN z grupy reklam."
        ),
        "scope": "SEARCH_TERM",
        "lookback_days": 30,
        "conditions": {
            "all": [
                {"metric": "clicks", "op": ">=", "value": 20},
                {"metric": "orders", "op": "==", "value": 0},
            ]
        },
        "action": {"type": "ADD_NEGATIVE_ASIN"},
        "limits": {"max_items": 100},
        "schedule_hours": 24,
    },
    {
        "key": "bid_down_high_acos",
        "name": "Obniż stawkę przy wysokim ACOS",
        "description": (
            "Keyword sprzedaje, ale za drogo. Zamiast pauzować, schodzimy ze stawką "
            "i dajemy mu szansę zmieścić się w celu."
        ),
        "scope": "TARGET",
        "lookback_days": 30,
        "conditions": {
            "all": [
                {"metric": "clicks", "op": ">=", "value": 15},
                {"metric": "acos", "op": ">", "value": 45},
            ]
        },
        "action": {"type": "SET_BID_PCT", "percent": -15},
        "limits": {"max_items": 200, "min_bid": 0.05, "max_bid": 5.0},
        "schedule_hours": 24,
    },
    {
        "key": "bid_up_no_impressions",
        "name": "Podnieś stawkę przy braku wyświetleń",
        "description": (
            "Keyword praktycznie nie startuje w aukcji — stawka jest za niska, "
            "żeby w ogóle się pokazać. Podbijamy ją, żeby zebrać dane."
        ),
        "scope": "TARGET",
        "lookback_days": 14,
        "conditions": {
            "all": [
                {"metric": "impressions", "op": "<", "value": 100},
                {"metric": "orders", "op": "==", "value": 0},
            ]
        },
        "action": {"type": "SET_BID_PCT", "percent": 20},
        "limits": {"max_items": 200, "min_bid": 0.05, "max_bid": 3.0},
        "schedule_hours": 72,
    },
    {
        "key": "bid_up_headroom",
        "name": "Dołóż tam, gdzie rentowne i jest co wygrywać",
        "description": (
            "Dobry ACOS przy niskim udziale na górze wyników znaczy, że zostawiasz "
            "ruch konkurencji. To najbezpieczniejszy moment na podwyżkę: wiadomo, "
            "że pozycja sprzedaje, i wiadomo, że jest gdzie rosnąć. Udział "
            "wyświetleń Amazon podaje tylko dla kampanii i targetów — przy "
            "placementach ta reguła nie miałaby na czym pracować."
        ),
        "scope": "TARGET",
        "lookback_days": 30,
        "conditions": {
            "all": [
                {"metric": "clicks", "op": ">=", "value": 20},
                {"metric": "orders", "op": ">=", "value": 2},
                {"metric": "acos", "op": "<", "value": 20},
                {"metric": "tos_share", "op": "<", "value": 30},
            ]
        },
        "action": {"type": "SET_BID_PCT", "percent": 10},
        "limits": {"max_items": 100, "min_bid": 0.05, "max_bid": 5.0, "max_change_pct": 15},
        "schedule_hours": 168,
    },
    {
        "key": "bid_to_target_acos",
        "name": "Ustaw stawkę pod docelowy ACOS",
        "description": (
            "Zamiast ślepego ±%, liczy stawkę z przychodu na kliknięcie: "
            "bid = RPC × docelowy ACOS. Skok ograniczony do ±50%, żeby jedna "
            "szczęśliwa konwersja nie wywindowała stawki."
        ),
        "scope": "TARGET",
        "lookback_days": 30,
        "conditions": {
            "all": [
                {"metric": "clicks", "op": ">=", "value": 20},
                {"metric": "orders", "op": ">=", "value": 1},
            ]
        },
        "action": {"type": "SET_BID_TARGET_ACOS", "target_acos": 30},
        "limits": {"max_items": 200, "min_bid": 0.05, "max_bid": 5.0, "max_change_pct": 50},
        "schedule_hours": 168,
    },
    {
        "key": "placement_boost",
        "name": "Przenieś budżet na lepiej konwertujący placement",
        "description": (
            "Placement o dobrym ACOS dostaje wyższy modyfikator stawki — ruch "
            "przesuwa się tam, gdzie sprzedaż jest najtańsza. Krok +10 p.p. "
            "na uruchomienie, sufit 100%."
        ),
        "scope": "PLACEMENT",
        "lookback_days": 30,
        "conditions": {
            "all": [
                {"metric": "orders", "op": ">=", "value": 5},
                {"metric": "acos", "op": "<", "value": 25},
            ]
        },
        "action": {"type": "SET_PLACEMENT_MODIFIER", "step": 10, "max_percent": 100},
        "limits": {"max_items": 50, "min_percent": 0},
        "schedule_hours": 168,
    },
    {
        "key": "harvest_winners",
        "name": "Przejmij konwertujące frazy jako EXACT",
        "description": (
            "Fraza sprzedaje w kampanii szerokiej — zakładamy jej własny keyword "
            "EXACT, żeby mieć kontrolę nad stawką."
        ),
        "scope": "SEARCH_TERM",
        "lookback_days": 60,
        "conditions": {
            "all": [
                {"metric": "orders", "op": ">=", "value": 3},
                {"metric": "acos", "op": "<", "value": 30},
            ]
        },
        "action": {"type": "HARVEST_KEYWORD", "match_type": "EXACT", "target_acos": 30},
        "limits": {"max_items": 50},
        "schedule_hours": 168,
    },
    {
        "key": "pause_hopeless",
        "name": "Wstrzymaj keywordy bez szans",
        "description": (
            "Dużo kliknięć, zero sprzedaży, wysoki koszt — pauzujemy zamiast "
            "obniżać stawkę w nieskończoność."
        ),
        "scope": "TARGET",
        "lookback_days": 60,
        "conditions": {
            "all": [
                {"metric": "clicks", "op": ">=", "value": 60},
                {"metric": "orders", "op": "==", "value": 0},
            ]
        },
        "action": {"type": "PAUSE"},
        "limits": {"max_items": 100},
        "schedule_hours": 168,
    },
    {
        "key": "budget_up_winners",
        "name": "Zwiększ budżet dobrym kampaniom",
        "description": (
            "Kampania trzyma niski ACOS i realnie sprzedaje — podnosimy budżet, "
            "żeby nie kończyła dnia wcześniej."
        ),
        "scope": "CAMPAIGN",
        "lookback_days": 14,
        "conditions": {
            "all": [
                {"metric": "orders", "op": ">=", "value": 10},
                {"metric": "acos", "op": "<", "value": 25},
            ]
        },
        "action": {"type": "SET_BUDGET_PCT", "percent": 20},
        "limits": {"max_items": 50, "min_budget": 5, "max_budget": 500},
        "schedule_hours": 168,
    },
]


def by_key(key: str) -> dict[str, Any] | None:
    return next((t for t in TEMPLATES if t["key"] == key), None)
