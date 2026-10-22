"""Wykrywanie nagłych zmian w kampaniach.

Tabele pokazują sumy za okres - a suma ukrywa dokładnie to, co trzeba zauważyć
najszybciej: że coś zmieniło się przedwczoraj. Kampania, która przez trzy
tygodnie wydawała 20 EUR dziennie, a od wczoraj wydaje 90, ma za caly miesiac
przyzwoity wynik i nie zwroci niczyjej uwagi.

Dwie rzeczy, ktore decyduja o tym, ze modul jest uzyteczny, a nie halasliwy:

1. **Odniesieniem jest mediana, nie srednia.** Jeden dzien wyprzedazy zawyza
   srednia tak, ze reszta okresu przestaje odstawac. Mediana i MAD (mediana
   odchylen) sa na to odporne.

2. **Sprzedaz liczymy z opoznieniem.** Amazon przypisuje zamowienia do
   klikniecia przez 7 dni, wiec sprzedaz z wczoraj jest jeszcze niepelna.
   Sprawdzone na danych: na DE swieze dni maja ROAS nizszy o okolo 25% od
   dojrzalych. Bez tego opoznienia alert "sprzedaz spadla" zapalalby sie
   codziennie dla wszystkiego. Koszt, klikniecia i wyswietlenia sa domkniete
   tego samego dnia, wiec te sygnaly moga isc od razu.
"""
from __future__ import annotations

import datetime as dt
import statistics
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Campaign, CampaignDaily, Portfolio, SearchTermDaily

# Ile ostatnich dni sklada sie na "teraz"
RECENT_DAYS = 3

# Ile dni wstecz stanowi normę, do której porownujemy
BASELINE_DAYS = 21

# O ile dni cofamy okno przy sygnalach sprzedazowych - patrz naglowek modulu
ATTRIBUTION_LAG_DAYS = 3

# Prog nietypowosci. 3.5 to standardowa wartosc dla zmodyfikowanego wyniku Z
# opartego na MAD.
Z_PROG = 3.5

# Nawet istotna statystycznie zmiana o 8% nie jest warta alertu.
MIN_ZMIANA = 0.25

# Gdy MAD wynosi zero (kampania stoi jak wryta), wynik Z nie ma sensu -
# wtedy wymagamy duzo wiekszej zmiany wzglednej.
MIN_ZMIANA_BEZ_ROZRZUTU = 0.60

# Ponizej tego kampania jest za mala, zeby jej wahania cokolwiek znaczyly.
MIN_KOSZT_DZIENNY = 1.0
MIN_KLIKNIEC_DZIENNIE = 3.0


class Sygnal:
    """Opis jednego rodzaju anomalii."""

    def __init__(
        self, key: str, kierunek: str, tytul: str, opis: str,
        *, opozniony: bool = False, waga: str = "warn",
    ):
        self.key = key
        self.kierunek = kierunek        # "up" albo "down"
        self.tytul = tytul
        self.opis = opis
        self.opozniony = opozniony      # czy dotyczy danych sprzedazowych
        self.waga = waga


SYGNALY = [
    Sygnal(
        "cost", "up", "Wydatek skoczył",
        "Kampania wydaje wyraźnie więcej niż zwykle. Najczęściej: podniesiona "
        "stawka, nowe frazy w auto-targetowaniu albo wzrost konkurencji w aukcji.",
        waga="bad",
    ),
    Sygnal(
        "cpc", "up", "Kliknięcia podrożały",
        "Średnia cena kliknięcia odstaje od normy tej kampanii. Zwykle znaczy, "
        "że ktoś przelicytował — ta sama liczba kliknięć kosztuje więcej.",
    ),
    Sygnal(
        "impressions", "down", "Przestała się wyświetlać",
        "Wyświetlenia spadły znacznie poniżej normy. Warto sprawdzić budżet, "
        "status kampanii i czy produkty są dostępne.",
        waga="bad",
    ),
    Sygnal(
        "clicks", "down", "Ruch spadł",
        "Mniej kliknięć niż zwykle przy zbliżonej ekspozycji — zwykle spadek "
        "pozycji w aukcji.",
    ),
    Sygnal(
        "sales", "down", "Sprzedaż spadła",
        "Przychód z reklam poniżej normy tej kampanii. Sprawdzone na danych "
        "starszych niż trzy dni, żeby nie mylić spadku z niedokończoną "
        "atrybucją Amazona.",
        opozniony=True, waga="bad",
    ),
    Sygnal(
        "cvr", "down", "Konwersja siadła",
        "Ten sam ruch kupuje rzadziej. Zwykle przyczyna jest poza reklamą: "
        "cena, brak stanu, utrata Buy Boxa albo świeże negatywne opinie.",
        opozniony=True,
    ),
]


