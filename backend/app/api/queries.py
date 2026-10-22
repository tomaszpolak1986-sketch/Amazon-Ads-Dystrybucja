"""Zapytania agregujace dla dashboardu."""
from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

from sqlalchemy import Text, and_, case, cast, func, or_, select, literal
from sqlalchemy.orm import Session

from . import actions as actions_mod, advice, targeting_names
from ..config import settings
from ..etl import catalog as catalog_etl, labels as labels_etl, total_sales
from ..models import (
    FxRate,
    DismissedSuggestion,
    AdGroup,
    Campaign,
    FulfillmentDaily,
    Portfolio,
    CampaignDaily,
    Keyword,
    Listing,
    NegativeKeyword,
    NegativeTarget,
    PlacementDaily,
    ProductAd,
    ProductDaily,
    Profile,
    SearchTermDaily,
    Target,
    TargetDaily,
    PendingPlan,
    TotalSalesDaily,
    SyncRun,
)


def default_range(days: int = 30) -> tuple[dt.date, dt.date]:
    end = dt.date.today() - dt.timedelta(days=1)
    return end - dt.timedelta(days=days - 1), end


def _derive(row: dict[str, Any]) -> dict[str, Any]:
    """Dokłada metryki pochodne, zabezpieczone przed dzieleniem przez zero."""
    impressions = row.get("impressions") or 0
    clicks = row.get("clicks") or 0
    cost = row.get("cost") or 0.0
    sales = row.get("sales") or 0.0
    orders = row.get("orders") or 0

    row["ctr"] = round(clicks / impressions * 100, 3) if impressions else 0.0
    row["cpc"] = round(cost / clicks, 3) if clicks else 0.0
    row["cvr"] = round(orders / clicks * 100, 2) if clicks else 0.0
    row["acos"] = round(cost / sales * 100, 2) if sales else None
    row["roas"] = round(sales / cost, 2) if cost else None
    row["cpa"] = round(cost / orders, 2) if orders else None

    # Udzial wyswietlen na gorze wynikow usredniamy wazac wyswietleniami, a nie
    # zwykla srednia z dni: dzien z 10 wyswietleniami nie moze wazyc tyle samo
    # co dzien z 10 tysiacami. Dni bez tej metryki nie wchodza do mianownika,
    # wiec brak danych nie zaniza wyniku.
    num = row.pop("_tos_num", None)
    den = row.pop("_tos_den", None)
    row["tos_share"] = round(num / den, 2) if num is not None and den else None

    # Efekt halo: ile ze sprzedazy przypisanej tej pozycji dotyczy INNYCH
    # produktow niz reklamowany.
    #
    # UWAGA na pulapke, w ktora sam wpadlem: `sales` (sales7d) JUZ zawiera
    # halo - sprawdzone na danych, sales7d = attributedSalesSameSku7d +
    # salesOtherSku7d co do grosza. Dodanie halo do sales liczyloby je dwa
    # razy i zanizalo ACOS o polowe. ACOS pokazywany od poczatku byl poprawny.
    #
    # Nowa informacja to nie inny ACOS, tylko SKLAD tej sprzedazy: pozycja
    # moze wygladac slabo na wlasnym produkcie, a napedzac katalog. To zmienia
    # decyzje o wstrzymaniu, nie sam wskaznik.
    halo = row.get("sales_other_sku")
    if halo is None or not sales:
        row["halo_share"] = None
        row["acos_own"] = None
        row["sales_own"] = None
    else:
        halo = float(halo)
        wlasne = max(0.0, sales - halo)
        row["sales_own"] = round(wlasne, 2)
        row["halo_share"] = round(halo / sales * 100, 1)
        # ACOS liczony wylacznie z reklamowanego produktu - widok pesymistyczny,
        # przydatny przy decyzji "czy ta reklama broni sie sama"
        row["acos_own"] = round(cost / wlasne * 100, 2) if wlasne else None
    return row


_METRIC_COLS = ("impressions", "clicks", "cost", "orders", "sales", "units")


# Waluta, w ktorej podajemy sumy zbiorcze. EUR, bo w niej liczy szesc z
# dziewieciu rynkow i w niej EBC publikuje kursy.
WALUTA_RAPORTU = "EUR"


def kurs_dnia(model, profile_id: int | None = None):
    """Mnoznik przeliczajacy kwoty z waluty rynku na walute raportu.

    Przeliczamy TYLKO w widoku zbiorczym. Przy jednym rynku kwoty zostaja
    w jego walucie - tak samo pokazuje je Amazon i tak samo podpisuje je
    panel, a stawki i budzety i tak ustawia sie w walucie rynku.

    Kurs bierzemy z DNIA transakcji, nie dzisiejszy - raport za wrzesien ma
    wygladac tak samo w grudniu. Gdy kursu brak (nie pobrano, nowa waluta),
    zostaje 1.0: lepiej pokazac kwote nieprzeliczona niz zero.
    """
    if profile_id:
        return literal(1.0)
    return func.coalesce(FxRate.rate_eur, 1.0)


def _fx_join(stmt, model, profile_id: int | None = None):
    """Dolacza profil i kurs z dnia - potrzebne, gdy sumujemy kilka rynkow."""
    if profile_id:
        return stmt
    return (
        stmt
        .join(Profile, Profile.profile_id == model.profile_id, isouter=True)
        .join(
            FxRate,
            and_(FxRate.date == model.date, FxRate.currency == Profile.currency_code),
            isouter=True,
        )
    )


def _sums(model, kurs=None):
    """Sumy metryk. `kurs` przelicza kwoty - podaj go, gdy zapytanie moze
    objac kilka rynkow naraz, inaczej zsumujesz korony z euro."""
    k = kurs if kurs is not None else 1.0
    return [
        func.sum(model.impressions).label("impressions"),
        func.sum(model.clicks).label("clicks"),
        func.sum(model.cost * k).label("cost"),
        func.sum(model.orders).label("orders"),
        func.sum(model.sales * k).label("sales"),
        func.sum(model.units).label("units"),
        func.sum(model.sales_other_sku * k).label("sales_other_sku"),
        func.sum(model.units_other_sku).label("units_other_sku"),
        func.sum(model.tos_impression_share * model.impressions).label("_tos_num"),
        func.sum(
            case((model.tos_impression_share.is_(None), 0), else_=model.impressions)
        ).label("_tos_den"),
    ]


def _labels_for(session: Session, profile_id: int | None) -> dict[tuple[str, str], str]:
    """Slownik nazw kategorii i marek dla rynku tego profilu.

    Bez profilu (widok laczony) scalamy slowniki wszystkich rynkow. Kolizje sa
    mozliwe teoretycznie, ale identyfikatory Amazona sa globalnie unikalne.
    """
    marketplace = None
    if profile_id:
        marketplace = session.execute(
            select(Profile.marketplace_id).where(Profile.profile_id == profile_id)
        ).scalar()
        if not marketplace:
            return {}

    # Slownik jest wspoldzielony - nie modyfikujemy go w miejscu.
    return labels_etl.labels_for(session, marketplace)


def _window(model, profile_id: int | None, start: dt.date, end: dt.date):
    conds = [model.date >= start, model.date <= end]
    if profile_id:
        conds.append(model.profile_id == profile_id)
    return and_(*conds)


# --------------------------------------------------------------------------- #


def profiles(session: Session) -> list[dict[str, Any]]:
    rows = session.execute(select(Profile).order_by(Profile.country_code)).scalars().all()
    return [
        {
            "profile_id": p.profile_id,
            "country_code": p.country_code,
            "currency_code": p.currency_code,
            "account_name": p.account_name,
            "account_type": p.account_type,
            "marketplace_id": p.marketplace_id,
        }
        for p in rows
    ]


def _with_tacos(
    session: Session, row: dict[str, Any], profile_id: int | None,
    start: dt.date, end: dt.date,
) -> dict[str, Any]:
    """Dokłada TACOS, jesli znamy sprzedaz calkowita w tym okresie.

    TACOS = wydatek na reklame / CALY przychod, takze organiczny. Ads API nie
    zna sprzedazy organicznej, wiec mianownik pochodzi z importu raportu
    biznesowego. Gdy go nie ma, zwracamy None zamiast liczby - TACOS policzony
    z samej sprzedazy reklamowej rownalby sie ACOS i falszywie uspokajal.
    """
    dzienne = total_sales.daily_totals(session, profile_id, start, end)
    okres_dni = (end - start).days + 1

    row["tacos_days"] = len(dzienne)
    row["range_days"] = okres_dni

    if not dzienne:
        row["total_sales"] = None
        row["tacos"] = None
        row["troas"] = None
        row["ad_share"] = None
        return row

    # Licznik i mianownik musza pochodzic z TYCH SAMYCH dni. Przy imporcie
    # obejmujacym czesc okresu porownanie wydatku z pelnych siedmiu dni ze
    # sprzedaza z trzech zawyzalo TACOS niemal dwukrotnie - i nic tego nie
    # zdradzalo. Dlatego zawezamy oba do dni, dla ktorych mamy komplet.
    dni = list(dzienne)
    conds = [CampaignDaily.date.in_(dni)]
    if profile_id:
        conds.append(CampaignDaily.profile_id == profile_id)
    kurs = kurs_dnia(CampaignDaily, profile_id)
    koszt, sprzedaz_reklam = session.execute(
        _fx_join(
            select(func.sum(CampaignDaily.cost * kurs), func.sum(CampaignDaily.sales * kurs)),
            CampaignDaily, profile_id,
        ).where(*conds)
    ).one()

    calosc = round(sum(dzienne.values()), 2)
    koszt = float(koszt or 0.0)
    sprzedaz_reklam = float(sprzedaz_reklam or 0.0)

    row["total_sales"] = calosc
    row["tacos"] = round(koszt / calosc * 100, 2) if calosc else None
    # TROAS - caly przychod na kazdą złotówkę wydaną na reklamę. Ta sama para
    # liczb co TACOS, tylko odwrocona: TACOS mowi, ile procent obrotu zjada
    # reklama, TROAS ile obrotu z niej wraca. Liczymy z kosztu, nie z calosci,
    # bo dzielenie przez zero przy zerowym wydatku nie ma sensu - wtedy None.
    row["troas"] = round(calosc / koszt, 2) if koszt else None
    # Ile z calego przychodu pochodzi z reklam - druga strona tej samej monety
    row["ad_share"] = round(sprzedaz_reklam / calosc * 100, 2) if calosc else None
    # Wydatek i sprzedaz reklamowa z tych samych dni - do pokazania obok TACOS,
    # zeby dalo sie sprawdzic, z czego liczba powstala
    row["tacos_cost"] = round(koszt, 2)
    row["tacos_ad_sales"] = round(sprzedaz_reklam, 2)
    return row


def fba_share(
    session: Session, profile_id: int | None, start: dt.date, end: dt.date
) -> dict[str, Any]:
    """Udzial FBA w CALEJ sprzedazy - z raportu zamowien.

    Zrodlem jest `fulfillment_daily`, czyli sumy z raportu zamowien, ktory jako
    jedyny zna kanal realizacji. Kwoty z rynkow w roznych walutach przeliczamy
    po kursie z dnia - wczesniej pokazywalismy tylko najwieksza grupe walutowa,
    bo dodanie koron do euro zawyzalo wynik.
    """
    kurs = literal(1.0) if profile_id else func.coalesce(FxRate.rate_eur, 1.0)
    stmt = select(
        FulfillmentDaily.channel,
        func.sum(FulfillmentDaily.sales * kurs).label("sales"),
        func.count(func.distinct(FulfillmentDaily.profile_id)).label("markets"),
        func.max(FulfillmentDaily.currency).label("currency"),
    )
    if not profile_id:
        stmt = stmt.join(
            FxRate,
            and_(
                FxRate.date == FulfillmentDaily.date,
                FxRate.currency == FulfillmentDaily.currency,
            ),
            isouter=True,
        )
    rows = session.execute(
        stmt.where(_window(FulfillmentDaily, profile_id, start, end))
        .group_by(FulfillmentDaily.channel)
    ).all()

    if not rows:
        return _fba_share_z_reklam(session, profile_id, start, end)

    fba = wlasna = inne = 0.0
    rynkow = 0
    waluta = WALUTA_RAPORTU
    for kanal, sprzedaz, ile_rynkow, kod_waluty in rows:
        kwota = float(sprzedaz or 0.0)
        rynkow = max(rynkow, int(ile_rynkow or 0))
        if profile_id and kod_waluty:
            waluta = kod_waluty
        if (kanal or "").upper() in ("FBA", "AMAZON"):
            fba += kwota
        elif (kanal or "").upper() in ("MERCHANT", "SELLER"):
            wlasna += kwota
        else:
            inne += kwota

    razem = fba + wlasna + inne
    if not razem:
        return _fba_share_z_reklam(session, profile_id, start, end)

    return {
        "source": "orders",
        "currency": waluta,
        "markets": rynkow,
        "converted": profile_id is None,
        "fba_sales": round(fba, 2),
        "merchant_sales": round(wlasna, 2),
        "unknown_sales": round(inne, 2),
        "total": round(razem, 2),
        "fba_pct": round(fba / razem * 100, 1),
        "unknown_pct": round(inne / razem * 100, 1),
    }


def _fba_share_z_reklam(
    session: Session, profile_id: int | None, start: dt.date, end: dt.date
) -> dict[str, Any]:
    """Zapasowy udzial FBA - liczony ze sprzedazy przypisanej reklamom.

    Liczone ze sprzedazy REKLAMOWEJ per produkt, nie z calkowitej - i to nie
    jest wybor, tylko jedyna dostepna droga. Raport biznesowy Seller Central
    oddaje sekcje per ASIN bez dat, zagregowana po calym oknie, wiec przy oknie
    dluzszym niz dzien nie da sie jej uczciwie rozlozyc na dni i `sales_report`
    swiadomie ja pomija. Rozbicie calego przychodu na FBA wymagaloby pobierania
    dzien po dniu, czyli tylu raportow, ile dni razy rynkow.

    Sposob wysylki bierzemy z oferty, po parze (SKU, marketplace): to samo SKU
    bywa w FBA na jednym rynku i we wlasnej wysylce na drugim.
    """
    warunki = [ProductDaily.date.between(start, end)]
    if profile_id:
        warunki.append(ProductDaily.profile_id == profile_id)

    stmt = (
        select(
            func.sum(case((Listing.fulfillment.like("AMAZON%"), ProductDaily.sales), else_=0.0)),
            func.sum(case((Listing.fulfillment == "DEFAULT", ProductDaily.sales), else_=0.0)),
            func.sum(case((Listing.sku.is_(None), ProductDaily.sales), else_=0.0)),
            func.sum(ProductDaily.sales),
        )
        .select_from(ProductDaily)
        .join(Profile, Profile.profile_id == ProductDaily.profile_id, isouter=True)
        .join(
            Listing,
            (Listing.sku == ProductDaily.sku)
            & (Listing.marketplace_id == Profile.marketplace_id),
            isouter=True,
        )
        .where(*warunki)
    )
    fba, wlasna, nieznane, razem = (float(x or 0.0) for x in session.execute(stmt).one())

    # Produkty bez dopasowanej oferty trzymamy osobno zamiast doliczac do
    # ktorejkolwiek strony. Doliczone cicho zanizalyby albo zawyzaly udzial,
    # a tak widac wprost, ile sprzedazy nie dalo sie zaklasyfikowac.
    znane = fba + wlasna
    return {
        "source": "ads",
        "currency": None,
        "markets": 0,
        "currencies": 0,
        "fba_sales": round(fba, 2),
        "merchant_sales": round(wlasna, 2),
        "unknown_sales": round(nieznane, 2),
        "total": round(razem, 2),
        "fba_pct": round(fba / znane * 100, 1) if znane else None,
        # Udzial sprzedazy, ktorej nie przypisano do zadnej formy wysylki -
        # przy wysokiej wartosci sam procent FBA robi sie niepewny.
        "unknown_pct": round(nieznane / razem * 100, 1) if razem else None,
    }


