"""Wczytywanie raportu biznesowego Seller Central.

Ads API nie zna sprzedazy organicznej, wiec mianownik do TACOS musi przyjsc
z zewnatrz. Najtansza droga, ktora nie wymaga nowych uprawnien, to eksport CSV
z Seller Central.

Ten plik celowo nie zaklada sztywnych nazw kolumn. Raport pobrany z amazon.de
ma naglowki po niemiecku, z amazon.it po wlosku, a Amazon potrafi je zmienic
miedzy wersjami. Dlatego rozpoznajemy kolumny po slowach kluczowych w kilku
jezykach, a wynik rozpoznania pokazujemy uzytkownikowi do zatwierdzenia -
zamiast po cichu wczytac nie te liczby.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import re
import unicodedata
from typing import Any

# Pola, ktore potrafimy wykorzystac. Kolejnosc wzorcow ma znaczenie: bardziej
# szczegolowe ida pierwsze, zeby "bestellte einheiten" nie zlapalo sie jako
# "bestellungen".
FIELDS: dict[str, dict[str, Any]] = {
    "date": {
        "label": "Data",
        "required": True,
        "keywords": [["date"], ["datum"], ["data"], ["fecha"]],
    },
    "asin": {
        "label": "ASIN",
        "required": False,
        "keywords": [
            ["child", "asin"], ["untergeordnet", "asin"], ["enfant", "asin"],
            ["figlio", "asin"], ["secundario", "asin"], ["onderliggend", "asin"],
            ["asin"],
        ],
    },
    "sku": {
        "label": "SKU",
        "required": False,
        "keywords": [["sku"], ["artikelnummer"], ["referencia"]],
    },
    "sales": {
        "label": "Sprzedaż",
        "required": True,
        "keywords": [
            ["ordered", "product", "sales"], ["produktumsatze"], ["produktumsatz"],
            ["ventes", "produits"], ["vendite", "prodotti"], ["ventas", "productos"],
            ["productverkopen"], ["umsatz"], ["sales"], ["ventes"], ["vendite"],
            ["ventas"], ["omzet"],
        ],
    },
    "units": {
        "label": "Sztuki",
        "required": False,
        "keywords": [
            ["units", "ordered"], ["bestellte", "einheiten"], ["unites", "commandees"],
            ["unita", "ordinate"], ["unidades", "pedidas"], ["bestelde", "eenheden"],
            ["units"], ["einheiten"], ["unita"], ["unidades"],
        ],
    },
    "orders": {
        "label": "Zamówienia",
        "required": False,
        "keywords": [
            ["total", "order", "items"], ["bestellungen", "gesamt"],
            ["articles", "commandes"], ["totale", "articoli", "ordinati"],
            ["pedidos", "total"], ["bestellungen"], ["orders"], ["pedidos"],
        ],
    },
    "sessions": {
        "label": "Sesje",
        "required": False,
        "keywords": [
            ["sessions", "total"], ["sitzungen"], ["sessions"], ["sessioni"],
            ["sesiones"], ["sessies"],
        ],
    },
}


def _norm(text: str) -> str:
    """Naglowek bez ogonkow, znakow i wielkosci liter - do porownywania."""
    bez_ogonkow = "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )
    return re.sub(r"[^a-z0-9]+", " ", bez_ogonkow.lower()).strip()


def guess_mapping(headers: list[str]) -> dict[str, str | None]:
    """Dopasowuje naglowki pliku do naszych pol.

    Naglowek juz przypisany nie jest brany pod uwage drugi raz - inaczej
    "Bestellte Einheiten" i "Bestellungen gesamt" walczylyby o to samo pole.
    """
    znormalizowane = [(h, _norm(h)) for h in headers]
    zajete: set[str] = set()
    out: dict[str, str | None] = {}

    for pole, opis in FIELDS.items():
        trafiony = None
        for slowa in opis["keywords"]:
            for oryginal, norm in znormalizowane:
                if oryginal in zajete:
                    continue
                if all(s in norm for s in slowa):
                    trafiony = oryginal
                    break
            if trafiony:
                break
        if trafiony:
            zajete.add(trafiony)
        out[pole] = trafiony
    return out


def parse_number(raw: Any) -> float | None:
    """Liczba z formatu lokalnego, np. 1.234,56 EUR albo 1,234.56.

    Separator dziesietny rozpoznajemy po tym, ktory znak wystepuje ostatni.
    Zgadywanie po samym znaku byloby zawodne: 1.234 to w jednym kraju tysiac
    dwiescie trzydziesci cztery, a w innym jeden i dwiescie trzydziesci cztery
    tysieczne.
    """
    if raw is None:
        return None
    tekst = str(raw).strip()
    if not tekst:
        return None

    # symbole walut, spacje zwykle i niełamiace, wszystko poza cyframi
    tekst = re.sub(r"[^\d,.\-]", "", tekst.replace(" ", " "))
    if not tekst or tekst in ("-", ".", ","):
        return None

    ostatni_przecinek = tekst.rfind(",")
    ostatnia_kropka = tekst.rfind(".")

    if ostatni_przecinek >= 0 and ostatnia_kropka >= 0:
        # Sa oba znaki - ten dalszy jest dziesietny, blizszy oddziela tysiace
        if ostatni_przecinek > ostatnia_kropka:
            tekst = tekst.replace(".", "").replace(",", ".")
        else:
            tekst = tekst.replace(",", "")
    elif ostatni_przecinek >= 0 or ostatnia_kropka >= 0:
        # Jeden znak, i tu zaczyna sie zgadywanie: "1.234" to w Niemczech tysiac
        # dwiescie trzydziesci cztery, a w Anglii jeden i trzysta czterdziesci
        # tysiecznych. Rozstrzygamy liczba cyfr po znaku - kwoty w raportach
        # maja dwa miejsca po przecinku, wiec trzy cyfry oznaczaja tysiace.
        # Bez tej reguly sprzedaz bywala zanizana tysiac razy, po cichu.
        pozycja = max(ostatni_przecinek, ostatnia_kropka)
        po_znaku = len(tekst) - pozycja - 1
        znak = tekst[pozycja]
        if po_znaku == 3:
            tekst = tekst.replace(znak, "")
        else:
            tekst = tekst.replace(znak, ".")

    try:
        return float(tekst)
    except ValueError:
        return None


DATE_FORMATS = [
    "%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y",
    "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%y", "%d/%m/%y",
]


def parse_date(raw: Any, fmt: str | None = None) -> dt.date | None:
    """Data jednej komorki. Z podanym formatem - tylko nim, inaczej po kolei."""
    if raw is None:
        return None
    tekst = str(raw).strip()
    if not tekst:
        return None
    formaty = [fmt] if fmt else DATE_FORMATS
    for f in formaty:
        try:
            return dt.datetime.strptime(tekst, f).date()
        except ValueError:
            continue
    return None


def detect_date_format(values: list[Any]) -> str | None:
    """Format daty wspolny dla calej kolumny.

    Rozpoznawanie wiersz po wierszu wywraca sie na niejednoznacznosci: "8/1/2026"
    to i 8 stycznia, i 1 sierpnia, a wybor pierwszego pasujacego formatu przesuwa
    czesc dat o miesiace. Cala kolumna prawie zawsze rozstrzyga, bo wystarczy
    jedna wartosc z dniem powyzej dwunastu.

    Wybieramy format, ktory poprawnie czyta najwiecej wartosci; przy remisie
    wygrywa wczesniejszy na liscie, czyli europejski.
    """
    probki = [str(v).strip() for v in values if v is not None and str(v).strip()]
    if not probki:
        return None

    najlepszy = None
    najwiecej = 0
    for f in DATE_FORMATS:
        ile = 0
        for v in probki:
            try:
                dt.datetime.strptime(v, f)
                ile += 1
            except ValueError:
                pass
        if ile > najwiecej:
            najwiecej, najlepszy = ile, f
    return najlepszy


def sniff(content: str, sample_rows: int = 5) -> dict[str, Any]:
    """Rozpoznaje separator, naglowki i proponuje przypisanie kolumn."""
    probka = content[:8192]
    try:
        dialect = csv.Sniffer().sniff(probka, delimiters=",;\t|")
        separator = dialect.delimiter
    except csv.Error:
        # Sniffer lubi sie mylic na plikach z jedna kolumna albo z cudzyslowami
        separator = ";" if probka.count(";") > probka.count(",") else ","

    reader = csv.DictReader(io.StringIO(content), delimiter=separator)
    headers = [h for h in (reader.fieldnames or []) if h and h.strip()]
    proba = []
    for i, row in enumerate(reader):
        if i >= sample_rows:
            break
        proba.append({k: v for k, v in row.items() if k in headers})

    mapping = guess_mapping(headers)
    brakujace = [
        FIELDS[f]["label"] for f, h in mapping.items() if FIELDS[f]["required"] and not h
    ]

    return {
        "delimiter": separator,
        "headers": headers,
        "mapping": mapping,
        "sample": proba,
        "missing_required": brakujace,
        "fields": [
            {"key": k, "label": v["label"], "required": v["required"]}
            for k, v in FIELDS.items()
        ],
    }


def parse(
    content: str,
    profile_id: int,
    mapping: dict[str, str | None],
    delimiter: str = ",",
    source: str = "CSV",
) -> dict[str, Any]:
    """Zamienia CSV na wiersze gotowe do zapisu.

    Wiersze bez daty albo bez kwoty pomijamy i liczymy. Milczace przepuszczenie
    ich zanizyloby TACOS, a to nie jest blad w bezpieczna strone: zanizony TACOS
    zacheca do zwiekszania wydatkow.
    """
    kol_data = mapping.get("date")
    kol_sprzedaz = mapping.get("sales")
    if not kol_data or not kol_sprzedaz:
        raise ValueError("Trzeba wskazac kolumne z data i kolumne ze sprzedaza.")

    # Najpierw jedno przejscie po samej kolumnie z data, zeby ustalic format
    # dla calego pliku - patrz detect_date_format.
    wszystkie = list(csv.DictReader(io.StringIO(content), delimiter=delimiter))
    format_daty = detect_date_format([r.get(kol_data) for r in wszystkie])

    wiersze: dict[tuple, dict[str, Any]] = {}
    pominiete_data = 0
    pominiete_kwota = 0
    zakres: list[dt.date] = []

    for row in wszystkie:
        data = parse_date(row.get(kol_data), format_daty)
        if data is None:
            pominiete_data += 1
            continue
        kwota = parse_number(row.get(kol_sprzedaz))
        if kwota is None:
            pominiete_kwota += 1
            continue

        asin = (row.get(mapping["asin"]) or "").strip().upper() if mapping.get("asin") else ""
        sku = (row.get(mapping["sku"]) or "").strip() if mapping.get("sku") else ""
        zamowienia = parse_number(row.get(mapping["orders"])) if mapping.get("orders") else None
        jednostki = parse_number(row.get(mapping["units"])) if mapping.get("units") else None
        sesje = parse_number(row.get(mapping["sessions"])) if mapping.get("sessions") else None

        klucz = (data, asin or None)
        istniejacy = wiersze.get(klucz)
        if istniejacy:
            # Raport potrafi miec kilka wierszy na ten sam ASIN i dzien
            istniejacy["sales"] += kwota
            istniejacy["units"] += int(jednostki or 0)
            istniejacy["orders"] += int(zamowienia or 0)
            if sesje is not None:
                istniejacy["sessions"] = (istniejacy["sessions"] or 0) + int(sesje)
            continue

        wiersze[klucz] = {
            "date": data,
            "profile_id": profile_id,
            "asin": asin or None,
            "sku": sku or None,
            "sales": kwota,
            "units": int(jednostki or 0),
            "orders": int(zamowienia or 0),
            "sessions": int(sesje) if sesje is not None else None,
            "source": source,
        }
        zakres.append(data)

    return {
        "rows": list(wiersze.values()),
        "skipped_no_date": pominiete_data,
        "skipped_no_amount": pominiete_kwota,
        "start": min(zakres).isoformat() if zakres else None,
        "end": max(zakres).isoformat() if zakres else None,
        "total_sales": round(sum(r["sales"] for r in wiersze.values()), 2),
        "date_format": format_daty,
    }