def _mad(wartosci: list[float], mediana: float) -> float:
    """Mediana odchyleń bezwzględnych - odporny odpowiednik odchylenia std."""
    if not wartosci:
        return 0.0
    return statistics.median([abs(w - mediana) for w in wartosci])


def _serie(wiersze: list[Any]) -> dict[str, dict[dt.date, float]]:
    """Przelicza dzienne wiersze na serie każdej metryki."""
    out: dict[str, dict[dt.date, float]] = {
        k: {} for k in ("cost", "clicks", "impressions", "sales", "cpc", "cvr")
    }
    for r in wiersze:
        d = r.date
        koszt = float(r.cost or 0)
        klik = float(r.clicks or 0)
        out["cost"][d] = koszt
        out["clicks"][d] = klik
        out["impressions"][d] = float(r.impressions or 0)
        out["sales"][d] = float(r.sales or 0)
        # Ilorazy maja sens tylko przy ruchu - inaczej dostajemy zera, ktore
        # udaja "spadek do zera", choc po prostu nie bylo z czego liczyc.
        if klik:
            out["cpc"][d] = koszt / klik
            out["cvr"][d] = float(r.orders or 0) / klik * 100
    return out


def _ocen(
    seria: dict[dt.date, float], swieze: list[dt.date], norma: list[dt.date],
) -> dict[str, float] | None:
    """Porownuje ostatnie dni z norma. None, gdy brak danych do porownania."""
    baza = [seria[d] for d in norma if d in seria]
    teraz = [seria[d] for d in swieze if d in seria]
    # Norma z kilku dni to nie norma; jeden swiezy dzien to nie trend.
    if len(baza) < 7 or len(teraz) < 2:
        return None

    mediana = statistics.median(baza)
    biezaca = statistics.mean(teraz)
    mad = _mad(baza, mediana)

    if mediana == 0:
        return None
    zmiana = (biezaca - mediana) / abs(mediana)

    if mad > 0:
        z = 0.6745 * (biezaca - mediana) / mad
        istotne = abs(z) >= Z_PROG and abs(zmiana) >= MIN_ZMIANA
    else:
        z = 0.0
        istotne = abs(zmiana) >= MIN_ZMIANA_BEZ_ROZRZUTU

    if not istotne:
        return None

    return {"baseline": mediana, "current": biezaca, "change": zmiana, "z": z}