def kpis(session: Session, profile_id: int | None, start: dt.date, end: dt.date) -> dict[str, Any]:
    """Sumy dla okresu + porownanie z poprzednim okresem tej samej dlugosci."""
    span = (end - start).days + 1
    prev_end = start - dt.timedelta(days=1)
    prev_start = prev_end - dt.timedelta(days=span - 1)

    def agg(s: dt.date, e: dt.date) -> dict[str, Any]:
        row = session.execute(
            _fx_join(
                select(*_sums(CampaignDaily, kurs_dnia(CampaignDaily, profile_id))),
                CampaignDaily, profile_id,
            ).where(_window(CampaignDaily, profile_id, s, e))
        ).mappings().one()
        base: dict[str, Any] = {k: (row[k] or 0) for k in _METRIC_COLS}
        base["_tos_num"] = row.get("_tos_num")
        base["_tos_den"] = row.get("_tos_den")
        return _derive(base)

    current = _with_tacos(session, agg(start, end), profile_id, start, end)
    previous = _with_tacos(session, agg(prev_start, prev_end), profile_id, prev_start, prev_end)

    deltas = {}
    for key in ("impressions", "clicks", "cost", "orders", "sales", "acos", "roas", "cpc",
                "tacos", "troas", "total_sales"):
        now, before = current.get(key), previous.get(key)
        if now is None or before in (None, 0):
            deltas[key] = None
        else:
            deltas[key] = round((now - before) / before * 100, 1)

    # Sprzedaz calkowita i TACOS porownujemy tylko wtedy, gdy oba okresy maja
    # komplet dni. Import siega wstecz tylko tam, gdzie ktos go dociagnal, wiec
    # okres wczesniejszy bywa pokryty czesciowo - i wtedy "wzrost o 500%" mowi
    # o zasiegu importu, nie o sprzedazy. Lepiej nie pokazac nic niz liczbe,
    # ktora wyglada na wynik.
    pelny_teraz = current.get("tacos_days") == span
    pelny_wczesniej = previous.get("tacos_days") == span
    if not (pelny_teraz and pelny_wczesniej):
        deltas["total_sales"] = None
        deltas["tacos"] = None
        # TROAS stoi na tej samej sprzedazy calkowitej, wiec przy niepelnym
        # pokryciu jest tak samo mylacy jak TACOS.
        deltas["troas"] = None

    return {
        "range": {"start": start.isoformat(), "end": end.isoformat(), "days": span},
        "current": current,
        "previous": previous,
        "delta_pct": deltas,
    }


def timeseries(
    session: Session, profile_id: int | None, start: dt.date, end: dt.date
) -> list[dict[str, Any]]:
    rows = session.execute(
        _fx_join(
            select(
                CampaignDaily.date,
                *_sums(CampaignDaily, kurs_dnia(CampaignDaily, profile_id)),
            ),
            CampaignDaily, profile_id,
        )
        .where(_window(CampaignDaily, profile_id, start, end))
        .group_by(CampaignDaily.date)
        .order_by(CampaignDaily.date)
    ).mappings().all()

    out = []
    for r in rows:
        item = {"date": r["date"].isoformat(), **{k: (r[k] or 0) for k in _METRIC_COLS}}
        out.append(_derive(item))
    return out


def campaigns(
    session: Session,
    profile_id: int | None,
    start: dt.date,
    end: dt.date,
    state: str | None = None,
    search: str | None = None,
    limit: int = 500,
    portfolio_id: int | None = None,
    include_archived: bool = False,
    sensitivity: float = 1.0,
) -> list[dict[str, Any]]:
    """Lista kampanii z metrykami za okres.

    Zapytanie wychodzi OD kampanii, a metryki dolacza lewym zlaczeniem z warunkiem
    dat w klauzuli ON. Gdyby zakres dat trafil do WHERE, kampania bez wydatku w tym
    okresie wypadlaby z wyniku - a wlasnie wstrzymane kampanie zwykle nic nie wydaja
    i to one najbardziej wymagaja bycia widocznymi.
    """
    join_window = [
        CampaignDaily.campaign_id == Campaign.campaign_id,
        CampaignDaily.date >= start,
        CampaignDaily.date <= end,
    ]

    stmt = (
        select(
            Campaign.campaign_id,
            Campaign.name.label("name"),
            Campaign.state.label("state"),
            Campaign.targeting_type.label("targeting_type"),
            # Typ produktu reklamowego: SP i SB leza w tej samej tabeli,
            # a roznia sie na tyle, ze widok musi je odroznic.
            Campaign.ad_product.label("ad_product"),
            Campaign.budget_amount.label("budget"),
            Campaign.bidding_strategy.label("bidding_strategy"),
            Campaign.portfolio_id.label("portfolio_id"),
            Portfolio.name.label("portfolio_name"),
            Profile.country_code.label("country"),
            Profile.currency_code.label("currency"),
            *_sums(CampaignDaily),
        )
        .select_from(Campaign)
        .join(CampaignDaily, and_(*join_window), isouter=True)
        .join(
            Portfolio,
            (Portfolio.portfolio_id == Campaign.portfolio_id)
            & (Portfolio.profile_id == Campaign.profile_id),
            isouter=True,
        )
        .join(Profile, Profile.profile_id == Campaign.profile_id, isouter=True)
        .group_by(
            Campaign.campaign_id, Campaign.name, Campaign.state, Campaign.targeting_type,
            Campaign.ad_product,
            Campaign.budget_amount, Campaign.bidding_strategy, Campaign.portfolio_id,
            Portfolio.name, Profile.country_code, Profile.currency_code,
        )
        .order_by(func.sum(CampaignDaily.cost).desc().nullslast())
        .limit(limit)
    )

    if profile_id:
        stmt = stmt.where(Campaign.profile_id == profile_id)

    # Kampanie typu, dla ktorego nie mamy jeszcze zadnych metryk, pokazywalyby
    # sie z zerami - a to nieprawda: nie "nic nie wydaly", tylko nie znamy ich
    # wynikow. Do czasu pobrania raportow danego typu trzymamy je poza widokiem.
    typy_z_danymi = {
        t for t, in session.execute(
            select(CampaignDaily.ad_product).distinct()
        ).all() if t
    }
    if typy_z_danymi:
        stmt = stmt.where(Campaign.ad_product.in_(list(typy_z_danymi)))

    if state:
        stmt = stmt.where(Campaign.state == state.upper())
    elif not include_archived:
        # Archiwum jest nieedytowalne - domyslnie poza widokiem, ale wstrzymane zostaja
        stmt = stmt.where(Campaign.state != "ARCHIVED")
    if search:
        stmt = stmt.where(Campaign.name.ilike(f"%{search}%"))
    if portfolio_id is not None:
        stmt = stmt.where(
            Campaign.portfolio_id.is_(None) if portfolio_id == 0
            else Campaign.portfolio_id == portfolio_id
        )

    out = []
    for r in session.execute(stmt).mappings().all():
        row = dict(r)
        # Lewe zlaczenie daje NULL tam, gdzie nie bylo wydatku - zerujemy,
        # zeby tabela pokazywala 0 zamiast pustych komorek
        for col in _METRIC_COLS:
            row[col] = row.get(col) or 0
        out.append(_derive(row))

    # Punkt odniesienia liczymy z tego samego zbioru, ktory oceniamy - dzieki
    # temu sugestie sa wzgledne wobec realnych wynikow konta, a nie sztywnych progow
    ctx = advice.build_context(out, (end - start).days + 1, sensitivity)
    for row in out:
        row["suggestions"] = advice.suggest_campaign(row, ctx)
    return out


def targets(
    session: Session,
    profile_id: int | None,
    start: dt.date,
    end: dt.date,
    campaign_id: str | None = None,
    kind: str | None = None,
    min_clicks: int = 0,
    limit: int = 1000,
    sensitivity: float = 1.0,
) -> list[dict[str, Any]]:
    stmt = (
        select(
            TargetDaily.target_id,
            TargetDaily.target_kind,
            TargetDaily.ad_product,
            func.max(TargetDaily.target_text).label("target_text"),
            func.max(TargetDaily.match_type).label("match_type"),
            TargetDaily.campaign_id,
            TargetDaily.ad_group_id,
            Campaign.name.label("campaign_name"),
            AdGroup.name.label("ad_group_name"),
            func.coalesce(Keyword.bid, Target.bid, AdGroup.default_bid).label("bid"),
            func.coalesce(Keyword.state, Target.state).label("state"),
            # Postgres nie ma MAX() dla json/jsonb - agregujemy jako tekst.
            # describe_expression() w targeting_names.py i tak robi json.loads()
            # gdy dostaje string, wiec dalsze przetwarzanie dziala bez zmian.
            func.max(cast(Target.expression, Text)).label("expression"),
            # Waluta rynku. Bez niej warstwa sugestii wpada w zapasowe "EUR"
            # i przy koncie GBP pisze "Podnies stawke do 0,23 EUR" obok kwoty
            # 0,23 GBP - ta sama liczba w dwoch walutach, czyli komunikat,
            # ktoremu nie sposob ufac.
            Profile.currency_code.label("currency"),
            *_sums(TargetDaily),
        )
        .join(Campaign, Campaign.campaign_id == TargetDaily.campaign_id, isouter=True)
        .join(AdGroup, AdGroup.ad_group_id == TargetDaily.ad_group_id, isouter=True)
        .join(Keyword, Keyword.keyword_id == TargetDaily.target_id, isouter=True)
        .join(Target, Target.target_id == TargetDaily.target_id, isouter=True)
        .join(Profile, Profile.profile_id == TargetDaily.profile_id, isouter=True)
        .where(_window(TargetDaily, profile_id, start, end))
        .group_by(
            TargetDaily.target_id, TargetDaily.target_kind, TargetDaily.ad_product,
            TargetDaily.campaign_id,
            TargetDaily.ad_group_id, Campaign.name, AdGroup.name,
            Keyword.bid, Target.bid, AdGroup.default_bid, Keyword.state, Target.state,
            Profile.currency_code,
        )
        .order_by(func.sum(TargetDaily.cost).desc())
        .limit(limit)
    )
    if campaign_id:
        stmt = stmt.where(TargetDaily.campaign_id == campaign_id)
    if kind:
        # Dla SP filtrujemy po match_type, bo zapisana kolumna bywa nieaktualna
        # w danych sprzed poprawki klasyfikacji. Dla SB i SD ufamy kolumnie -
        # SD nie ma match_type, wiec warunek na nim wykluczylby wszystko.
        expr_types = [targeting_names.AUTO_MATCH, targeting_names.PRODUCT_MATCH]
        sp = TargetDaily.ad_product == "SP"
        po_match = TargetDaily.match_type.in_(expr_types)
        po_kolumnie = TargetDaily.target_kind == kind.upper()
        stmt = stmt.where(
            or_(
                and_(sp, po_match if kind.upper() == "TARGET" else ~po_match),
                and_(~sp, po_kolumnie),
            )
        )
    if min_clicks:
        stmt = stmt.having(func.sum(TargetDaily.clicks) >= min_clicks)

    labels = _labels_for(session, profile_id)

    out = []
    for r in session.execute(stmt).mappings().all():
        row = _derive(dict(r))
        # Rodzaj wyliczamy z match_type **tylko dla Sponsored Products** -
        # tam zapisana kolumna bywa nieaktualna w danych sprzed poprawki
        # klasyfikacji. SB i SD zapisuja rodzaj poprawnie od poczatku, a SD
        # nie ma match_type wcale, wiec wyliczanie robilo z kazdego targetu
        # "keyword" i sugestia szla potem na niewlasciwy zasob Amazona.
        if (row.get("ad_product") or "SP") == "SP":
            row["target_kind"] = targeting_names.target_kind(row.get("match_type"))
        row["target_text"] = targeting_names.display_name(
            row.get("match_type"), row.get("target_text"),
            row.pop("expression", None), labels,
        )
        row["match_label"] = targeting_names.match_label(row.get("match_type"))
        out.append(row)

    ctx = advice.build_context(out, (end - start).days + 1, sensitivity)
    for row in out:
        row["suggestions"] = advice.suggest_target(row, ctx)
    return out


def search_terms(
    session: Session,
    profile_id: int | None,
    start: dt.date,
    end: dt.date,
    campaign_id: str | None = None,
    min_clicks: int = 0,
    min_orders: int | None = None,
    max_orders: int | None = None,
    search: str | None = None,
    limit: int = 1000,
    sensitivity: float = 1.0,
) -> list[dict[str, Any]]:
    stmt = (
        select(
            SearchTermDaily.search_term,
            SearchTermDaily.campaign_id,
            SearchTermDaily.ad_group_id,
            SearchTermDaily.target_id,
            func.max(SearchTermDaily.target_text).label("target_text"),
            func.max(SearchTermDaily.match_type).label("match_type"),
            Campaign.name.label("campaign_name"),
            AdGroup.name.label("ad_group_name"),
            # Waluta rynku - patrz komentarz przy `targets`.
            Profile.currency_code.label("currency"),
            *_sums(SearchTermDaily),
        )
        .join(Campaign, Campaign.campaign_id == SearchTermDaily.campaign_id, isouter=True)
        .join(AdGroup, AdGroup.ad_group_id == SearchTermDaily.ad_group_id, isouter=True)
        .join(Profile, Profile.profile_id == SearchTermDaily.profile_id, isouter=True)
        .where(_window(SearchTermDaily, profile_id, start, end))
        .group_by(
            SearchTermDaily.search_term, SearchTermDaily.campaign_id,
            SearchTermDaily.ad_group_id, SearchTermDaily.target_id,
            Campaign.name, AdGroup.name, Profile.currency_code,
        )
        .order_by(func.sum(SearchTermDaily.cost).desc())
        .limit(limit)
    )
    if campaign_id:
        stmt = stmt.where(SearchTermDaily.campaign_id == campaign_id)
    if search:
        stmt = stmt.where(SearchTermDaily.search_term.ilike(f"%{search}%"))
    if min_clicks:
        stmt = stmt.having(func.sum(SearchTermDaily.clicks) >= min_clicks)
    if min_orders is not None:
        stmt = stmt.having(func.sum(SearchTermDaily.orders) >= min_orders)
    if max_orders is not None:
        stmt = stmt.having(func.sum(SearchTermDaily.orders) <= max_orders)

    out = [_derive(dict(r)) for r in session.execute(stmt).mappings().all()]
    _attach_catalog_po_tekscie(session, profile_id, out, "search_term")
    ctx = advice.build_context(out, (end - start).days + 1, sensitivity)
    for row in out:
        row["suggestions"] = advice.suggest_search_term(row, ctx)
    return out


