"""Sugestie optymalizacyjne liczone z wynikow kampanii.

Trzy zasady, ktore rzadza tym modulem:

1. **Nie wnioskuj z za malej proby.** Prog klikniec nie jest staly - wynika ze
   sredniej konwersji konta. Przy CVR 10% jedno zamowienie wypada srednio co
   10 klikniec, wiec 12 klikniec bez sprzedazy nic nie dowodzi; przy CVR 2%
   trzeba ich piecdziesieciu. Sztywne "20 klikniec" bylo w jednym przypadku
   przeczuleniem, w drugim slepota.

2. **Zmieniaj malo i czesto.** Kazda sugestia stawki jest ograniczona do +/-15%.
   Nawet gdy rachunek mowi, ze stawka powinna byc trzy razy nizsza, jednorazowy
   taki skok wywraca kampanie: traci historie, pozycje w aukcji i dane, na
   ktorych opiera sie kolejna decyzja. Lepiej dojsc tam w kilku krokach.

3. **Patrz na przyczyne, nie na objaw.** Wysoki ACOS przy niskim CTR to problem
   trafnosci, nie stawki - obnizka o 15% niczego nie naprawi. Takie przypadki
   opisujemy, ale nie proponujemy do nich akcji do jednego kliknięcia.

Sugestie niczego nie zmieniaja. Wskazuja miejsce i kierunek; zmiana przechodzi
przez kolejke zatwierdzen jak wszystko inne.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from ..config import settings

MAX_SUGGESTIONS = 3

# Maksymalny jednorazowy skok stawki. Swiadomie maly - patrz zasada 2.
MAX_BID_STEP = 0.15
# Ponizej tej wzglednej roznicy zmiana nie jest warta wpisu w kolejce
MIN_BID_DELTA = 0.08
# Ile oczekiwanych zamowien musi "przepasc", zeby brak sprzedazy cos znaczyl
EXPECTED_ORDERS_FOR_KILL = 3.0
# Twardy dol - ponizej tylu klikniec nie wnioskujemy nic, niezaleznie od CVR
ABSOLUTE_MIN_CLICKS = 12
# Ile wyswietlen trzeba, zeby ocenic CTR
MIN_IMPRESSIONS_FOR_CTR = 2000

# Twarde granice stawki - te same, ktore stosuje warstwa zapisu.
MIN_BID_ABS = 0.02
MAX_BID_ABS = 100.0

# Kazdy produkt reklamowy ma wlasny dol. Sponsored Display zaczyna sie od
# 1,00 EUR - proponowanie 0,85 konczylo sie odmowa Amazona dopiero przy
# zatwierdzaniu, a sugestia do tego momentu wygladala na wykonalna.
DOLNE_STAWKI = {"SP": 0.02, "SB": 0.10, "SD": 1.00}

# Zapas przy przejmowaniu frazy jako keyword. Dopasowanie scisle bywa nieco
# drozsze niz usrednione szerokie, z ktorego fraza pochodzi, wiec sam obecny
# CPC bywa za niski, zeby w ogole wejsc do aukcji.
HARVEST_MARGIN = 1.15

# Udzial wyswietlen na gorze wynikow. Powyzej progu nasycenia licytacja wyzej
# kupuje glownie drozszy ten sam ruch - gora aukcji juz jest nasza. Ponizej
# progu zapasu widac, ze jest co wygrywac, i to wzmacnia argument za podwyzka.
TOS_SATURATED = 80.0
TOS_HEADROOM = 30.0

# Luka miedzy stawka a realnym CPC. Aukcja Amazona rozlicza ponizej stawki
# maksymalnej, wiec CPC duzo nizszy od stawki znaczy, ze nikt nas nie dociska.
# Sama luka niczego nie dowodzi - dopiero razem z wysokim udzialem na gorze
# wynikow mowi, ze pozycje utrzymamy takze taniej.
CPC_SLACK_RATIO = 0.70
CPC_CUSHION = 1.15


@dataclass
class Context:
    """Punkt odniesienia - srednie liczone z tego samego zbioru, ktory oceniamy."""

    avg_acos: float | None = None
    avg_cvr: float | None = None
    avg_ctr: float | None = None
    days: int = 30
    # Mnoznik progu probki. 1.0 to prog wyliczony z konwersji konta - tyle
    # klikniec, ile trzeba, zeby brak sprzedazy przestal byc przypadkiem.
    # Ponizej 1.0 wpuszczamy slabsze sygnaly: mniej pewne, ale wczesniejsze.
    # To swiadomy wybor uzytkownika, nie domyslne zachowanie.
    sensitivity: float = 1.0
    # Sufit odniesienia. Ustawienie, nie stala - wlasciwa wartosc zalezy od
    # marzy, ktorej panel nie zna.
    acos_ceiling: float = field(default_factory=lambda: settings.acos_ceiling)

    @property
    def acos_ref(self) -> float:
        """Do czego porownujemy rentownosc pozycji.

        MNIEJSZA z dwoch wartosci: srednia rynku albo sufit. Sama srednia
        byla zla miarka na rynku, ktory chodzi zle - przy sredniej 50% panel
        proponowal PODNIESIENIE stawki pozycji majacej ACOS 44%, bo formalnie
        byla "lepsza niz srednia". Optymalizowal wiec w strone status quo,
        a nie w strone rentownosci.

        Sufit dziala wylacznie jako hamulec: na rynku zdrowym (srednia ponizej
        sufitu) nie zmienia niczego, wiec nie potrafi pogorszyc tego, co juz
        dziala.
        """
        sredni = self.avg_acos if self.avg_acos and self.avg_acos > 0 else 30.0
        return min(sredni, self.acos_ceiling)

    @property
    def cvr_ref(self) -> float:
        return self.avg_cvr if self.avg_cvr and self.avg_cvr > 0 else 8.0

    @property
    def min_clicks(self) -> int:
        """Ile klikniec potrzeba, zeby brak konwersji cos znaczyl.

        Przy CVR konta 10% i progu 3 oczekiwanych zamowien wychodzi 30 klikniec.
        Przy CVR 2% - 150. To rachunek, nie arbitralny prog.
        """
        needed = EXPECTED_ORDERS_FOR_KILL / (self.cvr_ref / 100) * self.sensitivity
        return max(int(ABSOLUTE_MIN_CLICKS * self.sensitivity), int(round(needed)), 3)

    @property
    def min_clicks_for_bid(self) -> int:
        """Do korekty stawki wystarczy mniejsza proba niz do wykluczenia."""
        return max(ABSOLUTE_MIN_CLICKS, int(round(self.min_clicks * 0.6)))


@dataclass
class Suggestion:
    code: str
    level: str  # good | warn | bad | info
    title: str
    detail: str
    priority: int
    action: dict[str, Any] | None = None
    basis: str | None = None  # na czym oparta - pokazywane pod uzasadnieniem

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "level": self.level,
            "title": self.title,
            "detail": self.detail,
            "action": self.action,
            "basis": self.basis,
        }


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v:.1f}%"


def _money(v: float | None, currency: str) -> str:
    return "—" if v is None else f"{v:,.2f} {currency}".replace(",", " ")


def _basis(row: dict[str, Any]) -> str:
    """Jednolinijkowe podsumowanie proby, na ktorej oparta jest sugestia."""
    return (
        f"{row.get('impressions') or 0:,} wyśw. · {row.get('clicks') or 0} klik. · "
        f"{row.get('orders') or 0} zam. · CTR {_pct(row.get('ctr'))}"
    ).replace(",", " ")


def _capped_bid(
    current: float, target: float, ad_product: str | None = None
) -> float | None:
    """Stawka docelowa przycieta do jednego bezpiecznego kroku.

    Zwraca None, gdy zmiana bylaby kosmetyczna - nie ma sensu zapelniac
    kolejki zatwierdzen korektami o dwa grosze - albo gdy wyliczona wartosc
    lezy ponizej dolu dopuszczalnego dla tego produktu reklamowego. W tym
    drugim przypadku nie ma czego proponowac: nizej po prostu nie da sie zejsc.
    """
    if not current:
        return None

    dol = DOLNE_STAWKI.get(ad_product or "SP", MIN_BID_ABS)
    if target < dol:
        # Jesli juz jestesmy na dole, zadna obnizka nie jest mozliwa.
        if current <= dol:
            return None
        target = dol

    lo, hi = current * (1 - MAX_BID_STEP), current * (1 + MAX_BID_STEP)
    if target < lo:
        # Zaokraglamy do srodka przedzialu, nie zwyczajnie - przy stawce 0,18
        # jeden grosz to juz 5,5%, wiec round() potrafi wypchnac poza widelki
        capped = math.ceil(lo * 100) / 100
    elif target > hi:
        capped = math.floor(hi * 100) / 100
    else:
        capped = round(target, 2)

    if abs(capped - current) / current < MIN_BID_DELTA:
        return None
    return max(0.02, capped)


# --------------------------------------------------------------------------- #
# Kampanie
# --------------------------------------------------------------------------- #


def suggest_campaign(row: dict[str, Any], ctx: Context) -> list[dict[str, Any]]:
    out: list[Suggestion] = []

    state = (row.get("state") or "").upper()
    impressions = row.get("impressions") or 0
    clicks = row.get("clicks") or 0
    cost = row.get("cost") or 0.0
    orders = row.get("orders") or 0
    sales = row.get("sales") or 0.0
    acos = row.get("acos")
    ctr = row.get("ctr") or 0.0
    cvr = row.get("cvr") or 0.0
    budget = row.get("budget") or 0.0
    currency = row.get("currency") or "EUR"
    ref = ctx.acos_ref
    daily = cost / ctx.days if ctx.days else 0.0
    basis = _basis(row)

    if impressions == 0 and cost == 0:
        if state == "ENABLED":
            out.append(Suggestion(
                "not_serving", "warn", "Nie wyświetla się",
                "Aktywna, ale bez jednego wyświetlenia w tym okresie. Najczęściej: "
                "stawka poniżej progu aukcji, brak produktów w grupie reklam albo "
                "produkt niedostępny.",
                90, None, basis,
            ))
        return [s.as_dict() for s in out]

    # --- za mala proba, zeby cokolwiek orzekac ---
    if clicks < ABSOLUTE_MIN_CLICKS and orders == 0:
        out.append(Suggestion(
            "too_early", "info", "Za mało danych",
            f"{clicks} kliknięć to za mało, żeby ocenić tę kampanię. Przy średniej "
            f"konwersji konta {_pct(ctx.cvr_ref)} pierwszego zamówienia można się "
            f"spodziewać dopiero po około {int(100 / ctx.cvr_ref)} kliknięciach.",
            30, None, basis,
        ))
        return [s.as_dict() for s in out]

    if state == "PAUSED" and sales > 0:
        out.append(Suggestion(
            "paused_earner", "info", "Wstrzymana mimo sprzedaży",
            f"Przyniosła {_money(sales, currency)} przy ACOS {_pct(acos)}. "
            "Jeśli wstrzymanie było tymczasowe, warto rozważyć wznowienie.",
            60, None, basis,
        ))

    # --- klika i nie sprzedaje, przy probie, ktora juz cos znaczy ---
    if clicks >= ctx.min_clicks and orders == 0:
        oczekiwane = clicks * ctx.cvr_ref / 100
        out.append(Suggestion(
            "no_conversion", "bad", "Klika, nie sprzedaje",
            f"{clicks} kliknięć i {_money(cost, currency)} bez zamówienia. Przy "
            f"średniej konwersji konta {_pct(ctx.cvr_ref)} powinno ich paść około "
            f"{oczekiwane:.0f}. Zajrzyj we frazy tej kampanii — najpewniej ściąga "
            "ruch obok intencji zakupowej.",
            100, None, basis,
        ))

    # --- wysoki ACOS: rozroznij przyczyne ---
    elif acos is not None and clicks >= ctx.min_clicks_for_bid and acos > ref * 1.5:
        if impressions >= MIN_IMPRESSIONS_FOR_CTR and ctx.avg_ctr and ctr < ctx.avg_ctr * 0.6:
            out.append(Suggestion(
                "high_acos_low_ctr", "warn", "Wysoki ACOS z niskiej trafności",
                f"ACOS {_pct(acos)} przy średniej {_pct(ref)}, ale CTR {_pct(ctr)} "
                f"wobec {_pct(ctx.avg_ctr)} na koncie. Reklama pokazuje się na "
                "niewłaściwych zapytaniach — obniżka stawek tego nie naprawi, "
                "trzeba zawęzić targetowanie albo dołożyć wykluczeń.",
                95, None, basis,
            ))
        else:
            out.append(Suggestion(
                "high_acos", "warn", "ACOS powyżej średniej",
                f"ACOS {_pct(acos)} przy średniej konta {_pct(ref)}, CTR w normie. "
                "Problem leży w stawkach — najskuteczniej korygować je na poziomie "
                "pojedynczych keywordów, nie całej kampanii.",
                90, None, basis,
            ))

    # --- budzet ogranicza rentowna kampanie ---
    if (
        budget and daily >= budget * 0.9 and acos is not None
        and acos < ref and orders >= 3
    ):
        # Krok, nie skok: +20% pokaze, czy jest popyt, bez ryzyka duzej kwoty
        nowy = round(budget * 1.2, 2)
        out.append(Suggestion(
            "budget_capped", "good", f"Podnieś budżet do {_money(nowy, currency)}",
            f"Wydaje {_money(daily, currency)} dziennie przy budżecie "
            f"{_money(budget, currency)}, czyli praktycznie go wyczerpuje, "
            f"a ACOS {_pct(acos)} jest poniżej średniej konta przy {orders} "
            "zamówieniach. Krok +20% sprawdzi, czy jest jeszcze popyt.",
            110,
            {"type": "SET_BUDGET", "campaign_id": row.get("campaign_id"),
             "value": nowy, "from": budget},
            basis,
        ))

    if impressions >= MIN_IMPRESSIONS_FOR_CTR and ctx.avg_ctr and ctr < ctx.avg_ctr * 0.5:
        out.append(Suggestion(
            "low_ctr", "warn", "Niski CTR",
            f"CTR {_pct(ctr)} przy średniej konta {_pct(ctx.avg_ctr)}. Zapytania, "
            "na które trafia ta kampania, nie pasują do oferty — albo reklama "
            "przegrywa wyglądem z sąsiadami w wynikach.",
            70, None, basis,
        ))

    if clicks >= ctx.min_clicks and orders > 0 and cvr < ctx.cvr_ref * 0.5:
        out.append(Suggestion(
            "low_cvr", "warn", "Konwersja poniżej średniej",
            f"CVR {_pct(cvr)} przy średniej konta {_pct(ctx.cvr_ref)}. Ruch trafia, "
            "ale karta produktu go nie domyka — cena, zdjęcia albo opinie mogą "
            "odstawać od konkurencji.",
            65, None, basis,
        ))

    if not out and acos is not None and orders > 0:
        out.append(Suggestion(
            "healthy", "good", "Bez zastrzeżeń",
            f"ACOS {_pct(acos)} przy {orders} zamówieniach — w normie dla tego konta.",
            10, None, basis,
        ))

    out.sort(key=lambda s: s.priority, reverse=True)
    return [s.as_dict() for s in out[:MAX_SUGGESTIONS]]


# --------------------------------------------------------------------------- #
# Keywordy i targety
# --------------------------------------------------------------------------- #


def suggest_target(row: dict[str, Any], ctx: Context) -> list[dict[str, Any]]:
    out: list[Suggestion] = []

    clicks = row.get("clicks") or 0
    orders = row.get("orders") or 0
    cost = row.get("cost") or 0.0
    sales = row.get("sales") or 0.0
    impressions = row.get("impressions") or 0
    acos = row.get("acos")
    ctr = row.get("ctr") or 0.0
    bid = row.get("bid")
    tos = row.get("tos_share")
    currency = row.get("currency") or "EUR"
    ref = ctx.acos_ref
    name = row.get("target_text") or "ta pozycja"
    basis = _basis(row)

    rpc = (sales / clicks) if clicks else 0.0
    # stawka, przy ktorej ta pozycja trafilaby w srednia rentownosc konta
    fair = round(rpc * ref / 100, 2) if rpc else None

    # --- brak sprzedazy przy probie, ktora juz cos znaczy ---
    if clicks >= ctx.min_clicks and orders == 0:
        oczekiwane = clicks * ctx.cvr_ref / 100
        is_keyword = row.get("target_kind") == "KEYWORD"
        # Wlasny keyword WSTRZYMUJEMY, a nie wykluczamy. Wykluczenie o tej samej
        # tresci w tej samej grupie dziala - negatyw wygrywa z keywordem - ale
        # zostawia uklad, ktorego nikt pozniej nie rozumie: slowo wlaczone
        # i jednoczesnie zablokowane. Pauza mowi wprost, co sie stalo, i wraca
        # jednym kliknieciem. Wykluczenia zostaja od tego, czego sami nie
        # kupilismy - czyli od fraz z raportu wyszukiwan.
        out.append(Suggestion(
            "kill", "bad",
            "Wstrzymaj",
            f"{clicks} kliknięć i {_money(cost, currency)} bez zamówienia, "
            f"a przy średniej konwersji konta {_pct(ctx.cvr_ref)} powinno ich paść "
            f"około {oczekiwane:.0f}. Że to przypadek, jest już mało prawdopodobne — "
            + (
                "wstrzymaj ten keyword."
                if is_keyword else
                "wstrzymaj ten target."
            ),
            120,
            {"type": "PAUSE_TARGET", "target_id": row.get("target_id"),
             "kind": row.get("target_kind")},
            basis,
        ))

    # --- za drogo, ale sprzedaje: korekta o jeden krok ---
    elif (
        acos is not None and orders >= 1 and clicks >= ctx.min_clicks_for_bid
        and acos > ref * 1.3 and fair and bid
    ):
        nowy = _capped_bid(bid, fair, row.get("ad_product"))
        if nowy and nowy < bid:
            zmiana = (nowy - bid) / bid * 100
            out.append(Suggestion(
                "bid_down", "warn", f"Obniż stawkę do {_money(nowy, currency)}",
                f"ACOS {_pct(acos)} przy średniej konta {_pct(ref)}. Przychód na "
                f"kliknięcie to {_money(rpc, currency)}, więc docelowa stawka wynosi "
                f"{_money(fair, currency)} — schodzimy tam krokiem {zmiana:.0f}%, "
                "zamiast ciąć od razu, żeby nie stracić pozycji w aukcji.",
                115,
                {"type": "SET_BID", "target_id": row.get("target_id"),
                 "kind": row.get("target_kind"), "value": nowy, "from": bid},
                basis,
            ))

    # --- tanio sprzedaje: dolozyc, ale tylko gdy trafnosc jest dobra ---
    elif (
        acos is not None and orders >= 2 and clicks >= ctx.min_clicks_for_bid
        and acos < ref * 0.7 and fair and bid
    ):
        slaby_ctr = (
            impressions >= MIN_IMPRESSIONS_FOR_CTR
            and ctx.avg_ctr
            and ctr < ctx.avg_ctr * 0.6
        )
        if slaby_ctr:
            out.append(Suggestion(
                "cheap_but_irrelevant", "info", "Tanio, ale słaba trafność",
                f"ACOS {_pct(acos)} wygląda dobrze, ale CTR {_pct(ctr)} przy średniej "
                f"{_pct(ctx.avg_ctr)} mówi, że wyświetleń jest dużo, a klika niewielu. "
                "Podniesienie stawki kupi więcej tego samego — najpierw sprawdź, "
                "na jakich zapytaniach to się pokazuje.",
                60, None, basis,
            ))
        elif tos is not None and tos >= TOS_SATURATED:
            # Rentowne, ale gora wynikow praktycznie zajeta. Podwyzka stawki
            # nie ma juz czego kupic - zostaje szukanie zasiegu gdzie indziej.
            out.append(Suggestion(
                "tos_saturated", "info", "Rentowne, ale bez zapasu na górze",
                f"ACOS {_pct(acos)} jest dobry, ale udział wyświetleń na górze "
                f"wyników to {_pct(tos)} — te aukcje już wygrywasz. Wyższa stawka "
                "kupi ten sam ruch drożej, a nie większy. Zasięgu szukaj raczej "
                "w nowych frazach albo w pozostałych placementach.",
                70, None, basis,
            ))
        else:
            nowy = _capped_bid(bid, fair, row.get("ad_product"))
            if nowy and nowy > bid:
                zmiana = (nowy - bid) / bid * 100
                zapas = (
                    f" Na górze wyników masz dopiero {_pct(tos)} udziału, "
                    "więc jest co wygrywać."
                    if tos is not None and tos < TOS_HEADROOM else ""
                )
                out.append(Suggestion(
                    "bid_up", "good", f"Podnieś stawkę do {_money(nowy, currency)}",
                    f"ACOS {_pct(acos)} przy średniej {_pct(ref)} i {orders} zamówieniach, "
                    f"CTR {_pct(ctr)} w normie. Przychód na kliknięcie "
                    f"{_money(rpc, currency)} udźwignie stawkę {_money(fair, currency)} — "
                    f"próbujemy krokiem +{zmiana:.0f}%, żeby sprawdzić, czy zasięg urośnie."
                    + zapas,
                    118,
                    {"type": "SET_BID", "target_id": row.get("target_id"),
                     "kind": row.get("target_kind"), "value": nowy, "from": bid},
                    basis,
                ))

    # --- placimy duzo ponizej wlasnej stawki, a gore i tak mamy ---
    juz_o_stawce = any(sug.code in ("bid_up", "bid_down") for sug in out)
    cpc = (cost / clicks) if clicks else 0.0
    if (
        not juz_o_stawce and bid and cpc
        and clicks >= ctx.min_clicks_for_bid
        and tos is not None and tos >= TOS_SATURATED
        and cpc <= bid * CPC_SLACK_RATIO
        and (acos is None or acos <= ref * 1.3)
    ):
        cel = round(cpc * CPC_CUSHION, 2)
        nowy = _capped_bid(bid, cel, row.get("ad_product"))
        if nowy and nowy < bid:
            zmiana = (bid - nowy) / bid * 100
            out.append(Suggestion(
                "bid_slack", "info", f"Zejdź ze stawką do {_money(nowy, currency)}",
                f"Stawka {_money(bid, currency)}, a faktycznie płacisz "
                f"{_money(cpc, currency)} — to {cpc / bid * 100:.0f}% Twojej stawki. "
                f"Przy udziale {_pct(tos)} na górze wyników te aukcje wygrywasz z "
                f"zapasem, więc obniżka o {zmiana:.0f}% powinna zostawić pozycję na "
                "miejscu. Zastrzeżenie: CPC to średnia z okresu, a stawka jest "
                "wartością bieżącą — jeśli podnosiłeś ją w tych dniach, porównanie "
                "nie ma sensu i tę sugestię pomiń. Po zmianie sprawdź udział "
                "po kilku dniach.",
                85,
                {"type": "SET_BID", "target_id": row.get("target_id"),
                 "kind": row.get("target_kind"), "value": nowy, "from": bid},
                basis,
            ))

    # --- nie startuje w aukcji ---
    if impressions < 50 and clicks == 0 and bid:
        out.append(Suggestion(
            "no_impressions", "info", "Nie startuje w aukcji",
            f"{impressions} wyświetleń przy stawce {_money(bid, currency)}. "
            "Za nisko, żeby się pokazać — albo zapytanie jest zbyt niszowe. "
            "Bez wyświetleń nie ma czego optymalizować, więc nie proponuję "
            "tu żadnej zmiany automatycznie.",
            80, None, basis,
        ))

    if not out and 0 < clicks < ctx.min_clicks and orders == 0:
        out.append(Suggestion(
            "watch", "info", "Za wcześnie na ocenę",
            f"{clicks} kliknięć bez zamówienia. Do wniosku potrzeba ich "
            f"co najmniej {ctx.min_clicks} — przy średniej konwersji konta "
            f"{_pct(ctx.cvr_ref)} wcześniejszy brak sprzedaży mieści się w przypadku.",
            20, None, basis,
        ))

    out.sort(key=lambda s: s.priority, reverse=True)
    return [s.as_dict() for s in out[:MAX_SUGGESTIONS]]


# --------------------------------------------------------------------------- #
# Frazy wyszukiwania - stad biora sie wykluczenia
# --------------------------------------------------------------------------- #


def _looks_like_asin(text: str) -> bool:
    t = (text or "").strip().upper()
    return len(t) == 10 and t.startswith("B") and t.isalnum()


def suggest_search_term(row: dict[str, Any], ctx: Context) -> list[dict[str, Any]]:
    """Sugestie dla pojedynczej frazy wyszukiwania.

    Fraza rozni sie od keyworda tym, ze nie ma wlasnej stawki - jedyne, co da
    sie z nia zrobic, to wykluczyc albo przejac jako nowy keyword. Frazy bedace
    numerem ASIN wykluczamy jako produkt, nie jako slowo: wpisanie "B0851J3P81"
    do negatywnych keywordow nie zablokowaloby wyswietlen na tej karcie.
    """
    out: list[Suggestion] = []

    clicks = row.get("clicks") or 0
    orders = row.get("orders") or 0
    cost = row.get("cost") or 0.0
    sales = row.get("sales") or 0.0
    acos = row.get("acos")
    term = (row.get("search_term") or "").strip()
    campaign_id = row.get("campaign_id")
    ad_group_id = row.get("ad_group_id")
    currency = row.get("currency") or "EUR"
    ref = ctx.acos_ref
    basis = _basis(row)

    if not term or not campaign_id or not ad_group_id:
        return []

    asin = _looks_like_asin(term)

    # --- klika i nie sprzedaje ---
    if clicks >= ctx.min_clicks and orders == 0:
        oczekiwane = clicks * ctx.cvr_ref / 100
        out.append(Suggestion(
            "term_kill", "bad",
            "Wyklucz ASIN" if asin else "Wyklucz frazę",
            f"{clicks} kliknięć i {_money(cost, currency)} bez zamówienia. Przy "
            f"średniej konwersji konta {_pct(ctx.cvr_ref)} powinno paść około "
            f"{oczekiwane:.0f}. "
            + (
                "To numer produktu — wykluczamy go z targetowania produktowego, "
                "nie jako słowo kluczowe."
                if asin else
                "Fraza ściąga ruch obok intencji zakupowej."
            ),
            120,
            {"type": "ADD_NEGATIVE_ASIN", "campaign_id": campaign_id,
             "ad_group_id": ad_group_id, "asin": term.upper()}
            if asin else
            {"type": "ADD_NEGATIVE", "campaign_id": campaign_id,
             "ad_group_id": ad_group_id, "keyword_text": term,
             "match_type": "NEGATIVE_EXACT"},
            basis,
        ))

    # --- sprzedaje dobrze: warto miec wlasny keyword ---
    elif orders >= 2 and acos is not None and acos < ref * 0.8 and not asin:
        # Stawka startowa: zaczynamy tam, gdzie ruch juz jest, a nie przy cenie
        # progowej. Fraza konwertuje przy swoim obecnym CPC, wiec nie ma powodu
        # od razu placic wiecej - przejecie ma dac kontrole nad stawka, nie
        # podwyzke. Podnosic bedziemy pozniej, gdy keyword sam sie obroni.
        #
        #   podloga = obecny CPC + HARVEST_MARGIN (exact bywa drozszy niz
        #             usrednione szerokie, wiec sam CPC bywa za niski, zeby
        #             w ogole wejsc do aukcji)
        #   sufit   = przychod na klikniecie razy docelowy ACOS - powyzej tego
        #             fraza przestaje sie spinac przy zalozonej rentownosci
        #
        # Gdy podloga przebija sufit, wygrywa sufit: znaczy to, ze fraza nie
        # jest tak rentowna, jak sugeruje sama liczba zamowien.
        rpc = (sales / clicks) if clicks else 0.0
        cpc = (cost / clicks) if clicks else 0.0
        sufit = rpc * ref / 100 if rpc else 0.0
        podloga = cpc * HARVEST_MARGIN if cpc else 0.0

        nowa_stawka = None
        oparta_o = ""
        if podloga and sufit:
            surowa = min(podloga, sufit)
            oparta_o = "obecny CPC" if podloga <= sufit else "próg rentowności"
        elif sufit:
            surowa = sufit
            oparta_o = "próg rentowności"
        elif podloga:
            surowa = podloga
            oparta_o = "obecny CPC"
        else:
            surowa = 0.0

        if surowa:
            nowa_stawka = round(max(MIN_BID_ABS, min(surowa, MAX_BID_ABS)), 2)

        if nowa_stawka and oparta_o == "obecny CPC":
            opis_stawki = (
                f" Stawkę startową proponuję {_money(nowa_stawka, currency)} — "
                f"tyle mniej więcej płacisz dziś za tę frazę ({_money(cpc, currency)} "
                "za kliknięcie), z niewielkim zapasem na dopasowanie ścisłe. "
                f"Ekonomia udźwignęłaby nawet {_money(sufit, currency)}, ale na "
                "start lepiej nie podnosić ceny ruchu, który już konwertuje."
            )
        elif nowa_stawka:
            opis_stawki = (
                f" Stawkę startową proponuję {_money(nowa_stawka, currency)} — "
                f"to próg wynikający z przychodu na kliknięcie {_money(rpc, currency)} "
                f"przy docelowym ACOS {_pct(ref)}. Jest niższa niż obecny CPC "
                f"{_money(cpc, currency)}, więc fraza sprzedaje słabiej, niż sugeruje "
                "sama liczba zamówień."
            )
        else:
            opis_stawki = (
                " Stawki nie liczę, bo brakuje danych o koszcie i przychodzie na "
                "kliknięcie — keyword dostanie stawkę domyślną grupy."
            )

        out.append(Suggestion(
            "harvest", "good", "Przejmij jako keyword",
            f"{orders} zamówień przy ACOS {_pct(acos)}, poniżej średniej konta "
            f"{_pct(ref)}. Własny keyword ścisły da kontrolę nad stawką na tej frazie "
            f"zamiast polegać na dopasowaniu szerokim.{opis_stawki}",
            100,
            {"type": "HARVEST", "campaign_id": campaign_id, "ad_group_id": ad_group_id,
             "keyword_text": term, "match_type": "EXACT",
             **({"value": nowa_stawka} if nowa_stawka else {})},
            basis,
        ))

    return [s.as_dict() for s in out[:MAX_SUGGESTIONS]]


# --------------------------------------------------------------------------- #
# Reklamy produktowe
# --------------------------------------------------------------------------- #


def suggest_product(row: dict[str, Any], ctx: Context) -> list[dict[str, Any]]:
    """Sugestie dla pojedynczego produktu w grupie reklam.

    Produktu nie da sie wykluczyc - mozna tylko wstrzymac jego reklame. Prog
    jest tu wyzszy niz przy keywordzie, bo produkt bywa kupowany po dluzszym
    namysle: ktos klika, oglada, wraca za tydzien. Wstrzymanie po trzydziestu
    klikniecach ucielo by takze te opoznione konwersje.
    """
    out: list[Suggestion] = []

    clicks = row.get("clicks") or 0
    orders = row.get("orders") or 0
    cost = row.get("cost") or 0.0
    acos = row.get("acos")
    ad_id = row.get("ad_id")
    sku = row.get("sku") or "ten produkt"
    currency = row.get("currency") or "EUR"
    ref = ctx.acos_ref
    basis = _basis(row)

    if not ad_id:
        return []

    prog = int(ctx.min_clicks * 1.5)

    if clicks >= prog and orders == 0:
        oczekiwane = clicks * ctx.cvr_ref / 100
        out.append(Suggestion(
            "product_kill", "bad", "Wstrzymaj reklamę produktu",
            f"{sku}: {clicks} kliknięć i {_money(cost, currency)} bez zamówienia. "
            f"Przy średniej konwersji konta {_pct(ctx.cvr_ref)} powinno paść około "
            f"{oczekiwane:.0f}. Próg jest tu wyższy niż przy keywordach, bo produkt "
            "bywa kupowany po namyśle — ale ta liczba już go przekracza.",
            115,
            {"type": "PAUSE_PRODUCT", "ad_id": str(ad_id)},
            basis,
        ))
    elif acos is not None and orders >= 2 and acos > ref * 2:
        out.append(Suggestion(
            "product_expensive", "warn", "Bardzo drogo sprzedaje",
            f"{sku}: ACOS {_pct(acos)} przy średniej konta {_pct(ref)}. Produkt "
            "sprzedaje, ale każde zamówienie kosztuje dwa razy więcej niż przeciętnie. "
            "Warto sprawdzić cenę i konkurencję na tej karcie, zanim ruszysz stawki.",
            85, None, basis,
        ))

    return [s.as_dict() for s in out[:MAX_SUGGESTIONS]]


# --------------------------------------------------------------------------- #
# Placementy
# --------------------------------------------------------------------------- #


def suggest_placement(
    row: dict[str, Any], ctx: Context, best_acos: float | None
) -> list[dict[str, Any]]:
    """Modyfikator dotyka wszystkich stawek w kampanii.

    Skutek jest szerszy niz przy pojedynczym keywordzie, wiec i prog wejscia
    jest wyzszy: minimum piec zamowien i pelna proba klikniec.
    """
    out: list[Suggestion] = []

    acos = row.get("acos")
    orders = row.get("orders") or 0
    clicks = row.get("clicks") or 0
    modifier = row.get("modifier") or 0
    cost = row.get("cost") or 0.0
    currency = row.get("currency") or "EUR"
    ref = ctx.acos_ref
    basis = _basis(row)

    if orders < 5 or clicks < ctx.min_clicks or acos is None:
        return []

    if acos < ref * 0.75 and modifier < 100:
        krok = 10
        docelowy = min(100, int(modifier) + krok)
        out.append(Suggestion(
            "placement_up", "good", f"Podnieś modyfikator do +{docelowy}%",
            f"ACOS {_pct(acos)} przy średniej konta {_pct(ref)}, {orders} zamówień "
            f"z {clicks} kliknięć. Krok +{krok} p.p. przesunie część ruchu tutaj, "
            "nie ruszając stawek w pozostałych miejscach.",
            110,
            {"type": "SET_PLACEMENT", "campaign_id": row.get("campaign_id"),
             "placement": row.get("placement"), "value": docelowy, "from": int(modifier)},
            basis,
        ))
    elif acos > ref * 1.5 and modifier > 0:
        docelowy = max(0, int(modifier) - 10)
        out.append(Suggestion(
            "placement_down", "warn", f"Obniż modyfikator do +{docelowy}%",
            f"ACOS {_pct(acos)} przy średniej {_pct(ref)}, a modyfikator "
            f"+{int(modifier)}% dodatkowo podbija tu stawki. Wydatek "
            f"{_money(cost, currency)} przesunie się tam, gdzie sprzedaje taniej.",
            105,
            {"type": "SET_PLACEMENT", "campaign_id": row.get("campaign_id"),
             "placement": row.get("placement"), "value": docelowy, "from": int(modifier)},
            basis,
        ))
    elif best_acos is not None and acos > best_acos * 1.6:
        out.append(Suggestion(
            "placement_worse", "info", "Droższy niż pozostałe",
            f"ACOS {_pct(acos)} wobec {_pct(best_acos)} na najlepszym placemencie "
            "tej kampanii. Modyfikator jest zerowy, więc nie ma czego obniżać — "
            "różnica bierze się z samej aukcji.",
            50, None, basis,
        ))

    return [s.as_dict() for s in out[:MAX_SUGGESTIONS]]


def build_context(
    rows: list[dict[str, Any]], days: int, sensitivity: float = 1.0
) -> Context:
    cost = sum(r.get("cost") or 0 for r in rows)
    sales = sum(r.get("sales") or 0 for r in rows)
    clicks = sum(r.get("clicks") or 0 for r in rows)
    orders = sum(r.get("orders") or 0 for r in rows)
    impressions = sum(r.get("impressions") or 0 for r in rows)

    return Context(
        avg_acos=(cost / sales * 100) if sales else None,
        avg_cvr=(orders / clicks * 100) if clicks else None,
        avg_ctr=(clicks / impressions * 100) if impressions else None,
        days=days,
        sensitivity=sensitivity,
    )