def detect(
    session: Session,
    profile_id: int | None,
    end: dt.date | None = None,
    limit: int = 60,
) -> dict[str, Any]:
    """Kampanie, w ktorych ostatnie dni odstaja od ich wlasnej normy.

    Punktem odniesienia jest sama kampania, a nie konto: dla jednej 40 EUR
    dziennie to norma, dla drugiej trzykrotnosc.
    """
    stmt = select(func.max(CampaignDaily.date))
    if profile_id:
        stmt = stmt.where(CampaignDaily.profile_id == profile_id)
    ostatni = session.execute(stmt).scalar()
    end = end or ostatni
    if not end:
        return {"window": None, "items": []}
    end = min(end, ostatni) if ostatni else end

    najstarszy = end - dt.timedelta(
        days=RECENT_DAYS + BASELINE_DAYS + ATTRIBUTION_LAG_DAYS
    )

    warunki = [CampaignDaily.date >= najstarszy, CampaignDaily.date <= end]
    if profile_id:
        warunki.append(CampaignDaily.profile_id == profile_id)

    wiersze = session.execute(
        select(CampaignDaily).where(*warunki).order_by(CampaignDaily.date)
    ).scalars().all()

    po_kampanii: dict[str, list[Any]] = {}
    for r in wiersze:
        po_kampanii.setdefault(r.campaign_id, []).append(r)

    kampanie = {
        c.campaign_id: c
        for c in session.execute(
            select(Campaign).where(Campaign.campaign_id.in_(list(po_kampanii)))
        ).scalars()
    }
    portfolia = dict(
        session.execute(select(Portfolio.portfolio_id, Portfolio.name)).all()
    )

    def dni(od: int, ile: int) -> list[dt.date]:
        """Lista dat: `ile` dni konczac `od` dni przed koncem okna."""
        koniec = end - dt.timedelta(days=od)
        return [koniec - dt.timedelta(days=i) for i in range(ile)]

    swieze_teraz = dni(0, RECENT_DAYS)
    swieza_norma = dni(RECENT_DAYS, BASELINE_DAYS)
    pozne_teraz = dni(ATTRIBUTION_LAG_DAYS, RECENT_DAYS)
    pozna_norma = dni(ATTRIBUTION_LAG_DAYS + RECENT_DAYS, BASELINE_DAYS)

    znalezione: list[dict[str, Any]] = []

    for campaign_id, dane in po_kampanii.items():
        kampania = kampanie.get(campaign_id)
        if not kampania or kampania.state != "ENABLED":
            continue

        seria = _serie(dane)

        # Bramka wielkosci: liczymy ja z normy, nie z ostatnich dni, zeby
        # kampania wygaszona wczoraj nie wypadla z analizy.
        koszty = [seria["cost"][d] for d in swieza_norma if d in seria["cost"]]
        kliki = [seria["clicks"][d] for d in swieza_norma if d in seria["clicks"]]
        if not koszty:
            continue
        if (
            statistics.median(koszty) < MIN_KOSZT_DZIENNY
            and statistics.median(kliki or [0]) < MIN_KLIKNIEC_DZIENNIE
        ):
            continue

        for sygnal in SYGNALY:
            teraz = pozne_teraz if sygnal.opozniony else swieze_teraz
            norma = pozna_norma if sygnal.opozniony else swieza_norma

            wynik = _ocen(seria[sygnal.key], teraz, norma)
            if not wynik:
                continue
            # Kierunek musi sie zgadzac: wzrost kosztu to alert, spadek nie.
            if sygnal.kierunek == "up" and wynik["change"] <= 0:
                continue
            if sygnal.kierunek == "down" and wynik["change"] >= 0:
                continue

            # Kontekst finansowy tych samych dni. Sprzedaz podajemy razem ze
            # znacznikiem niedojrzalosci: przy sygnalach na swiezych dniach
            # jest jeszcze niepelna i nie wolno jej czytac jak koncowej.
            k_teraz = sum(seria["cost"].get(d, 0.0) for d in teraz)
            s_teraz = sum(seria["sales"].get(d, 0.0) for d in teraz)

            znalezione.append({
                "recent_cost": round(k_teraz, 2),
                "recent_sales": round(s_teraz, 2),
                "recent_acos": round(k_teraz / s_teraz * 100, 1) if s_teraz else None,
                "sales_incomplete": not sygnal.opozniony,
                "campaign_id": campaign_id,
                "campaign_name": kampania.name,
                "portfolio_id": kampania.portfolio_id,
                "portfolio_name": portfolia.get(kampania.portfolio_id),
                "targeting_type": kampania.targeting_type,
                "metric": sygnal.key,
                "code": f"{sygnal.key}_{sygnal.kierunek}",
                "title": sygnal.tytul,
                "detail": sygnal.opis,
                "level": sygnal.waga,
                "delayed": sygnal.opozniony,
                "baseline": round(wynik["baseline"], 2),
                "current": round(wynik["current"], 2),
                "change_pct": round(wynik["change"] * 100, 1),
                "z": round(wynik["z"], 1),
                "window": {
                    "from": min(teraz).isoformat(),
                    "to": max(teraz).isoformat(),
                },
            })

    # Najpierw najwieksze odchylenia, a przy remisie te o wiekszej wadze.
    kolejnosc = {"bad": 0, "warn": 1, "info": 2}
    znalezione.sort(
        key=lambda a: (kolejnosc.get(a["level"], 3), -abs(a["change_pct"]))
    )

    return {
        "window": {
            "end": end.isoformat(),
            "recent_days": RECENT_DAYS,
            "baseline_days": BASELINE_DAYS,
            "attribution_lag_days": ATTRIBUTION_LAG_DAYS,
        },
        "items": znalezione[:limit],
        "total": len(znalezione),
    }


# --------------------------------------------------------------------------- #
# Co napedzilo anomalie
# --------------------------------------------------------------------------- #

# Ile pozycji pokazujemy w rozwinieciu - dluzsza lista przestaje byc odpowiedzia
MAX_WINOWAJCOW = 12