def placements(
    session: Session, profile_id: int | None, start: dt.date, end: dt.date,
    campaign_id: str | None = None,
) -> list[dict[str, Any]]:
    stmt = (
        _fx_join(
            select(
                PlacementDaily.placement,
                *_sums(PlacementDaily, kurs_dnia(PlacementDaily, profile_id)),
            ),
            PlacementDaily, profile_id,
        )
        .where(_window(PlacementDaily, profile_id, start, end))
        .group_by(PlacementDaily.placement)
        .order_by(func.sum(PlacementDaily.cost * kurs_dnia(PlacementDaily, profile_id)).desc())
    )
    if campaign_id:
        stmt = stmt.where(PlacementDaily.campaign_id == campaign_id)
    return [_derive(dict(r)) for r in session.execute(stmt).mappings().all()]


def campaign_detail(
    session: Session, campaign_id: str, start: dt.date, end: dt.date,
) -> dict[str, Any] | None:
    """Naglowek widoku kampanii: konfiguracja + metryki za okres."""
    row = session.execute(
        select(
            Campaign.campaign_id,
            Campaign.name,
            Campaign.state,
            Campaign.targeting_type,
            Campaign.budget_amount.label("budget"),
            Campaign.budget_type,
            Campaign.bidding_strategy,
            Campaign.placement_bidding,
            Campaign.start_date,
            Campaign.end_date,
            Campaign.profile_id,
            Campaign.portfolio_id,
            Portfolio.name.label("portfolio_name"),
            Profile.country_code.label("country"),
            Profile.currency_code.label("currency"),
        )
        .select_from(Campaign)
        .join(
            Portfolio,
            (Portfolio.portfolio_id == Campaign.portfolio_id)
            & (Portfolio.profile_id == Campaign.profile_id),
            isouter=True,
        )
        .join(Profile, Profile.profile_id == Campaign.profile_id, isouter=True)
        .where(Campaign.campaign_id == campaign_id)
    ).mappings().first()

    if not row:
        return None

    metrics = session.execute(
        select(*_sums(CampaignDaily)).where(
            CampaignDaily.campaign_id == campaign_id,
            CampaignDaily.date >= start,
            CampaignDaily.date <= end,
        )
    ).mappings().one()

    out = dict(row)
    out.update({k: (metrics[k] or 0) for k in _METRIC_COLS})
    out["start_date"] = out["start_date"].isoformat() if out.get("start_date") else None
    out["end_date"] = out["end_date"].isoformat() if out.get("end_date") else None
    return _derive(out)


def ad_groups(
    session: Session, profile_id: int | None, start: dt.date, end: dt.date,
    campaign_id: str | None = None, limit: int = 2000,
) -> list[dict[str, Any]]:
    """Grupy reklam z metrykami.

    Metryki sumujemy z raportu targetowania - nie ma osobnego raportu na poziomie
    grupy, ale kazdy wiersz targetowania niesie ad_group_id, wiec suma po grupie
    daje ten sam wynik. Grupy bez ruchu pokazujemy z zerem, dlatego lewe zlaczenie.
    """
    stmt = (
        select(
            AdGroup.ad_group_id,
            AdGroup.name,
            AdGroup.state,
            AdGroup.default_bid,
            AdGroup.campaign_id,
            Campaign.name.label("campaign_name"),
            *_sums(TargetDaily),
        )
        .select_from(AdGroup)
        .join(
            TargetDaily,
            and_(
                TargetDaily.ad_group_id == AdGroup.ad_group_id,
                TargetDaily.date >= start,
                TargetDaily.date <= end,
            ),
            isouter=True,
        )
        .join(Campaign, Campaign.campaign_id == AdGroup.campaign_id, isouter=True)
        .where(AdGroup.state != "ARCHIVED")
        .group_by(
            AdGroup.ad_group_id, AdGroup.name, AdGroup.state,
            AdGroup.default_bid, AdGroup.campaign_id, Campaign.name,
        )
        .order_by(func.sum(TargetDaily.cost).desc().nullslast())
        .limit(limit)
    )
    if profile_id:
        stmt = stmt.where(AdGroup.profile_id == profile_id)
    if campaign_id:
        stmt = stmt.where(AdGroup.campaign_id == campaign_id)

    out = []
    for r in session.execute(stmt).mappings().all():
        row = dict(r)
        for col in _METRIC_COLS:
            row[col] = row.get(col) or 0
        out.append(_derive(row))
    return out


# Amazon Business to placement, ktory da sie **ustawic**, ale ktorego Amazon
# nie raportuje osobno: raport placementow grupuje po `placementClassification`
# i przez 19,5 tys. wierszy zwrocil wylacznie trzy wartosci. Modyfikator wiec
# pokazujemy i pozwalamy zmieniac, a metryki zostawiamy puste - zamiast
# udawac, ze ich nie ma, bo jest zero.
PLACEMENT_B2B = "SITE_AMAZON_BUSINESS"


def _dolacz_b2b(
    session: Session, profile_id: int | None,
    out: list[dict[str, Any]], campaign_id: str | None,
) -> None:
    """Dokłada wiersz Amazon Business dla kampanii obecnych w wyniku.

    Bez tego modyfikator B2B byl niewidoczny w panelu, choc na tym koncie
    ustawiony w 23 kampaniach. Gorzej: zapis wysyla liste placementow, wiec
    pominiecie B2B oznaczalo poleganie na tym, ze Amazon scali ja z istniejaca
    konfiguracja. Scala - sprawdzone - ale to zachowanie nieudokumentowane,
    a wiersz w komplecie usuwa ten problem u zrodla.
    """
    kampanie = {r["campaign_id"] for r in out}
    if campaign_id:
        kampanie.add(str(campaign_id))
    if not kampanie:
        return

    stmt = select(
        Campaign.campaign_id, Campaign.name, Campaign.state,
        Campaign.placement_bidding, Profile.country_code, Profile.currency_code,
    ).join(
        Profile, Profile.profile_id == Campaign.profile_id, isouter=True
    ).where(Campaign.campaign_id.in_(list(kampanie)))

    for cid, nazwa, stan, bidding, kraj, waluta in session.execute(stmt).all():
        mnoznik = next(
            (
                float(b.get("percentage", 0))
                for b in (bidding or [])
                if b.get("placement") == PLACEMENT_B2B
            ),
            0.0,
        )
        # Kampania bez ustawionego mnoznika nie ma tu czego pokazac: metryk
        # Amazon nie raportuje, a modyfikator jest zerowy. Na liscie wszystkich
        # kampanii dawalo to 50 pustych wierszy na 76 - wiecej szumu niz tresci.
        # W widoku jednej kampanii wiersz zostaje zawsze, bo to tam sie go
        # ustawia i bez niego nie byloby jak dodac mnoznika tam, gdzie go nie ma.
        if not mnoznik and not campaign_id:
            continue
        out.append({
            "campaign_id": cid,
            "campaign_name": nazwa,
            "campaign_state": stan,
            "placement": PLACEMENT_B2B,
            "country": kraj,
            "currency": waluta,
            "modifier": mnoznik,
            # Amazon nie raportuje tego placementu osobno - puste, nie zero.
            "no_report": True,
            **{k: None for k in _METRIC_COLS},
            "ctr": None, "cpc": None, "cvr": None,
            "acos": None, "roas": None, "cpa": None, "tos_share": None,
            "suggestions": [],
        })


def placements_by_campaign(
    session: Session, profile_id: int | None, start: dt.date, end: dt.date,
    campaign_id: str | None = None, limit: int = 2000,
    sensitivity: float = 1.0,
) -> list[dict[str, Any]]:
    """Placementy w rozbiciu na kampanie - z modyfikatorem stawki ustawionym w Amazonie."""
    # `placement_bidding` jest typu json (nie jsonb) - Postgres nie ma dla
    # json zadnego operatora porownania, wiec surowa kolumna w GROUP BY
    # konczy sie "nie mozna okreslic operatora porownujacego dla typu json".
    # Ten sam obiekt w select() i group_by() (patrz komentarz przy
    # portfolio_id_expr wyzej) - inaczej dwa oddzielne rzutowania tworza dwa
    # bind-parametry i Postgres znow widzi rozne wyrazenia.
    placement_bidding_expr = cast(Campaign.placement_bidding, Text)
    stmt = (
        select(
            PlacementDaily.campaign_id,
            PlacementDaily.placement,
            Campaign.name.label("campaign_name"),
            Campaign.state.label("campaign_state"),
            placement_bidding_expr.label("placement_bidding"),
            Profile.country_code.label("country"),
            Profile.currency_code.label("currency"),
            *_sums(PlacementDaily),
        )
        .join(Campaign, Campaign.campaign_id == PlacementDaily.campaign_id, isouter=True)
        .join(Profile, Profile.profile_id == PlacementDaily.profile_id, isouter=True)
        .where(_window(PlacementDaily, profile_id, start, end))
        .group_by(
            PlacementDaily.campaign_id, PlacementDaily.placement, Campaign.name,
            Campaign.state, placement_bidding_expr,
            Profile.country_code, Profile.currency_code,
        )
        .order_by(func.sum(PlacementDaily.cost).desc())
        .limit(limit)
    )
    if campaign_id:
        stmt = stmt.where(PlacementDaily.campaign_id == campaign_id)

    out = []
    for r in session.execute(stmt).mappings().all():
        row = _derive(dict(r))
        # Modyfikator siedzi w konfiguracji kampanii jako lista per placement.
        # Kolumna wraca teraz jako tekst (rzutowanie wyzej), stad jawny
        # json.loads zamiast polegania na automatycznym dekodowaniu jsonb.
        raw_bidding = row.pop("placement_bidding", None)
        bidding = json.loads(raw_bidding) if raw_bidding else []
        row["modifier"] = next(
            (
                float(b.get("percentage", 0))
                for b in bidding
                if b.get("placement") == row["placement"]
            ),
            0.0,
        )
        out.append(row)

    _dolacz_b2b(session, profile_id, out, campaign_id)

    ctx = advice.build_context(out, (end - start).days + 1, sensitivity)
    best = min((r["acos"] for r in out if r.get("acos") is not None), default=None)
    for row in out:
        row["suggestions"] = advice.suggest_placement(row, ctx, best)
    return out


def negative_keywords(
    session: Session, profile_id: int | None, search: str | None = None, limit: int = 5000,
    campaign_id: str | None = None,
) -> list[dict[str, Any]]:
    """Istniejace wykluczenia slowne - to, co w panelu Amazona jest pod negative keywords."""
    stmt = (
        select(
            NegativeKeyword.keyword_id,
            NegativeKeyword.keyword_text,
            NegativeKeyword.match_type,
            NegativeKeyword.level,
            NegativeKeyword.state,
            NegativeKeyword.campaign_id,
            NegativeKeyword.ad_group_id,
            Campaign.name.label("campaign_name"),
            AdGroup.name.label("ad_group_name"),
            Profile.country_code.label("country"),
        )
        .join(Campaign, Campaign.campaign_id == NegativeKeyword.campaign_id, isouter=True)
        .join(AdGroup, AdGroup.ad_group_id == NegativeKeyword.ad_group_id, isouter=True)
        .join(Profile, Profile.profile_id == NegativeKeyword.profile_id, isouter=True)
        .order_by(NegativeKeyword.keyword_text)
        .limit(limit)
    )
    if profile_id:
        stmt = stmt.where(NegativeKeyword.profile_id == profile_id)
    if campaign_id:
        stmt = stmt.where(NegativeKeyword.campaign_id == campaign_id)
    if search:
        stmt = stmt.where(NegativeKeyword.keyword_text.ilike(f"%{search}%"))
    return [dict(r) for r in session.execute(stmt).mappings().all()]


def _opis_wykluczenia(row: dict[str, Any], labels) -> str | None:
    """Czytelny opis wykluczenia produktowego z pary (typ, wartosc)."""
    typ = (row.get("expression_type") or "").upper()
    wartosc = (row.get("value") or "").strip()
    if not typ or typ == "MANUAL":
        return None
    # Wartosc zaczyna sie od typu - odcinamy go, zostaje sam identyfikator
    if wartosc.upper().startswith(typ):
        wartosc = wartosc[len(typ):].strip()
    return targeting_names._describe_condition({"type": typ, "value": wartosc}, labels)


def negative_targets(
    session: Session, profile_id: int | None, search: str | None = None, limit: int = 5000,
    campaign_id: str | None = None,
) -> list[dict[str, Any]]:
    """Wykluczone ASIN-y i marki - odpowiednik zakladki negative targeting."""
    stmt = (
        select(
            NegativeTarget.target_id,
            NegativeTarget.value,
            NegativeTarget.expression_type,
            NegativeTarget.level,
            NegativeTarget.state,
            NegativeTarget.campaign_id,
            NegativeTarget.ad_group_id,
            Campaign.name.label("campaign_name"),
            AdGroup.name.label("ad_group_name"),
            Profile.country_code.label("country"),
        )
        .join(Campaign, Campaign.campaign_id == NegativeTarget.campaign_id, isouter=True)
        .join(AdGroup, AdGroup.ad_group_id == NegativeTarget.ad_group_id, isouter=True)
        .join(Profile, Profile.profile_id == NegativeTarget.profile_id, isouter=True)
        .order_by(NegativeTarget.value)
        .limit(limit)
    )
    if profile_id:
        stmt = stmt.where(NegativeTarget.profile_id == profile_id)
    if campaign_id:
        stmt = stmt.where(NegativeTarget.campaign_id == campaign_id)
    if search:
        stmt = stmt.where(NegativeTarget.value.ilike(f"%{search}%"))

    labels = _labels_for(session, profile_id)
    out = []
    for r in session.execute(stmt).mappings().all():
        row = dict(r)
        # `value` trzymamy jako "TYP identyfikator" - to jedyne, co mamy pod
        # reka, bo surowej odpowiedzi Amazona ta kwerenda nie pobiera.
        # Rozwijamy to w czytelny opis: "Marka Quest" zamiast
        # "ASIN_BRAND_SAME_AS 16404114031".
        row["label"] = _opis_wykluczenia(row, labels) or row.get("value")
        out.append(row)
    # Wykluczony ASIN bez zdjecia nic nie mowi - a to na jego podstawie
    # ocenia sie, czy wykluczenie bylo trafne.
    _attach_catalog_po_tekscie(session, profile_id, out, "value")
    return out


def products(
    session: Session, profile_id: int | None, start: dt.date, end: dt.date, limit: int = 500
) -> list[dict[str, Any]]:
    stmt = (
        select(
            ProductDaily.sku,
            func.max(ProductDaily.asin).label("asin"),
            Profile.country_code.label("country"),
            Profile.currency_code.label("currency"),
            *_sums(ProductDaily),
        )
        .join(Profile, Profile.profile_id == ProductDaily.profile_id, isouter=True)
        .where(_window(ProductDaily, profile_id, start, end))
        .group_by(ProductDaily.sku, Profile.country_code, Profile.currency_code)
        .order_by(func.sum(ProductDaily.cost).desc())
        .limit(limit)
    )
    rows = [_derive(dict(r)) for r in session.execute(stmt).mappings().all()]

    # TACOS per produkt. Na poziomie rynku widac, czy ogolnie przeplacamy;
    # dopiero tutaj widac, KTORY produkt. Laczymy po ASIN, bo raport biznesowy
    # rozlicza sie wlasnie po nim - SKU bywa w nim nieobecny.
    _attach_catalog(session, profile_id, rows)
    po_asin = total_sales.sales_by_asin(session, profile_id, start, end)
    for r in rows:
        calosc = po_asin.get((r.get("asin") or "").upper())
        r["total_sales"] = round(calosc, 2) if calosc else None
        r["tacos"] = (
            round((r.get("cost") or 0.0) / calosc * 100, 2) if calosc else None
        )
        r["ad_share"] = (
            round((r.get("sales") or 0.0) / calosc * 100, 2) if calosc else None
        )
    return rows


def markets_overview(session: Session, start: dt.date, end: dt.date) -> list[dict[str, Any]]:
    """Porownanie rynkow DE / ES / IT obok siebie."""
    stmt = (
        select(
            Profile.profile_id,
            Profile.country_code.label("country"),
            Profile.currency_code.label("currency"),
            Profile.account_name.label("account_name"),
            *_sums(CampaignDaily),
        )
        .join(Profile, Profile.profile_id == CampaignDaily.profile_id)
        .where(and_(CampaignDaily.date >= start, CampaignDaily.date <= end))
        .group_by(Profile.profile_id, Profile.country_code, Profile.currency_code,
                  Profile.account_name)
        .order_by(func.sum(CampaignDaily.cost).desc())
    )
    return [
        _with_tacos(session, _derive(dict(r)), r["profile_id"], start, end)
        for r in session.execute(stmt).mappings().all()
    ]


def portfolios(
    session: Session, profile_id: int | None, start: dt.date, end: dt.date,
) -> list[dict[str, Any]]:
    """Wyniki zgrupowane po portfoliach - tak, jak widac je w panelu Amazona.

    Jak przy kampaniach: wychodzimy od encji, metryki dolaczamy warunkowo.
    Portfolio, ktorego kampanie nic nie wydaly w okresie, ma sie pokazac z zerem,
    a nie zniknac. Kampanie bez portfolio trafiaja do osobnej pozycji, zeby sumy
    zgadzaly sie z przegladem.
    """
    # Ten sam obiekt wyrazenia w select() i group_by(): func.coalesce(..., 0)
    # wywolane osobno w kazdym miejscu tworzy dwa rozne bind-parametry dla
    # literalu 0, wiec Postgres widzi w SELECT i GROUP BY teksowo rozne
    # wyrazenia i odmawia zapytania GroupingError-em.
    portfolio_id_expr = func.coalesce(Campaign.portfolio_id, 0)
    stmt = (
        select(
            portfolio_id_expr.label("portfolio_id"),
            func.max(Portfolio.name).label("name"),
            func.max(Portfolio.state).label("state"),
            func.max(Portfolio.budget_amount).label("budget"),
            func.max(Portfolio.budget_policy).label("budget_policy"),
            func.max(Profile.country_code).label("country"),
            func.max(Profile.currency_code).label("currency"),
            func.count(func.distinct(Campaign.campaign_id)).label("campaigns"),
            *_sums(CampaignDaily),
        )
        .select_from(Campaign)
        .join(
            CampaignDaily,
            and_(
                CampaignDaily.campaign_id == Campaign.campaign_id,
                CampaignDaily.date >= start,
                CampaignDaily.date <= end,
            ),
            isouter=True,
        )
        .join(
            Portfolio,
            (Portfolio.portfolio_id == Campaign.portfolio_id)
            & (Portfolio.profile_id == Campaign.profile_id),
            isouter=True,
        )
        .join(Profile, Profile.profile_id == Campaign.profile_id, isouter=True)
        .where(Campaign.state != "ARCHIVED")
        .group_by(portfolio_id_expr)
        .order_by(func.sum(CampaignDaily.cost).desc().nullslast())
    )
    if profile_id:
        stmt = stmt.where(Campaign.profile_id == profile_id)

    out = []
    for r in session.execute(stmt).mappings().all():
        row = dict(r)
        for col in _METRIC_COLS:
            row[col] = row.get(col) or 0
        row = _derive(row)
        if not row["name"]:
            row["name"] = "Bez portfolio"
            row["state"] = None
        out.append(row)
    return out


def product_ads(
    session: Session, profile_id: int | None, start: dt.date, end: dt.date,
    campaign_id: str | None = None, limit: int = 5000,
    sensitivity: float = 1.0,
) -> list[dict[str, Any]]:
    """Produkty w rozbiciu na grupy reklam - z ad_id, ktore pozwala je wstrzymac.

    Ekran Produkty sumuje po SKU przez wszystkie kampanie, co jest dobre do
    ogladania. Do dzialania trzeba wiedziec, KTORA reklame wstrzymac - ten sam
    SKU bywa reklamowany w kilku grupach i w jednej moze sprzedawac, a w innej nie.
    """
    stmt = (
        select(
            ProductAd.ad_id,
            ProductAd.sku,
            ProductAd.asin,
            ProductAd.state,
            ProductAd.campaign_id,
            ProductAd.ad_group_id,
            Campaign.name.label("campaign_name"),
            AdGroup.name.label("ad_group_name"),
            Profile.country_code.label("country"),
            Profile.currency_code.label("currency"),
            *_sums(ProductDaily),
        )
        .select_from(ProductAd)
        .join(
            ProductDaily,
            and_(
                ProductDaily.sku == ProductAd.sku,
                ProductDaily.ad_group_id == ProductAd.ad_group_id,
                ProductDaily.date >= start,
                ProductDaily.date <= end,
            ),
            isouter=True,
        )
        .join(Campaign, Campaign.campaign_id == ProductAd.campaign_id, isouter=True)
        .join(AdGroup, AdGroup.ad_group_id == ProductAd.ad_group_id, isouter=True)
        .join(Profile, Profile.profile_id == ProductAd.profile_id, isouter=True)
        .where(ProductAd.state != "ARCHIVED")
        .group_by(
            ProductAd.ad_id, ProductAd.sku, ProductAd.asin, ProductAd.state,
            ProductAd.campaign_id, ProductAd.ad_group_id, Campaign.name,
            AdGroup.name, Profile.country_code, Profile.currency_code,
        )
        .order_by(func.sum(ProductDaily.cost).desc().nullslast())
        .limit(limit)
    )
    if profile_id:
        stmt = stmt.where(ProductAd.profile_id == profile_id)
    if campaign_id:
        stmt = stmt.where(ProductAd.campaign_id == campaign_id)

    out = []
    for r in session.execute(stmt).mappings().all():
        row = dict(r)
        for col in _METRIC_COLS:
            row[col] = row.get(col) or 0
        out.append(_derive(row))

    _attach_catalog(session, profile_id, out)

    ctx = advice.build_context(out, (end - start).days + 1, sensitivity)
    for row in out:
        row["suggestions"] = advice.suggest_product(row, ctx)
    return out


def campaign_portfolios(
    session: Session, profile_id: int | None = None
) -> dict[str, tuple[int, str]]:
    """Mapa kampania -> (id portfolio, nazwa). Bez portfolio dostaje zero.

    Zero zamiast None jest celowe i zgodne z widokiem portfolii: kampanie
    nieprzypisane maja tam wlasna pozycje, wiec sumy sie zgadzaja i da sie
    je odfiltrowac tak samo jak kazde inne.
    """
    stmt = (
        select(Campaign.campaign_id, Campaign.portfolio_id, Portfolio.name)
        .join(Portfolio, Portfolio.portfolio_id == Campaign.portfolio_id, isouter=True)
    )
    if profile_id:
        stmt = stmt.where(Campaign.profile_id == profile_id)

    return {
        str(cid): (int(pid or 0), nazwa or "Bez portfolio")
        for cid, pid, nazwa in session.execute(stmt).all()
    }


def all_suggestions(
    session: Session, profile_id: int | None, start: dt.date, end: dt.date,
    only_actionable: bool = True, limit: int = 500,
    portfolio_id: int | None = None, sensitivity: float = 1.0,
) -> list[dict[str, Any]]:
    """Zbiera sugestie z calego konta w jedna liste do przejrzenia.

    Kazdy wpis niesie identyfikator zrodla i akcje, wiec da sie go zatwierdzic
    bez wracania na ekran, z ktorego pochodzi. Dostaje tez portfolio kampanii,
    zeby dalo sie przerabiac konto linia produktowa po linii, a nie wszystko
    naraz - przy dwudziestu kilku portfoliach to rozni sie jak dzien od nocy.
    """
    out: list[dict[str, Any]] = []
    portfele = campaign_portfolios(session, profile_id)

    def collect(rows: list[dict[str, Any]], scope: str, id_key: str, name_key: str):
        for row in rows:
            for sug in row.get("suggestions") or []:
                if only_actionable and not sug.get("action"):
                    continue
                cid = str(
                    row.get("campaign_id")
                    or (row.get(id_key) if scope == "CAMPAIGN" else "")
                    or ""
                )
                pid_portfela, nazwa_portfela = portfele.get(cid, (0, "Bez portfolio"))
                if portfolio_id is not None and pid_portfela != portfolio_id:
                    continue
                out.append({
                    "scope": scope,
                    "entity_id": str(row.get(id_key) or ""),
                    "entity_name": row.get(name_key),
                    "portfolio_id": pid_portfela,
                    "portfolio_name": nazwa_portfela,
                    "campaign_id": row.get("campaign_id"),
                    "campaign_name": row.get("campaign_name")
                    or (row.get("name") if scope == "CAMPAIGN" else None),
                    "country": row.get("country"),
                    "currency": row.get("currency"),
                    "cost": row.get("cost"),
                    "sales": row.get("sales"),
                    "impressions": row.get("impressions"),
                    "clicks": row.get("clicks"),
                    "ctr": row.get("ctr"),
                    "cvr": row.get("cvr"),
                    "orders": row.get("orders"),
                    "acos": row.get("acos"),
                    # Efekt halo - przy decyzji o wykluczeniu ma znaczenie
                    # rozstrzygajace: pozycja moze byc nierentowna na wlasnym
                    # produkcie, a naped sprzedazy katalogu.
                    "sales_other_sku": row.get("sales_other_sku"),
                    "acos_own": row.get("acos_own"),
                    "halo_share": row.get("halo_share"),
                    "sales_own": row.get("sales_own"),
                    **sug,
                })

    collect(
        campaigns(session, profile_id, start, end, limit=1000, sensitivity=sensitivity),
        "CAMPAIGN", "campaign_id", "name",
    )
    collect(
        targets(session, profile_id, start, end, limit=3000, sensitivity=sensitivity),
        "TARGET", "target_id", "target_text",
    )
    collect(
        placements_by_campaign(session, profile_id, start, end, limit=2000),
        "PLACEMENT", "placement", "placement",
    )
    frazy = search_terms(session, profile_id, start, end, limit=5000, sensitivity=sensitivity)
    collect(frazy, "SEARCH_TERM", "search_term", "search_term")
    collect(
        product_ads(session, profile_id, start, end, limit=5000, sensitivity=sensitivity),
        "PRODUCT", "ad_id", "sku",
    )

    # Najpierw to, co kosztuje najwiecej - tam decyzja ma najwieksza wage
    order = {"bad": 0, "warn": 1, "good": 2, "info": 3}
    out.sort(key=lambda x: (order.get(x["level"], 9), -(x.get("cost") or 0)))
    out = _bez_powtorzonych_akcji(out)
    out = out[:limit]

    # Sugestie juz wyslane do kolejki znikaja z listy - czekaja w zatwierdzeniach
    # i nie ma sensu proponowac ich drugi raz.
    zakolejkowane = actions_mod.queued_action_keys(session, profile_id)
    if zakolejkowane:
        out = [
            r for r in out
            if not r.get("action")
            or actions_mod.action_key(r["action"]) not in zakolejkowane
        ]

    # Wykluczenie, ktore juz istnieje, Amazon odrzuci bledem 207 - a sugestia
    # wygladalaby na wykonalna az do zatwierdzenia. Odsiewamy je tu, zeby nie
    # proponowac pracy, ktora nie moze sie udac.
    istniejace = _istniejace_wykluczenia(session)
    out = [r for r in out if not _juz_wykluczone(r.get("action"), istniejace)]

    # Sugestie odrzucone recznie - na stale albo do wskazanej daty
    odrzucone = dismissed_keys(session, profile_id)
    if odrzucone:
        out = [
            r for r in out
            if (r.get("scope"), str(r.get("entity_id")), r.get("code")) not in odrzucone
        ]

    if profile_id:
        _attach_bid_ranges(session, profile_id, out)
    _attach_negative_patterns(out, frazy)
    return out