def contributors(
    session: Session,
    campaign_id: str,
    metric: str = "cost",
    end: dt.date | None = None,
) -> dict[str, Any]:
    """Frazy, ktore najbardziej zmienily zachowanie tej kampanii.

    Alert "wydatek skoczyl" mowi, ze cos sie stalo, ale nie co. W kampaniach
    automatycznych przyczyna jest prawie zawsze ta sama: Amazon rozszerzyl
    dopasowanie i zaczal kupowac ruch na frazach, ktorych wczesniej nie bylo.
    Bez tego rozbicia trzeba to sprawdzac recznie w panelu Amazona.

    Porownujemy te same okna, co przy wykrywaniu anomalii, zeby liczby
    zgadzaly sie z tym, co pokazal alert.
    """
    stmt = select(func.max(SearchTermDaily.date)).where(
        SearchTermDaily.campaign_id == campaign_id
    )
    ostatni = session.execute(stmt).scalar()
    end = end or ostatni
    if not end:
        return {"items": [], "window": None}

    swieze_od = end - dt.timedelta(days=RECENT_DAYS - 1)
    norma_do = swieze_od - dt.timedelta(days=1)
    norma_od = norma_do - dt.timedelta(days=BASELINE_DAYS - 1)

    wiersze = session.execute(
        select(
            SearchTermDaily.search_term,
            SearchTermDaily.ad_group_id,
            SearchTermDaily.date,
            SearchTermDaily.cost,
            SearchTermDaily.clicks,
            SearchTermDaily.orders,
            SearchTermDaily.sales,
        ).where(
            SearchTermDaily.campaign_id == campaign_id,
            SearchTermDaily.date >= norma_od,
            SearchTermDaily.date <= end,
        )
    ).all()

    agg: dict[str, dict[str, Any]] = {}
    for fraza, grupa, data, koszt, klik, zam, sprzedaz in wiersze:
        w = agg.setdefault(fraza, {
            "search_term": fraza, "ad_group_id": grupa,
            "teraz": {"cost": 0.0, "clicks": 0, "orders": 0, "sales": 0.0},
            "norma": {"cost": 0.0, "clicks": 0, "orders": 0, "sales": 0.0},
        })
        cel = w["teraz"] if data >= swieze_od else w["norma"]
        cel["cost"] += float(koszt or 0)
        cel["clicks"] += int(klik or 0)
        cel["orders"] += int(zam or 0)
        cel["sales"] += float(sprzedaz or 0)

    out: list[dict[str, Any]] = []
    for w in agg.values():
        teraz_dziennie = w["teraz"]["cost"] / RECENT_DAYS
        norma_dziennie = w["norma"]["cost"] / BASELINE_DAYS
        roznica = teraz_dziennie - norma_dziennie
        # Zaokraglamy do grosza; ponizej tego to szum, nie przyczyna.
        if abs(roznica) < 0.01:
            continue

        koszt = w["teraz"]["cost"]
        sprzedaz = w["teraz"]["sales"]
        out.append({
            "search_term": w["search_term"],
            "ad_group_id": w["ad_group_id"],
            "daily_now": round(teraz_dziennie, 2),
            "daily_before": round(norma_dziennie, 2),
            "daily_change": round(roznica, 2),
            # Fraza, ktorej wczesniej nie bylo w ogole - typowy skutek
            # rozszerzenia dopasowania przez Amazona.
            "is_new": w["norma"]["clicks"] == 0,
            "cost": round(koszt, 2),
            "clicks": w["teraz"]["clicks"],
            "orders": w["teraz"]["orders"],
            "sales": round(sprzedaz, 2),
            "acos": round(koszt / sprzedaz * 100, 1) if sprzedaz else None,
        })

    rosnace = [w for w in out if w["daily_change"] > 0]
    rosnace.sort(key=lambda w: -w["daily_change"])
    wybrane = rosnace[:MAX_WINOWAJCOW]

    # Ile z przyrostu wydatku poszlo na frazy, ktore nic nie sprzedaly.
    przyrost = sum(w["daily_change"] for w in rosnace)
    bez_sprzedazy = sum(w["daily_change"] for w in rosnace if not w["orders"])

    return {
        "window": {
            "recent_from": swieze_od.isoformat(),
            "recent_to": end.isoformat(),
            "baseline_from": norma_od.isoformat(),
            "baseline_to": norma_do.isoformat(),
        },
        "items": wybrane,
        "total_terms": len(rosnace),
        "daily_increase": round(przyrost, 2),
        "daily_increase_no_sales": round(bez_sprzedazy, 2),
    }