def _bez_powtorzonych_akcji(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Jedna akcja = jeden wiersz, nawet gdy widac ja z kilku stron.

    To samo zdarzenie opisuja dwa raporty Amazona. Wlasny keyword EXACT
    "flexible sockelleiste" siedzi w raporcie targetowania, a zapytanie, ktore
    go uruchomilo, brzmi tak samo i wraca w raporcie fraz. Obie pozycje
    proponowaly potem identyczne wykluczenie - ta sama kampania, grupa, tresc
    i typ dopasowania. Lista odsiewala duplikaty wzgledem kolejki i wykluczen
    juz istniejacych, ale nie sprawdzala, czy sama czegos nie powtarza.

    Przy zatwierdzeniu obu Amazon przyjmowal pierwsza pozycje i odrzucal druga
    bledem 207 - czyli czerwony wiersz przy zmianie, ktora sie udala.

    Zostawiamy wiersz z **najwieksza proba**: uzasadnienie cytuje liczby, wiec
    "17 klikniec" zamiast "219" zanizaloby wage decyzji. Kolejnosc listy
    zostaje nienaruszona.
    """
    from .actions import action_key

    najlepszy: dict[str, int] = {}
    for i, r in enumerate(rows):
        akcja = r.get("action")
        if not akcja:
            continue
        klucz = action_key(akcja)
        poprzedni = najlepszy.get(klucz)
        if poprzedni is None:
            najlepszy[klucz] = i
            continue
        a, b = rows[poprzedni], r
        waga = lambda x: ((x.get("clicks") or 0), (x.get("cost") or 0.0))
        if waga(b) > waga(a):
            najlepszy[klucz] = i

    zostaw = set(najlepszy.values())
    return [
        r for i, r in enumerate(rows)
        if not r.get("action") or i in zostaw
    ]


def dismissed_keys(session: Session, profile_id: int | None) -> set[tuple[str, str, str]]:
    """Klucze (scope, entity_id, code) sugestii odrzuconych i wciaz aktywnych."""
    from ..timeutil import utcnow

    warunki = [
        (DismissedSuggestion.until.is_(None)) | (DismissedSuggestion.until > utcnow())
    ]
    if profile_id:
        warunki.append(DismissedSuggestion.profile_id == profile_id)
    return {
        (sc, str(eid), code)
        for sc, eid, code in session.execute(
            select(DismissedSuggestion.scope, DismissedSuggestion.entity_id,
                   DismissedSuggestion.code).where(*warunki)
        ).all()
    }


def _istniejace_wykluczenia(session: Session) -> set[tuple[str, str, str]]:
    """Klucze wykluczen juz wprowadzonych: (grupa albo kampania, tekst, typ).

    Budujemy je raz na wywolanie i przekazujemy dalej - przy kilkuset
    sugestiach zapytanie na kazda z osobna kosztowaloby wiecej niz cala
    reszta liczenia. Cache miedzy wywolaniami byloby pulapka: po dodaniu
    wykluczenia lista musi to od razu uwzglednic.
    """
    return {
        (str(ag or cid), (txt or "").strip().lower(), (mt or "").upper())
        for ag, cid, txt, mt in session.execute(
            select(
                NegativeKeyword.ad_group_id,
                NegativeKeyword.campaign_id,
                NegativeKeyword.keyword_text,
                NegativeKeyword.match_type,
            ).where(NegativeKeyword.state != "ARCHIVED")
        ).all()
    }


def _juz_wykluczone(
    action: dict[str, Any] | None, istniejace: set[tuple[str, str, str]]
) -> bool:
    """Czy ta sugestia proponuje wykluczenie, ktore juz jest wprowadzone."""
    if not action or action.get("type") != "ADD_NEGATIVE":
        return False
    klucz = (
        str(action.get("ad_group_id") or action.get("campaign_id") or ""),
        str(action.get("keyword_text") or "").strip().lower(),
        str(action.get("match_type") or "NEGATIVE_EXACT").upper(),
    )
    return klucz in istniejace


# Slowo krotsze niz to jest zwykle przyimkiem albo rozmiarem - jako wykluczenie
# wyrazeniowe zablokowaloby pol konta.
MIN_TOKEN_LEN = 4
# Ile razy slowo musi wrocic w niesprzedajacych frazach, zeby wyrazenie mialo
# przewage nad wykluczaniem ich pojedynczo.
MIN_PATTERN_TERMS = 3


def _tokeny(tekst: str) -> set[str]:
    return {
        t for t in re.split(r"[^\w]+", (tekst or "").lower())
        if len(t) >= MIN_TOKEN_LEN
    }


def _attach_negative_patterns(
    rows: list[dict[str, Any]], frazy: list[dict[str, Any]]
) -> None:
    """Podpowiada wykluczenie wyrazeniowe tam, gdzie wraca cale slowo.

    Wykluczanie fraz pojedynczo jest bezpieczne, ale przy powtarzajacym sie
    slowie nieskuteczne: zablokujemy pietnascie zapytan, a szesnaste przyjdzie
    jutro. Wyrazenie zalatwia je naraz - pod warunkiem, ze to slowo NIE
    wystepuje w zadnej frazie, ktora sprzedaje. Ten warunek jest tu istotny:
    bez niego jedno wykluczenie potrafi wyciac dzialajaca czesc konta.
    """
    sprzedajace: set[str] = set()
    for f in frazy:
        if (f.get("orders") or 0) > 0:
            sprzedajace |= _tokeny(f.get("search_term") or "")

    # Ile niesprzedajacych fraz zawiera dane slowo i ile to kosztuje
    licznik: dict[str, int] = {}
    koszt: dict[str, float] = {}
    for r in rows:
        if (r.get("action") or {}).get("type") != "ADD_NEGATIVE":
            continue
        for t in _tokeny(r.get("entity_name") or ""):
            if t in sprzedajace:
                continue
            licznik[t] = licznik.get(t, 0) + 1
            koszt[t] = koszt.get(t, 0.0) + (r.get("cost") or 0.0)

    for r in rows:
        akcja = r.get("action") or {}
        if akcja.get("type") != "ADD_NEGATIVE":
            continue
        kandydaci = [
            (t, licznik[t]) for t in _tokeny(r.get("entity_name") or "")
            if licznik.get(t, 0) >= MIN_PATTERN_TERMS
        ]
        if not kandydaci:
            r["pattern"] = None
            continue
        token, ile = max(kandydaci, key=lambda x: x[1])
        r["pattern"] = {
            "token": token,
            "terms": ile,
            "cost": round(koszt.get(token, 0.0), 2),
        }


def _attach_bid_ranges(
    session: Session, profile_id: int, rows: list[dict[str, Any]]
) -> None:
    """Dokłada widelki rekomendowane przez Amazona do sugestii dotyczacych stawek.

    Dla istniejacych keywordow szukamy po ich identyfikatorze, dla fraz jeszcze
    nieprzejetych - po zlozonym kluczu grupa+dopasowanie+tekst, bo ta sama fraza
    w innej grupie ma inna wycene.

    Brak widelek nie jest bledem: pobieramy je w tle i moga byc jeszcze
    niepobrane albo Amazon moze ich nie miec dla danej frazy.
    """
    from ..etl import bid_recos

    istniejace = [
        str(r["entity_id"]) for r in rows
        if r.get("scope") == "TARGET" and r.get("entity_id")
    ]
    nowe: dict[str, str] = {}
    for r in rows:
        akcja = r.get("action") or {}
        if akcja.get("type") != "HARVEST":
            continue
        klucz = bid_recos.new_keyword_key(
            str(akcja.get("ad_group_id") or ""),
            str(akcja.get("match_type") or "EXACT"),
            str(akcja.get("keyword_text") or ""),
        )
        nowe[id(r)] = klucz

    reco_kw = bid_recos.lookup(session, profile_id, "KEYWORD", istniejace)
    reco_new = bid_recos.lookup(session, profile_id, "NEW_KEYWORD", list(set(nowe.values())))

    for r in rows:
        widelki = None
        if id(r) in nowe:
            widelki = reco_new.get(nowe[id(r)])
        elif r.get("scope") == "TARGET":
            widelki = reco_kw.get(str(r.get("entity_id")))
        r["amazon_bid"] = widelki


def negative_candidates(
    session: Session, profile_id: int | None, start: dt.date, end: dt.date,
    min_clicks: int = 10, max_orders: int = 0, min_cost: float = 0.0, limit: int = 500,
) -> list[dict[str, Any]]:
    """Search termy, ktore przepalaja budzet bez konwersji."""
    stmt = (
        select(
            SearchTermDaily.search_term,
            SearchTermDaily.campaign_id,
            SearchTermDaily.ad_group_id,
            SearchTermDaily.target_id,
            Campaign.name.label("campaign_name"),
            AdGroup.name.label("ad_group_name"),
            *_sums(SearchTermDaily),
        )
        .join(Campaign, Campaign.campaign_id == SearchTermDaily.campaign_id, isouter=True)
        .join(AdGroup, AdGroup.ad_group_id == SearchTermDaily.ad_group_id, isouter=True)
        .where(_window(SearchTermDaily, profile_id, start, end))
        .group_by(
            SearchTermDaily.search_term, SearchTermDaily.campaign_id,
            SearchTermDaily.ad_group_id, SearchTermDaily.target_id,
            Campaign.name, AdGroup.name,
        )
        .having(
            and_(
                func.sum(SearchTermDaily.clicks) >= min_clicks,
                func.sum(SearchTermDaily.orders) <= max_orders,
                func.sum(SearchTermDaily.cost) >= min_cost,
            )
        )
        .order_by(func.sum(SearchTermDaily.cost).desc())
        .limit(limit)
    )
    return [_derive(dict(r)) for r in session.execute(stmt).mappings().all()]


def harvest_candidates(
    session: Session, profile_id: int | None, start: dt.date, end: dt.date,
    min_orders: int = 2, max_acos: float = 35.0, limit: int = 500,
) -> list[dict[str, Any]]:
    """Search termy warte przeniesienia do wlasnej kampanii EXACT.

    Zwraca tylko te, ktorych jeszcze nie ma jako keyword EXACT na koncie.
    """
    existing = {
        (t or "").strip().lower()
        for t in session.execute(
            select(Keyword.keyword_text).where(Keyword.match_type == "EXACT")
        ).scalars()
    }

    stmt = (
        select(
            SearchTermDaily.search_term,
            SearchTermDaily.campaign_id,
            SearchTermDaily.ad_group_id,
            Campaign.name.label("campaign_name"),
            AdGroup.name.label("ad_group_name"),
            *_sums(SearchTermDaily),
        )
        .join(Campaign, Campaign.campaign_id == SearchTermDaily.campaign_id, isouter=True)
        .join(AdGroup, AdGroup.ad_group_id == SearchTermDaily.ad_group_id, isouter=True)
        .where(_window(SearchTermDaily, profile_id, start, end))
        .group_by(
            SearchTermDaily.search_term, SearchTermDaily.campaign_id,
            SearchTermDaily.ad_group_id, Campaign.name, AdGroup.name,
        )
        .having(func.sum(SearchTermDaily.orders) >= min_orders)
        .order_by(func.sum(SearchTermDaily.sales).desc())
        .limit(limit * 3)
    )

    out = []
    for r in session.execute(stmt).mappings().all():
        row = _derive(dict(r))
        if row["acos"] is not None and row["acos"] > max_acos:
            continue
        if (row["search_term"] or "").strip().lower() in existing:
            continue
        # Sugerowany bid = przychod na klikniecie * docelowy ACOS.
        # Cap na 3x biezacy CPC: bez niego fraza z kilkoma szczesliwymi konwersjami
        # generuje absurdalna stawke, ktora przepali budzet zanim urealni sie CVR.
        if row["cpc"] and row["clicks"]:
            rpc = (row["sales"] or 0.0) / row["clicks"]
            raw_bid = rpc * (max_acos / 100.0)
            row["suggested_bid"] = round(max(0.02, min(raw_bid, row["cpc"] * 3)), 2)
        else:
            row["suggested_bid"] = None
        out.append(row)
        if len(out) >= limit:
            break
    return out


# Slowa, ktore same z siebie nic nie znacza. Fragment zaczynajacy sie albo
# konczacy takim slowem jest bezuzyteczny jako wykluczenie frazowe: "leisten
# fur" zablokowaloby wszystko, co po tym nastepuje, wiec nikt tego nie uzyje.
STOPWORDY = {
    # niemiecki
    "fur", "für", "und", "mit", "der", "die", "das", "den", "dem", "zu", "im",
    "in", "an", "auf", "von", "am", "als", "ohne", "aus", "bei", "zum", "zur",
    # francuski
    "pour", "avec", "de", "des", "du", "la", "le", "les", "et", "en", "sur",
    # hiszpanski i wloski
    "para", "con", "por", "el", "los", "las", "y", "il", "lo", "gli", "e", "di",
    # niderlandzki i angielski
    "voor", "met", "van", "het", "een", "for", "with", "and", "the", "of", "to",
    # jednostki i liczby same w sobie
    "cm", "mm", "m", "x",
}

# Ile zamowien musi sie "spodziewac" fragment, zeby jego wynik cokolwiek
# znaczyl. Przeliczamy to na klikniecia przez konwersje konta, wiec prog sam
# dostosowuje sie do rynku - przy slabszej konwersji potrzeba wiecej klikniec.
OCZEKIWANE_ZAMOWIENIA = 3
MIN_KLIKNIEC_ZAKRES = (15, 120)

# Ile razy gorszy od sredniej konta musi byc fragment, zeby trafic na liste.
# Ponizej tego to nie jest marnotrawstwo, tylko normalny rozrzut.
PROG_ACOS = 1.5


def _czy_uzyteczny(slowa: list[str]) -> bool:
    """Czy z tego fragmentu da sie zrobic sensowne wykluczenie frazowe."""
    if slowa[0] in STOPWORDY or slowa[-1] in STOPWORDY:
        return False
    # ASIN-y to targetowanie produktowe, nie jezyk zapytan - maja wlasny widok
    # z miniaturami, a tutaj tylko zasmiecalyby liste.
    if any(ASIN_WZOR.match(w) for w in slowa):
        return False
    # Fragment zlozony wylacznie z liczb nic nie mowi
    if all(w.replace(",", "").replace(".", "").isdigit() for w in slowa):
        return False
    return True


def ngram_analysis(
    session: Session, profile_id: int | None, start: dt.date, end: dt.date,
    max_n: int = 3, min_clicks: int | None = None, limit: int = 200,
    show_all: bool = False,
) -> dict[str, Any]:
    """Fragmenty fraz, ktore przepalaja budzet - jedna lista dla 1, 2 i 3 slow.

    Trzy decyzje, kazda sprawdzona na danych tego konta:

    1. **Jedna lista zamiast trzech zakladek.** Najbardziej kosztowny fragment
       bywa jednym slowem (`doppelstabmattenzaun`), a jego doprecyzowanie -
       dwoma (`sichtschutz doppelstabmattenzaun`). Rozdzielone widoki
       rozrywaly te pare i trzeba bylo zgadywac, gdzie szukac.

    2. **Ranking po zmarnowanym wydatku, nie po koszcie ani ACOS.** Sortowanie
       po koszcie wyrzucalo na gore najlepsze frazy konta (ACOS 9-19%), bo one
       maja najwiekszy wolumen. Sortowanie po ACOS wyrzucalo szum z 17
       klikniec. Marnotrawstwo - czyli wydatek ponad to, co usprawiedliwia
       sprzedaz - odpowiada wprost na pytanie "gdzie ucieka budzet".

    3. **Prog wzgledny, nie staly.** Na liste trafia tylko fragment co najmniej
       poltora raza gorszy od sredniej konta. Bez tego lista jest pelna
       fragmentow, ktore sa po prostu przecietne.

    Nic nie znika po cichu: `hidden` mowi, ile pozycji i z jakiego powodu
    zostalo odfiltrowanych, a `show_all` wylacza progi.
    """
    # Grupujemy takze po kampanii, zeby wiedziec, gdzie fragment wystepuje -
    # bez tego trzeba bylo klikac w kazdy z osobna, zeby to sprawdzic.
    rows = session.execute(
        select(
            SearchTermDaily.search_term,
            SearchTermDaily.campaign_id,
            *_sums(SearchTermDaily),
        )
        .where(_window(SearchTermDaily, profile_id, start, end))
        .group_by(SearchTermDaily.search_term, SearchTermDaily.campaign_id)
    ).mappings().all()

    koszt_konta = sum(float(r["cost"] or 0) for r in rows)
    sprzedaz_konta = sum(float(r["sales"] or 0) for r in rows)
    klik_konta = sum(int(r["clicks"] or 0) for r in rows)
    zam_konta = sum(int(r["orders"] or 0) for r in rows)

    acos_konta = (koszt_konta / sprzedaz_konta * 100) if sprzedaz_konta else 30.0
    cvr_konta = (zam_konta / klik_konta * 100) if klik_konta else 8.0

    if min_clicks is None:
        potrzeba = OCZEKIWANE_ZAMOWIENIA / (cvr_konta / 100) if cvr_konta else 30
        min_clicks = int(min(max(potrzeba, MIN_KLIKNIEC_ZAKRES[0]), MIN_KLIKNIEC_ZAKRES[1]))

    prog_acos = acos_konta * PROG_ACOS

    kubelki: dict[str, dict[str, Any]] = {}
    odrzucone_slabe = 0
    for r in rows:
        slowa = (r["search_term"] or "").split()
        for n in range(1, max_n + 1):
            for i in range(max(0, len(slowa) - n + 1)):
                fragment = slowa[i : i + n]
                if not _czy_uzyteczny(fragment):
                    odrzucone_slabe += 1
                    continue
                klucz = " ".join(fragment)
                b = kubelki.setdefault(klucz, {
                    "ngram": klucz, "n": n, "terms": set(), "_kampanie": {},
                    **{k: 0 for k in _METRIC_COLS},
                })
                b["terms"].add(r["search_term"])
                for k in _METRIC_COLS:
                    b[k] += r[k] or 0
                # Koszt i zamowienia per kampania - kolejnosc wg wagi, a przy
                # okazji widac, w ktorych kampaniach fragment nic nie sprzedaje.
                # To wlasnie te kampanie sa kandydatem do wykluczenia.
                cid = r["campaign_id"]
                k = b["_kampanie"].setdefault(cid, {"cost": 0.0, "orders": 0})
                k["cost"] += float(r["cost"] or 0)
                k["orders"] += int(r["orders"] or 0)

    nazwy_kampanii = dict(
        session.execute(select(Campaign.campaign_id, Campaign.name)).all()
    )

    za_male = 0
    ponizej_progu = 0
    kandydaci: list[dict[str, Any]] = []
    for b in kubelki.values():
        if b["clicks"] < min_clicks:
            za_male += 1
            continue
        kampanie = sorted(
            b.pop("_kampanie").items(), key=lambda kv: -kv[1]["cost"]
        )
        b["terms"] = len(b["terms"])
        wiersz = _derive(dict(b))
        wiersz["campaigns"] = [
            {
                "campaign_id": cid,
                "campaign_name": nazwy_kampanii.get(cid) or cid,
                "cost": round(dane["cost"], 2),
                "orders": dane["orders"],
            }
            for cid, dane in kampanie
        ]
        wiersz["campaign_count"] = len(kampanie)
        # Ile kampanii nie sprzedalo na tym fragmencie ani razu - tam
        # wykluczenie jest decyzja bez kosztu.
        wiersz["campaigns_no_sales"] = sum(1 for _, d in kampanie if not d["orders"])
        wiersz["cost_no_sales"] = round(
            sum(d["cost"] for _, d in kampanie if not d["orders"]), 2
        )
        # Wydatek ponad to, co usprawiedliwia sprzedaz przy sredniej konta.
        # Przy zerowej sprzedazy marnotrawstwem jest caly koszt.
        wiersz["waste"] = round(
            wiersz["cost"] - (wiersz["sales"] * acos_konta / 100), 2
        )
        wiersz["acos_ratio"] = (
            round(wiersz["acos"] / acos_konta, 2) if wiersz["acos"] else None
        )
        if not show_all:
            if wiersz["acos"] is not None and wiersz["acos"] < prog_acos:
                ponizej_progu += 1
                continue
            if wiersz["waste"] <= 0:
                ponizej_progu += 1
                continue
        kandydaci.append(wiersz)

    kandydaci.sort(key=lambda x: -x["waste"])

    # Zagniezdzanie: dluzszy fragment zawierajacy krotszy opisuje ten sam ruch.
    # Pokazanie ich obok siebie sugerowaloby dwa osobne problemy, a decyzja
    # jest jedna - dlatego dluzszy lezy pod krotszym.
    wybrane: list[dict[str, Any]] = []
    zajete: list[list[str]] = []
    for w in kandydaci:
        slowa = w["ngram"].split()
        rodzic = None
        for kandydat in wybrane:
            krotsze = kandydat["ngram"].split()
            if len(krotsze) >= len(slowa):
                continue
            if any(
                slowa[i : i + len(krotsze)] == krotsze
                for i in range(len(slowa) - len(krotsze) + 1)
            ):
                rodzic = kandydat
                break
        if rodzic is not None:
            rodzic.setdefault("children", []).append(w)
        else:
            w.setdefault("children", [])
            wybrane.append(w)
        zajete.append(slowa)

    return {
        "account": {
            "acos": round(acos_konta, 2),
            "cvr": round(cvr_konta, 2),
            "min_clicks": min_clicks,
        },
        "threshold_acos": round(prog_acos, 2),
        "items": wybrane[:limit],
        "total": len(wybrane),
        "hidden": {
            "below_threshold": ponizej_progu,
            "too_small": za_male,
            "not_usable": odrzucone_slabe,
        },
    }


def _zawiera_ngram(fraza: str, ngram: str) -> bool:
    """Czy fraza zawiera ten n-gram jako ciag calych slow.

    Tak samo dziala wykluczenie frazowe Amazona: `NEGATIVE_PHRASE` blokuje
    zapytania zawierajace podany ciag slow po kolei. Zwykle `in` nie wystarczy,
    bo "regal" trafiloby tez w "regalsystem" i pokazalibysmy skutki
    szersze, niz beda naprawde.
    """
    slowa = (fraza or "").split()
    czesci = ngram.split()
    if not czesci or len(slowa) < len(czesci):
        return False
    for i in range(len(slowa) - len(czesci) + 1):
        if slowa[i : i + len(czesci)] == czesci:
            return True
    return False


def ngram_breakdown(
    session: Session, profile_id: int | None, ngram: str,
    start: dt.date, end: dt.date, limit: int = 40,
) -> dict[str, Any]:
    """Gdzie wystepuje ten n-gram - w rozbiciu na grupy reklam.

    Lista n-gramow mowi, ze rodzina slow przepala budzet, i pokazuje kampanie,
    ale zeby cos wykluczyc, trzeba zejsc do grup reklam - tam siedza
    wykluczenia. To rozbicie zamyka petle.

    Podajemy tez, ile zamowien maja frazy z tym fragmentem. Wykluczenie
    frazowe zablokuje je wszystkie, wiec przy n-gramie, ktory cos sprzedaje,
    to jest cena decyzji - a nie sam zysk.
    """
    ngram = (ngram or "").strip().lower()
    if not ngram:
        return {"ngram": ngram, "items": [], "totals": None}

    rows = session.execute(
        select(
            SearchTermDaily.search_term,
            SearchTermDaily.campaign_id,
            SearchTermDaily.ad_group_id,
            *_sums(SearchTermDaily),
        )
        .where(_window(SearchTermDaily, profile_id, start, end))
        .group_by(
            SearchTermDaily.search_term,
            SearchTermDaily.campaign_id,
            SearchTermDaily.ad_group_id,
        )
    ).mappings().all()

    pasujace = [r for r in rows if _zawiera_ngram(r["search_term"], ngram)]
    if not pasujace:
        return {"ngram": ngram, "items": [], "totals": None}

    grupy: dict[tuple[str, str | None], dict[str, Any]] = {}
    for r in pasujace:
        klucz = (r["campaign_id"], r["ad_group_id"])
        g = grupy.setdefault(klucz, {
            "campaign_id": r["campaign_id"],
            "ad_group_id": r["ad_group_id"],
            "terms": 0,
            "examples": [],
            **{k: 0 for k in _METRIC_COLS},
        })
        g["terms"] += 1
        for k in _METRIC_COLS:
            g[k] += r[k] or 0
        # Kilka przykladow wystarczy, zeby ocenic, co dokladnie zniknie.
        if len(g["examples"]) < 5:
            g["examples"].append({
                "search_term": r["search_term"],
                "clicks": int(r["clicks"] or 0),
                "cost": round(float(r["cost"] or 0), 2),
                "orders": int(r["orders"] or 0),
            })

    nazwy_kampanii = dict(
        session.execute(
            select(Campaign.campaign_id, Campaign.name).where(
                Campaign.campaign_id.in_([k[0] for k in grupy])
            )
        ).all()
    )
    nazwy_grup = dict(
        session.execute(
            select(AdGroup.ad_group_id, AdGroup.name).where(
                AdGroup.ad_group_id.in_([k[1] for k in grupy if k[1]])
            )
        ).all()
    )

    out = []
    for g in grupy.values():
        wiersz = _derive(dict(g))
        wiersz["campaign_name"] = nazwy_kampanii.get(g["campaign_id"])
        wiersz["ad_group_name"] = nazwy_grup.get(g["ad_group_id"])
        wiersz["examples"] = g["examples"]
        out.append(wiersz)
    out.sort(key=lambda x: -x["cost"])

    suma = {k: sum(float(r[k] or 0) for r in pasujace) for k in _METRIC_COLS}
    totals = _derive(dict(suma))
    totals["terms"] = len({r["search_term"] for r in pasujace})
    totals["ad_groups"] = len(grupy)

    return {
        "ngram": ngram,
        "items": out[:limit],
        "total_groups": len(grupy),
        "totals": totals,
    }


# Kampania liczy sie jako wspoldzielaca fraze, gdy ma w niej realny udzial -
# nie wtedy, gdy przypadkiem zlapala kilka klikniec.
MIN_SHARE_CLICKS = 5
MIN_SHARE_RATIO = 0.10


def internal_competition(
    session: Session, profile_id: int | None, start: dt.date, end: dt.date,
    min_campaigns: int = 2, min_clicks: int = 10, limit: int = 300,
) -> list[dict[str, Any]]:
    """Frazy, o ktore licytuje kilka wlasnych kampanii naraz.

    Amazon dopuszcza do aukcji tylko jedna reklame z konta, wiec kampanie nie
    podbijaja sobie ceny wprost. Koszt jest inny i mniej widoczny: budzet i
    historia jednej frazy rozjezdzaja sie na kilka kampanii, przez co kazda
    uczy sie wolniej, a wyniki trudniej porownac. Dlatego pokazujemy fakt i
    rozbicie po kampaniach - decyzja, ktora zostawic, nalezy do czlowieka,
    bo zalezy od roli kampanii (auto zbierajaca vs exact dowozaca).
    """
    rows = session.execute(
        select(
            SearchTermDaily.search_term,
            SearchTermDaily.campaign_id,
            Campaign.name.label("campaign_name"),
            Campaign.targeting_type.label("targeting_type"),
            *_sums(SearchTermDaily),
        )
        .join(Campaign, Campaign.campaign_id == SearchTermDaily.campaign_id, isouter=True)
        .where(_window(SearchTermDaily, profile_id, start, end))
        .group_by(
            SearchTermDaily.search_term, SearchTermDaily.campaign_id,
            Campaign.name, Campaign.targeting_type,
        )
        .having(func.sum(SearchTermDaily.clicks) > 0)
    ).mappings().all()

    grouped: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        grouped.setdefault(r["search_term"], []).append(_derive(dict(r)))

    out: list[dict[str, Any]] = []
    for term, parts in grouped.items():
        clicks = sum(p["clicks"] for p in parts)
        if clicks < min_clicks:
            continue

        # Sama liczba kampanii wprowadza w blad: fraza potrafi otrzec sie o
        # kilkanascie kampanii, zbierajac w wiekszosci z nich po cztery
        # klikniecia. To nie jest rozproszenie, tylko szum. Liczymy wiec tylko
        # kampanie z realnym udzialem w ruchu tej frazy.
        prog = max(MIN_SHARE_CLICKS, clicks * MIN_SHARE_RATIO)
        znaczace = [p for p in parts if p["clicks"] >= prog]
        if len(znaczace) < min_campaigns:
            continue

        cost = sum(p["cost"] for p in parts)
        orders = sum(p["orders"] for p in parts)
        sales = sum(p["sales"] for p in parts)
        parts.sort(key=lambda p: p["cost"], reverse=True)

        # Kampania, ktora dowozi najwiecej sprzedazy - kandydatka na te, przy
        # ktorej fraze warto zostawic. Gdy nikt nie sprzedaje, zostaje sam koszt.
        best = max(parts, key=lambda p: (p["sales"], -p["cost"]))
        czolowa = max(parts, key=lambda p: p["clicks"])
        out.append(_derive({
            "search_term": term,
            "campaign_count": len(znaczace),
            "campaign_count_all": len(parts),
            # Udzial najwiekszej kampanii w klikniecach frazy. Blisko 100%
            # znaczy, ze mimo wielu kampanii ruch i tak jest skupiony.
            "top_share": round(czolowa["clicks"] / clicks * 100, 1) if clicks else None,
            "impressions": sum(p["impressions"] for p in parts),
            "clicks": clicks,
            "cost": cost,
            "orders": orders,
            "sales": sales,
            "units": sum(p["units"] for p in parts),
            "best_campaign_id": best["campaign_id"] if best["sales"] else None,
            "best_campaign_name": best["campaign_name"] if best["sales"] else None,
            "breakdown": [
                {
                    "significant": p["clicks"] >= prog,
                    "campaign_id": p["campaign_id"],
                    "campaign_name": p["campaign_name"],
                    "targeting_type": p["targeting_type"],
                    "clicks": p["clicks"], "cost": p["cost"],
                    "orders": p["orders"], "sales": p["sales"],
                    "acos": p["acos"], "cvr": p["cvr"],
                }
                for p in parts
            ],
        }))

    # Najpierw tam, gdzie rozproszenie jest realne i drogie zarazem: koszt
    # przemnozony przez czesc ruchu lezaca poza czolowa kampania.
    out.sort(key=lambda x: x["cost"] * (100 - (x["top_share"] or 100)) / 100, reverse=True)
    return out[:limit]


def sku_overlap(
    session: Session, profile_id: int | None, start: dt.date, end: dt.date,
    min_campaigns: int = 2, min_clicks: int = 10, limit: int = 300,
) -> list[dict[str, Any]]:
    """Ten sam produkt reklamowany w kilku kampaniach naraz.

    Rozproszenie samo w sobie nie jest bledem - produkt bywa swiadomie wystawiony
    i w kampanii automatycznej, i w recznej. Warto na nie spojrzec wtedy, gdy
    kampanie roznia sie skutecznoscia: ten sam produkt, ta sama cena, a ACOS
    dwa razy wyzszy znaczy, ze budzet plynie gorszym kanalem.
    """
    rows = session.execute(
        select(
            ProductDaily.sku,
            ProductDaily.asin,
            ProductDaily.campaign_id,
            Campaign.name.label("campaign_name"),
            Campaign.targeting_type.label("targeting_type"),
            *_sums(ProductDaily),
        )
        .join(Campaign, Campaign.campaign_id == ProductDaily.campaign_id, isouter=True)
        .where(_window(ProductDaily, profile_id, start, end))
        .group_by(
            ProductDaily.sku, ProductDaily.asin, ProductDaily.campaign_id,
            Campaign.name, Campaign.targeting_type,
        )
        .having(func.sum(ProductDaily.clicks) > 0)
    ).mappings().all()

    grouped: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        grouped.setdefault(r["sku"] or r["asin"] or "?", []).append(_derive(dict(r)))

    out: list[dict[str, Any]] = []
    for sku, parts in grouped.items():
        clicks = sum(p["clicks"] for p in parts)
        if clicks < min_clicks:
            continue

        prog = max(MIN_SHARE_CLICKS, clicks * MIN_SHARE_RATIO)
        znaczace = [p for p in parts if p["clicks"] >= prog]
        if len(znaczace) < min_campaigns:
            continue

        parts.sort(key=lambda p: p["cost"], reverse=True)
        cost = sum(p["cost"] for p in parts)
        sales = sum(p["sales"] for p in parts)

        # Rozpietosc ACOS miedzy znaczacymi kampaniami - to ona decyduje, czy
        # rozproszenie w ogole warto ogladac. Kampanie bez sprzedazy pomijamy
        # w liczeniu rozpietosci, bo ich ACOS jest nieokreslony, a nie zly.
        z_acos = [p["acos"] for p in znaczace if p["acos"] is not None]
        rozpietosc = (
            round(max(z_acos) - min(z_acos), 2) if len(z_acos) >= 2 else None
        )

        out.append(_derive({
            "sku": sku,
            "asin": next((p["asin"] for p in parts if p["asin"]), None),
            "campaign_count": len(znaczace),
            "campaign_count_all": len(parts),
            "acos_spread": rozpietosc,
            "impressions": sum(p["impressions"] for p in parts),
            "clicks": clicks,
            "cost": cost,
            "orders": sum(p["orders"] for p in parts),
            "sales": sales,
            "units": sum(p["units"] for p in parts),
            "breakdown": [
                {
                    "significant": p["clicks"] >= prog,
                    "campaign_id": p["campaign_id"],
                    "campaign_name": p["campaign_name"],
                    "targeting_type": p["targeting_type"],
                    "clicks": p["clicks"], "cost": p["cost"],
                    "orders": p["orders"], "sales": p["sales"],
                    "acos": p["acos"], "cvr": p["cvr"],
                }
                for p in parts
            ],
        }))

    # Najpierw produkty, przy ktorych roznica miedzy kampaniami jest najdrozsza
    out.sort(key=lambda x: (x["acos_spread"] or 0) * x["cost"], reverse=True)
    out = out[:limit]
    _attach_catalog(session, profile_id, out)
    return out


def _attach_catalog(
    session: Session, profile_id: int | None, rows: list[dict[str, Any]]
) -> None:
    """Dokłada tytul i miniature produktu.

    Kod `FBA-800-18-BIALY-5-NEW` nie mowi nic - zdjecie mowi od razu, o ktory
    produkt chodzi. Brak miniatury nie jest bledem: katalog pobieramy w tle
    i moze go jeszcze nie byc.
    """
    from ..etl import catalog

    asiny = [str(r["asin"]) for r in rows if r.get("asin")]
    if not asiny:
        return

    marketplace = None
    if profile_id:
        marketplace = session.execute(
            select(Profile.marketplace_id).where(Profile.profile_id == profile_id)
        ).scalar()

    dane = catalog.lookup(session, marketplace, asiny)
    for r in rows:
        r["catalog"] = dane.get(str(r.get("asin") or ""))


# ASIN ma staly ksztalt; w raporcie fraz Amazon zapisuje go mala litera.
ASIN_WZOR = re.compile(r"^b[0-9a-z]{9}$", re.I)


def _wyluskaj_asin(tekst: str | None) -> str | None:
    """ASIN ukryty w tekscie frazy albo wyrazenia wykluczenia."""
    if not tekst:
        return None
    czysty = tekst.strip()
    if ASIN_WZOR.match(czysty):
        return czysty.upper()
    # Wykluczenia produktowe maja postac "ASIN_SAME_AS B08XYZ1234"
    for slowo in czysty.replace("_", " ").split():
        if ASIN_WZOR.match(slowo):
            return slowo.upper()
    return None


def _attach_catalog_po_tekscie(
    session: Session, profile_id: int | None, rows: list[dict[str, Any]], pole: str
) -> None:
    """Dokłada katalog tam, gdzie ASIN siedzi w tekscie, a nie w osobnej kolumnie.

    W kampaniach produktowych Amazon zapisuje ASIN-y konkurencji jako frazy
    wyszukiwania, a wykluczenia jako "ASIN_SAME_AS B08XYZ1234". Bez tytulu
    i zdjecia `b093zsryyw` to goly kod, a wlasnie na tej podstawie podejmuje
    sie decyzje o wykluczeniu produktu.
    """
    from ..etl import catalog

    znalezione = {id(r): _wyluskaj_asin(r.get(pole)) for r in rows}
    asiny = [a for a in znalezione.values() if a]
    if not asiny:
        return

    marketplace = None
    if profile_id:
        marketplace = session.execute(
            select(Profile.marketplace_id).where(Profile.profile_id == profile_id)
        ).scalar()

    dane = catalog.lookup(session, marketplace, asiny)
    for r in rows:
        asin = znalezione.get(id(r))
        if asin:
            r["asin"] = asin
            r["catalog"] = dane.get(asin)


def portfolio_timeseries(
    session: Session, profile_id: int | None, start: dt.date, end: dt.date,
) -> dict[str, list[dict[str, Any]]]:
    """Dzienne wyniki kazdego portfolio, do wykresow przebiegu w tabeli.

    Zwracamy slownik po portfolio_id, zeby front nie musial grupowac tysiaca
    wierszy przy kazdym renderze. Kampanie bez portfolio ida pod kluczem "0" -
    tak samo jak w `portfolios`, wiec sumy sie zgadzaja.
    """
    # Patrz komentarz w `portfolios`: ten sam obiekt wyrazenia w select() i
    # group_by(), inaczej dwa niezalezne bind-parametry dla literalu 0
    # wywoluja GroupingError w Postgresie.
    portfolio_id_expr = func.coalesce(Campaign.portfolio_id, 0)
    rows = session.execute(
        select(
            portfolio_id_expr.label("portfolio_id"),
            CampaignDaily.date,
            *_sums(CampaignDaily),
        )
        .join(Campaign, Campaign.campaign_id == CampaignDaily.campaign_id)
        .where(_window(CampaignDaily, profile_id, start, end))
        .group_by(portfolio_id_expr, CampaignDaily.date)
        .order_by(CampaignDaily.date)
    ).mappings().all()

    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        point = _derive({
            "date": r["date"].isoformat(),
            **{k: (r[k] or 0) for k in _METRIC_COLS},
            "_tos_num": r.get("_tos_num"),
            "_tos_den": r.get("_tos_den"),
        })
        out.setdefault(str(r["portfolio_id"]), []).append(point)
    return out


def monthly(
    session: Session, profile_id: int | None, months: int = 6
) -> list[dict[str, Any]]:
    """Wyniki miesiac po miesiacu - reklama obok sprzedazy calkowitej.

    Grupujemy w Pythonie, a nie w SQL, bo funkcje daty roznia sie miedzy SQLite
    a Postgresem, a wolumen jest maly: kilkaset dni razy kilka rynkow.

    Miesiac niepelny (biezacy albo pierwszy w zakresie danych) oznaczamy, zeby
    nie porownywac dziesieciu dni z trzydziestoma i nie wyciagac z tego wnioskow.
    """
    dzis = dt.date.today()
    start = (dzis.replace(day=1) - dt.timedelta(days=1)).replace(day=1)
    for _ in range(months - 1):
        start = (start - dt.timedelta(days=1)).replace(day=1)

    conds = [CampaignDaily.date >= start]
    if profile_id:
        conds.append(CampaignDaily.profile_id == profile_id)

    reklama = session.execute(
        select(
            CampaignDaily.date,
            func.sum(CampaignDaily.cost),
            func.sum(CampaignDaily.sales),
            func.sum(CampaignDaily.orders),
        ).where(*conds).group_by(CampaignDaily.date)
    ).all()

    calosc = total_sales.daily_totals(session, profile_id, start, dzis)

    grupy: dict[str, dict[str, Any]] = {}
    for data, koszt, sprzedaz, zamowienia in reklama:
        klucz = data.strftime("%Y-%m")
        g = grupy.setdefault(klucz, {
            "month": klucz, "cost": 0.0, "ad_sales": 0.0, "orders": 0,
            "total_sales": 0.0, "days": 0, "days_with_total": 0,
            # Wydatek i sprzedaz reklamowa liczone TYLKO z dni, dla ktorych
            # znamy sprzedaz calkowita. Bez tego TACOS za miesiac z czesciowym
            # importem dzieli pelny wydatek przez ulamek przychodu i wychodzi
            # liczba bez sensu - a wyglada wiarygodnie.
            "_cost_matched": 0.0, "_ad_sales_matched": 0.0,
        })
        g["cost"] += float(koszt or 0)
        g["ad_sales"] += float(sprzedaz or 0)
        g["orders"] += int(zamowienia or 0)
        g["days"] += 1
        if data in calosc:
            g["_cost_matched"] += float(koszt or 0)
            g["_ad_sales_matched"] += float(sprzedaz or 0)

    for data, kwota in calosc.items():
        klucz = data.strftime("%Y-%m")
        g = grupy.get(klucz)
        if g is None:
            continue
        g["total_sales"] += kwota
        g["days_with_total"] += 1

    out: list[dict[str, Any]] = []
    for klucz in sorted(grupy):
        g = grupy[klucz]
        rok, miesiac = int(klucz[:4]), int(klucz[5:])
        # Ile dni ma ten miesiac i ile z nich juz minelo
        if miesiac == 12:
            nastepny = dt.date(rok + 1, 1, 1)
        else:
            nastepny = dt.date(rok, miesiac + 1, 1)
        dni_w_miesiacu = (nastepny - dt.date(rok, miesiac, 1)).days

        g["acos"] = round(g["cost"] / g["ad_sales"] * 100, 2) if g["ad_sales"] else None
        g["roas"] = round(g["ad_sales"] / g["cost"], 2) if g["cost"] else None

        # TACOS wylacznie z dni majacych obie strony rownania
        koszt_pasujacy = g.pop("_cost_matched")
        rekl_pasujaca = g.pop("_ad_sales_matched")
        g["tacos"] = (
            round(koszt_pasujacy / g["total_sales"] * 100, 2)
            if g["total_sales"] else None
        )
        g["ad_share"] = (
            round(rekl_pasujaca / g["total_sales"] * 100, 2)
            if g["total_sales"] else None
        )
        # Czy sprzedaz calkowita pokrywa caly miesiac - inaczej TACOS opisuje
        # tylko jego kawalek i trzeba to pokazac obok liczby
        g["total_complete"] = g["days_with_total"] >= g["days"] > 0
        g["total_sales"] = round(g["total_sales"], 2) or None
        g["cost"] = round(g["cost"], 2)
        g["ad_sales"] = round(g["ad_sales"], 2)
        g["complete"] = g["days"] >= dni_w_miesiacu
        g["days_in_month"] = dni_w_miesiacu
        out.append(g)

    return out


# --------------------------------------------------------------------------- #
# Stan konta - do naglowka strony glownej
# --------------------------------------------------------------------------- #


# Dzien uznajemy za niepelny, gdy ma mniej niz tyle rynkow, co najlepszy dzien
# w oknie. Prog jest luzny z rozmyslem: maly rynek potrafi miec dzien bez
# zadnego wydatku i nie przysyla wtedy wiersza - to nie jest brak danych.
# Dziura po wylaczonym komputerze wyglada inaczej: brakuje polowy rynkow naraz.
PROG_PELNEGO_DNIA = 0.7


def luki_w_danych(
    session: Session, model, profile_id: int | None = None, dni: int = 30
) -> dict[str, Any]:
    """Dni bez danych albo z polowa rynkow - w oknie ostatnich `dni`.

    Sprawdzenie samej najnowszej daty nie wystarcza i to nie jest teoria:
    po kilku dniach z wylaczonym komputerem panel pokazywal "aktualne", bo
    wczorajszy dzien byl na miejscu, a trzy dni w srodku okna mialy cztery
    rynki zamiast siedmiu. Wydatek za tydzien byl zanizony o 40% i nic tego
    nie zdradzalo.
    """
    wczoraj = dt.date.today() - dt.timedelta(days=1)
    od = wczoraj - dt.timedelta(days=dni - 1)

    conds = [model.date >= od, model.date <= wczoraj]
    if profile_id:
        conds.append(model.profile_id == profile_id)

    po_dniach = {
        d: n
        for d, n in session.execute(
            select(model.date, func.count(func.distinct(model.profile_id)))
            .where(*conds)
            .group_by(model.date)
        ).all()
    }
    if not po_dniach:
        return {"missing": [], "partial": [], "from": None}

    # Zaczynamy od pierwszego dnia, w ktorym cokolwiek mamy - wczesniejszych
    # brakow nie zglaszamy, bo to po prostu poczatek historii konta.
    poczatek = min(po_dniach)
    najlepszy = max(po_dniach.values())
    prog = max(1, round(najlepszy * PROG_PELNEGO_DNIA))

    brakujace, czesciowe = [], []
    dzien = poczatek
    while dzien <= wczoraj:
        ile = po_dniach.get(dzien)
        if ile is None:
            brakujace.append(dzien)
        elif ile < prog:
            czesciowe.append(dzien)
        dzien += dt.timedelta(days=1)

    wszystkie = sorted(brakujace + czesciowe)
    return {
        "missing": brakujace,
        "partial": czesciowe,
        "from": wszystkie[0] if wszystkie else None,
    }


def data_freshness(
    session: Session, profile_id: int | None = None
) -> dict[str, Any]:
    """Do kiedy siegaja dane i kiedy ostatnio cos pobralismy.

    Mierzymy DATE OSTATNIEGO DNIA Z DANYMI, a nie godzine ostatniego pobrania.
    Pobranie moze sie udac i nie przyniesc niczego nowego - wtedy "zsynchronizowano
    5 minut temu" jest prawda, a mimo to patrzymy na dane sprzed tygodnia.
    """
    wczoraj = dt.date.today() - dt.timedelta(days=1)

    def ostatni_dzien(model) -> dt.date | None:
        conds = []
        if profile_id:
            conds.append(model.profile_id == profile_id)
        return session.execute(select(func.max(model.date)).where(*conds)).scalar()

    def opis(model, etykieta: str, *dodatkowe) -> dict[str, Any]:
        do = ostatni_dzien(model)
        # Luki w srodku okna sa rownie wazne co data ostatniego dnia: jedno
        # i drugie znaczy, ze liczby na pulpicie sa zanizone. Patrzymy na
        # wszystkie tabele danego zrodla - raporty sa osobne i potrafia
        # zawiesc pojedynczo (zdarzyl sie dzien z pelnymi kampaniami
        # i raportem produktow tylko z trzech rynkow na siedem).
        dziury = set()
        for m in (model, *dodatkowe):
            luki = luki_w_danych(session, m, profile_id)
            dziury |= set(luki["missing"]) | set(luki["partial"])
        dziury = sorted(dziury)
        return {
            "label": etykieta,
            "through": do.isoformat() if do else None,
            "lag_days": (wczoraj - do).days if do else None,
            "gap_days": len(dziury),
            "gap_from": dziury[0].isoformat() if dziury else None,
            "gap_to": dziury[-1].isoformat() if dziury else None,
        }

    reklamy = opis(
        CampaignDaily, "Reklamy",
        PlacementDaily, TargetDaily, SearchTermDaily, ProductDaily,
    )
    sprzedaz = opis(TotalSalesDaily, "Sprzedaż całkowita", FulfillmentDaily)

    # Ostatni przebieg - do pokazania, kiedy panel ostatnio probowal
    conds = []
    if profile_id:
        conds.append(SyncRun.profile_id == profile_id)
    ostatni = session.execute(
        select(SyncRun.finished_at, SyncRun.status)
        .where(*conds, SyncRun.finished_at.is_not(None))
        .order_by(SyncRun.finished_at.desc())
        .limit(1)
    ).first()

    return {
        "sources": [reklamy, sprzedaz],
        "last_run": ostatni[0].isoformat() + "Z" if ostatni and ostatni[0] else None,
        "last_status": ostatni[1] if ostatni else None,
        "expected_through": wczoraj.isoformat(),
    }


def attention(
    session: Session, profile_id: int | None, start: dt.date, end: dt.date,
    limit: int = 2000,
) -> dict[str, Any]:
    """Co czeka na decyzje: plany w kolejce i sugestie w rozbiciu na portfolia.

    Rozbicie po portfoliach jest tu celowe - przy dwudziestu kilku portfoliach
    "124 sugestie" nie mowi nic, a "KTK: 61 za 1495 zl" mowi, od czego zaczac.
    """
    plany = pending_plans_summary(session, profile_id)

    sugestie = all_suggestions(session, profile_id, start, end, limit=limit)
    prog = settings.auto_queue_min_cost

    grupy: dict[int, dict[str, Any]] = {}
    krytyczne = 0
    for s in sugestie:
        koszt = s.get("cost") or 0.0
        if koszt >= prog:
            krytyczne += 1
        pid = s.get("portfolio_id") or 0
        g = grupy.setdefault(pid, {
            "portfolio_id": pid,
            "portfolio_name": s.get("portfolio_name") or "Bez portfolio",
            "count": 0, "cost": 0.0,
        })
        g["count"] += 1
        g["cost"] += koszt

    portfolia = sorted(grupy.values(), key=lambda g: -g["cost"])
    for g in portfolia:
        g["cost"] = round(g["cost"], 2)

    return {
        "plans": plany,
        "suggestions": {
            "total": len(sugestie),
            "critical": krytyczne,
            "threshold": prog,
            "by_portfolio": portfolia[:8],
        },
    }


def pending_plans_summary(
    session: Session, profile_id: int | None = None
) -> dict[str, Any]:
    """Ile planow czeka i czego dotycza - bez ladowania calej ich zawartosci."""
    # Bez warunku na expires_at: plany czekaja do zatwierdzenia albo usuniecia.
    # Ten filtr zostal tu po starej wersji i po dwoch godzinach kasowal licznik
    # na stronie glownej, chociaz plany dalej wisialy w kolejce.
    conds = [PendingPlan.status == "PENDING"]
    if profile_id:
        conds.append(PendingPlan.profile_id == profile_id)

    rows = session.execute(
        select(PendingPlan.kind, func.count(), func.sum(PendingPlan.item_count))
        .where(*conds)
        .group_by(PendingPlan.kind)
    ).all()

    return {
        "count": sum(r[1] for r in rows),
        "items": sum(int(r[2] or 0) for r in rows),
        "by_kind": [
            {"kind": k, "plans": n, "items": int(poz or 0)} for k, n, poz in rows
        ],
    }


# --------------------------------------------------------------------------- #
# Kampanie ograniczone budzetem
# --------------------------------------------------------------------------- #

# Amazon rzadko wydaje budzet co do grosza - przy 92% uznajemy dzien za
# domkniety. Przy 100% nie zlapalibysmy prawie nic.
BUDGET_CAP_RATIO = 0.92

# Budzet znamy tylko w wersji aktualnej - Amazon nie oddaje jego historii.
# Im dalej wstecz, tym wieksza szansa, ze porownujemy koszt ze stawka, ktora
# wtedy nie obowiazywala. Dwa tygodnie to kompromis miedzy tym ryzykiem
# a probka, ktorej mozna ufac.
BUDGET_WINDOW_DAYS = 14

# Ponizej tylu dni na limicie to przypadek - wyprzedaz, weekend - a nie wzorzec.
BUDGET_MIN_CAPPED_DAYS = 3


def budget_capped(
    session: Session, profile_id: int | None, start: dt.date, end: dt.date,
) -> dict[str, Any]:
    """Kampanie, ktore regularnie wyczerpuja dzienny budzet.

    Kampania na limicie przestaje sie wyswietlac przed koncem dnia. Jesli przy
    tym zarabia lepiej niz srednia konta, kazdy taki dzien to sprzedaz, ktora
    nie miala jak sie wydarzyc - i czego nie widac w zadnej innej tabeli,
    bo wszystkie metryki takiej kampanii wygladaja dobrze.

    Okno skracamy do ostatnich dwoch tygodni niezaleznie od wybranego zakresu,
    bo budzet znamy tylko w wersji dzisiejszej.
    """
    okno_start = max(start, end - dt.timedelta(days=BUDGET_WINDOW_DAYS - 1))

    na_limicie = case(
        (CampaignDaily.cost >= BUDGET_CAP_RATIO * Campaign.budget_amount, 1),
        else_=0,
    )

    rows = session.execute(
        select(
            Campaign.campaign_id,
            Campaign.name,
            Campaign.budget_amount,
            Campaign.portfolio_id,
            Campaign.targeting_type,
            func.count().label("days"),
            func.sum(na_limicie).label("days_capped"),
            *_sums(CampaignDaily),
        )
        .join(Campaign, Campaign.campaign_id == CampaignDaily.campaign_id)
        .where(
            _window(CampaignDaily, profile_id, okno_start, end),
            Campaign.state == "ENABLED",
            Campaign.budget_amount > 0,
        )
        .group_by(
            Campaign.campaign_id, Campaign.name, Campaign.budget_amount,
            Campaign.portfolio_id, Campaign.targeting_type,
        )
    ).mappings().all()

    nazwy_portfolii = {
        pid: nazwa
        for pid, nazwa in session.execute(
            select(Portfolio.portfolio_id, Portfolio.name)
        ).all()
    }

    # Srednia konta w tym samym oknie - punkt odniesienia dla rentownosci.
    wszystkie_koszty = sum(float(r["cost"] or 0) for r in rows)
    wszystkie_sprzedaze = sum(float(r["sales"] or 0) for r in rows)
    ref = (
        wszystkie_koszty / wszystkie_sprzedaze * 100
        if wszystkie_sprzedaze else 30.0
    )

    dni_okna = (end - okno_start).days + 1
    out: list[dict[str, Any]] = []

    for r in rows:
        dni_limitu = int(r["days_capped"] or 0)
        if dni_limitu < BUDGET_MIN_CAPPED_DAYS:
            continue

        row = _derive(dict(r))
        budzet = float(r["budget_amount"])
        koszt = float(r["cost"] or 0)
        dni = int(r["days"] or 0) or 1
        acos = row.get("acos")

        # Propozycja jest celowo ostrozna: jeden krok o 20%, nie skok do
        # "ile sie da". Przy kampanii na limicie nie wiadomo, gdzie konczy
        # sie oplacalny popyt - to widac dopiero po podniesieniu.
        propozycja = round(budzet * 1.2, 2) if acos is not None and acos < ref else None

        out.append({
            **row,
            "campaign_id": r["campaign_id"],
            "name": r["name"],
            "targeting_type": r["targeting_type"],
            "portfolio_id": r["portfolio_id"],
            "portfolio_name": nazwy_portfolii.get(r["portfolio_id"]),
            "budget": budzet,
            "days": dni,
            "days_capped": dni_limitu,
            "capped_share": round(dni_limitu / dni * 100, 0),
            "acos_ref": round(ref, 2),
            "beats_average": acos is not None and acos < ref,
            "suggested_budget": propozycja,
            "daily_cost": round(koszt / dni, 2),
        })

    # Najpierw te, ktore dobijaja najczesciej i zarabiaja najlepiej.
    out.sort(key=lambda x: (-(x["days_capped"]), x.get("acos") or 999))

    return {
        "window": {"start": okno_start.isoformat(), "end": end.isoformat(),
                   "days": dni_okna},
        "acos_ref": round(ref, 2),
        "cap_ratio": BUDGET_CAP_RATIO,
        "items": out,
    }


# --------------------------------------------------------------------------- #
# Wyszukiwarka produktow - do formularza nowej kampanii
# --------------------------------------------------------------------------- #


def catalog_search(
    session: Session, profile_id: int, q: str | None = None, limit: int = 40,
) -> list[dict[str, Any]]:
    """Produkty dostepne na tym rynku, z tytulem, miniatura i stanem.

    Formularz nowej kampanii wymagal wpisania SKU z pamieci. Kod
    `FBA-800-18-BIALY-5-NEW` nie mowi nic nawet osobie, ktora go nadala, wiec
    szukamy po tytule tak samo jak po kodzie.

    Zrodlem jest pelna baza ofert z Seller Central. Gdyby dla rynku jeszcze jej
    nie pobrano, cofamy sie do produktow kiedys reklamowanych - to nadal
    dziala, tylko lista jest wezsza.
    """
    marketplace = session.execute(
        select(Profile.marketplace_id).where(Profile.profile_id == profile_id)
    ).scalar()

    # Ile kampanii juz reklamuje dany SKU - podpowiada, czy produkt jest nowy
    # w koncie, czy tylko przenoszony.
    kampanie = {
        sku: int(ile)
        for sku, ile in session.execute(
            select(ProductAd.sku, func.count(func.distinct(ProductAd.campaign_id)))
            .where(ProductAd.profile_id == profile_id, ProductAd.state != "ARCHIVED")
            .group_by(ProductAd.sku)
        ).all()
        if sku
    }

    oferty = session.execute(
        select(Listing).where(Listing.marketplace_id == marketplace)
    ).scalars().all() if marketplace else []

    if oferty:
        pozycje = [{
            "sku": o.sku,
            "asin": o.asin,
            "title": o.title,
            "price": o.price,
            "quantity": o.quantity,
            "status": o.status,
            "fulfillment": o.fulfillment,
        } for o in oferty]
    else:
        # Zapas: rynek bez pobranych ofert. Wtedy znamy tylko to, co reklamowane.
        pozycje = [{
            "sku": r["sku"], "asin": r["asin"], "title": None,
            "price": None, "quantity": None, "status": None, "fulfillment": None,
        } for r in session.execute(
            select(ProductAd.sku, ProductAd.asin)
            .where(ProductAd.profile_id == profile_id, ProductAd.state != "ARCHIVED")
            .group_by(ProductAd.sku, ProductAd.asin)
        ).mappings().all()]

    katalog = catalog_etl.lookup(
        session, marketplace, [p["asin"] for p in pozycje if p["asin"]]
    )

    szukane = (q or "").strip().lower()
    out: list[dict[str, Any]] = []
    for p in pozycje:
        dane = katalog.get(p["asin"] or "") or {}
        # Tytul z oferty jest wlasny i zwykle dokladniejszy; katalog to zapas.
        tytul = p["title"] or dane.get("title")
        if szukane:
            # Szukamy po kodzie i po tytule naraz - uzytkownik pamieta jedno
            # albo drugie, rzadko oba.
            gdzie = " ".join(
                filter(None, [p["sku"], p["asin"], tytul, dane.get("brand")])
            ).lower()
            if szukane not in gdzie:
                continue
        out.append({
            "sku": p["sku"],
            "asin": p["asin"],
            "title": tytul,
            "image": dane.get("image"),
            "price": p["price"],
            "quantity": p["quantity"],
            "fulfillment": p["fulfillment"],
            # Stan znamy tylko dla wysylki wlasnej - przy FBA raport pokazuje
            # zero, chociaz towar lezy w magazynie Amazona.
            "out_of_stock": (
                p["quantity"] == 0
                and (p["fulfillment"] or "DEFAULT") == "DEFAULT"
            ),
            "campaigns": kampanie.get(p["sku"], 0),
        })

    # Najpierw te z tytulem - bez niego wiersz jest tylko kodem i mniej pomaga.
    out.sort(key=lambda p: (p["title"] is None, (p["title"] or p["sku"] or "").lower()))
    return out[:limit]
