"""Baza demonstracyjna - sztuczne konto reklamowe do pokazania panelu.

    python backend/scripts/seed_demo.py --reset

Wypelnia WSZYSTKIE moduly panelu: Sponsored Products, Brands i Display,
placementy (z B2B), frazy wyszukiwania, produkty, sprzedaz calkowita (TACOS),
podzial FBA/wlasna, portfolia, kolejke zatwierdzen, historie zmian i reguly.
Nie dotyka Amazona - zadne klucze nie sa potrzebne.

Fikcyjna marka: **Nordlicht Home** (tekstylia domowe). Cztery rynki, w tym UK
w funtach - zeby bylo widac, ze panel nie miesza walut.

Dane sa losowe, ale z ustalonym ziarnem: kazde uruchomienie daje te same
liczby. Sa w nich celowe "historie", ktore warto pokazac klientowi:

* SP_Bedding_Auto - fraza-perelka sprzedaje swietnie z kampanii
  automatycznej -> sugestia przejecia do manualnej.
* SP_Curtains_Broad - pierwszy keyword broad zbiera klikniecia bez sprzedazy
  -> sugestia wstrzymania; frazy z "smieciowa" koncowka -> wykluczenie.
* SP_Towels_Exact - wydaje caly budzet kazdego dnia przy dobrym ACOS
  -> sugestia podniesienia budzetu.
* Placement "gora wynikow" w SP_Bedding_Exact ma ACOS o polowe nizszy
  niz reszta -> sugestia podniesienia modyfikatora.
* UK sprzedaje drogo (ACOS ~40%) - widac dzialanie sufitu ACOS.

Przeznaczone do OSOBNEJ instancji (wlasny .env, wlasna baza, inne porty).
Z --reset kasuje wszystko, co jest w bazie wskazanej przez DATABASE_URL -
nie uruchamiaj tego na prawdziwej bazie.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.db import init_db, session_scope  # noqa: E402
from app.models import (  # noqa: E402
    AdGroup, Base, BidRecommendation, Campaign, CampaignDaily, CatalogItem,
    ChangeLog, FulfillmentDaily, Keyword, Listing, NegativeKeyword,
    NegativeTarget, PendingPlan, PlacementDaily, Portfolio, ProductAd,
    ProductDaily, Profile, Rule, RuleRun, SearchTermDaily, SyncRun, Target,
    TargetDaily, TotalSalesDaily,
)
from app.timeutil import utcnow  # noqa: E402

R = random.Random(2026)
DNI = 90
DZIS = dt.date.today()
KONIEC = DZIS - dt.timedelta(days=1)
START = KONIEC - dt.timedelta(days=DNI - 1)
BRAND = "Nordlicht Home"

# --------------------------------------------------------------------------- #
# Rynki
# --------------------------------------------------------------------------- #

RYNKI = [
    # profile_id, kraj, waluta, marketplace, strefa, mnoznik wolumenu, mnoznik ACOS
    (9100000001, "DE", "EUR", "A1PA6795UKMFR9", "Europe/Berlin", 1.00, 1.00),
    (9100000002, "FR", "EUR", "A13V1IB3VIYZZH", "Europe/Paris", 0.55, 1.35),
    (9100000003, "IT", "EUR", "APJ6JRA9NG5V4", "Europe/Rome", 0.45, 1.15),
    (9100000004, "UK", "GBP", "A1F83G8C2ARO7P", "Europe/London", 0.60, 2.20),
]

SLOWA = {
    "DE": {
        "bedding": ["bettwäsche 200x200", "bettwäsche baumwolle", "bettbezug 135x200", "bettwäsche set", "bettwäsche grau", "kissenbezug 80x80"],
        "curtains": ["vorhang", "vorhänge blickdicht", "gardinen wohnzimmer", "verdunkelungsvorhang", "vorhang leinen", "ösenvorhang"],
        "towels": ["handtuch set", "badetuch baumwolle", "handtücher grau", "duschtuch 70x140", "frottee handtuch"],
        "brand": ["nordlicht home", "nordlicht bettwäsche", "nordlicht handtuch"],
        "smieci": [" selber nähen", " gebraucht", " ikea", " anleitung", " kinder", " 3d", " billig"],
        "perelki": ["bettwäsche 200x200 baumwolle", "handtuch set 4 teilig grau", "vorhang leinen beige 245"],
    },
    "FR": {
        "bedding": ["housse de couette 220x240", "parure de lit coton", "housse de couette 140x200", "linge de lit", "taie d'oreiller 65x65"],
        "curtains": ["rideau", "rideau occultant", "rideaux salon", "rideau lin", "rideau à oeillets"],
        "towels": ["serviette de bain", "serviette coton", "drap de bain 70x140", "serviettes de toilette"],
        "brand": ["nordlicht home", "nordlicht linge de lit"],
        "smieci": [" ikea", " occasion", " tuto couture", " enfant", " disney"],
        "perelki": ["housse de couette 220x240 coton lavé", "serviette de bain 100x150 gris"],
    },
    "IT": {
        "bedding": ["copripiumino matrimoniale", "lenzuola cotone", "copripiumino 200x200", "set letto", "federe cuscino"],
        "curtains": ["tende", "tende oscuranti", "tende soggiorno", "tenda lino", "tende con occhielli"],
        "towels": ["asciugamani set", "telo bagno cotone", "asciugamani grigi"],
        "brand": ["nordlicht home", "nordlicht lenzuola"],
        "smieci": [" ikea", " usato", " fai da te", " bambini", " economici"],
        "perelki": ["copripiumino matrimoniale cotone lavato grigio", "asciugamani set 4 pezzi"],
    },
    "UK": {
        "bedding": ["duvet cover king", "cotton bedding set", "duvet cover double", "bed linen", "pillowcases 50x75"],
        "curtains": ["curtains", "blackout curtains", "living room curtains", "linen curtains", "eyelet curtains"],
        "towels": ["towel set", "bath towel cotton", "grey towels", "bath sheet"],
        "brand": ["nordlicht home", "nordlicht bedding"],
        "smieci": [" ikea", " second hand", " how to sew", " kids", " dunelm"],
        "perelki": ["duvet cover king size cotton grey", "bath towel set 4 piece"],
    },
}

TYTULY = {
    "bedding": {
        "DE": "Nordlicht Home Bettwäsche {r} cm, 100% Baumwolle, {k}",
        "FR": "Nordlicht Home Housse de couette {r} cm, 100% coton, {k}",
        "IT": "Nordlicht Home Copripiumino {r} cm, 100% cotone, {k}",
        "UK": "Nordlicht Home Duvet Cover Set {r} cm, 100% Cotton, {k}",
    },
    "curtains": {
        "DE": "Nordlicht Home Vorhang blickdicht {r} cm mit Ösen, {k}",
        "FR": "Nordlicht Home Rideau occultant {r} cm à oeillets, {k}",
        "IT": "Nordlicht Home Tenda oscurante {r} cm con occhielli, {k}",
        "UK": "Nordlicht Home Blackout Curtain {r} cm Eyelet, {k}",
    },
    "towels": {
        "DE": "Nordlicht Home Handtuch-Set 4-teilig {r} cm Frottee, {k}",
        "FR": "Nordlicht Home Set de serviettes 4 pièces {r} cm, {k}",
        "IT": "Nordlicht Home Set asciugamani 4 pezzi {r} cm, {k}",
        "UK": "Nordlicht Home Towel Set 4 Piece {r} cm Cotton, {k}",
    },
}
KOLORY = {"DE": ["Grau", "Weiß", "Beige"], "FR": ["Gris", "Blanc", "Beige"],
          "IT": ["Grigio", "Bianco", "Beige"], "UK": ["Grey", "White", "Beige"]}
ROZMIARY = {"bedding": ["135x200", "155x220", "200x200", "200x220"], "curtains": ["140x175", "140x245", "245x140"], "towels": ["50x100", "70x140"]}
CENY = {"bedding": 34.9, "curtains": 24.9, "towels": 29.9}
LINIE = [("bedding", "Bedding", 0.5), ("curtains", "Curtains", 0.3), ("towels", "Towels", 0.2)]
KONKURENCJA = ["B0C1K7QX2M", "B09XR4T8LP", "B0BFK3M9ZD", "B08YV2P6HN", "B0D3M8Q1RT"]

METRYKI_KLUCZE = ("impressions", "clicks", "cost", "orders", "sales", "units",
                  "orders_same_sku", "sales_same_sku", "sales_other_sku", "units_other_sku")

# --------------------------------------------------------------------------- #
# Pomocnicze
# --------------------------------------------------------------------------- #

_licznik = [0]


def nowe_id() -> str:
    """Identyfikatory w stylu Amazona - dlugie liczby, rosnace."""
    _licznik[0] += 1
    return str(500000000000000 + _licznik[0] * 7919)


def asin_z(nazwa: str) -> str:
    """Stabilny, wygladajacy na prawdziwy ASIN - z tekstu, nie z losowania."""
    h = 0
    for ch in nazwa:
        h = (h * 131 + ord(ch)) % (36 ** 7)
    litery = "0123456789ABCDEFGHIJKLMNPQRSTUVWXYZ"   # bez O, jak w ASIN-ach
    out = ""
    for _ in range(7):
        out = litery[h % 35] + out
        h //= 35
    return "B0D" + out


def sezon(d: dt.date) -> float:
    """Tydzien: weekend slabszy; lekki trend wzrostowy; szum."""
    w = {0: 1.05, 1: 1.08, 2: 1.06, 3: 1.02, 4: 0.95, 5: 0.82, 6: 0.85}[d.weekday()]
    t = 1.0 + 0.15 * (d - START).days / DNI
    return w * t * R.uniform(0.85, 1.15)


def metryki(kliki_bazowe: float, cpc: float, cvr: float, aov: float, d: dt.date) -> dict:
    """Dzienne metryki z rozkladow - kazdy dzien inny, ale spojny."""
    kliki = max(0, int(R.gauss(kliki_bazowe * sezon(d), kliki_bazowe * 0.25)))
    ctr = R.uniform(0.004, 0.012)
    impr = int(kliki / ctr) if kliki else int(R.uniform(50, 400))
    koszt = round(kliki * cpc * R.uniform(0.85, 1.15), 2)
    zam = sum(1 for _ in range(kliki) if R.random() < cvr)
    sztuki = zam + sum(1 for _ in range(zam) if R.random() < 0.15)
    sprz = round(sztuki * aov * R.uniform(0.9, 1.1), 2) if zam else 0.0
    same = sum(1 for _ in range(zam) if R.random() < 0.8)
    same_sales = round(sprz * (same / zam), 2) if zam else 0.0
    return {
        "impressions": impr, "clicks": kliki, "cost": koszt, "orders": zam,
        "sales": sprz, "units": sztuki, "orders_same_sku": same,
        "sales_same_sku": same_sales, "sales_other_sku": round(sprz - same_sales, 2),
        "units_other_sku": (sztuki - same) if zam else 0,
        "tos_impression_share": round(R.uniform(0.05, 0.45), 3),
    }


def suma(rows: list[dict]) -> dict:
    out = {k: 0 for k in METRYKI_KLUCZE}
    for r in rows:
        for k in out:
            out[k] += r.get(k) or 0
    for k in ("cost", "sales", "sales_same_sku", "sales_other_sku"):
        out[k] = round(out[k], 2)
    out["tos_impression_share"] = round(R.uniform(0.1, 0.4), 3)
    return out


def czesc(s: dict, u: float) -> dict:
    """Udzial u z sumy s - liczby calkowite zaokraglone, kwoty do groszy."""
    out = {}
    for k in METRYKI_KLUCZE:
        v = s[k]
        out[k] = round(v * u, 2) if isinstance(v, float) else int(v * u)
    out["tos_impression_share"] = None
    return out


def wstaw(session, model, rows: list[dict], porcja: int = 5000) -> None:
    for i in range(0, len(rows), porcja):
        session.bulk_insert_mappings(model, rows[i:i + porcja])


# --------------------------------------------------------------------------- #
# Generator jednego rynku
# --------------------------------------------------------------------------- #

def rynek(session, pid: int, kraj: str, waluta: str, mp: str, strefa: str,
          wolumen: float, acos_mn: float, stat: dict) -> None:
    slowa = SLOWA[kraj]
    dni = [START + dt.timedelta(days=i) for i in range(DNI)]
    kurs = 0.86 if waluta == "GBP" else 1.0

    session.add(Profile(
        profile_id=pid, country_code=kraj, currency_code=waluta, timezone=strefa,
        marketplace_id=mp, account_name=BRAND, account_type="seller",
        entity_id=f"ENTITY{pid}", daily_budget=None, is_active=True,
    ))
    session.flush()

    # ---- produkty: katalog + oferty ----
    produkty: dict[str, list[tuple[str, str, str]]] = {}
    catalog, listings = [], []
    for linia, _, _ in LINIE:
        produkty[linia] = []
        for roz in ROZMIARY[linia]:
            for kolor in KOLORY[kraj]:
                sku = f"NH-{linia[:3].upper()}-{roz}-{kolor[:3].upper()}"
                asin = asin_z(f"{linia}{roz}{kolor}")
                tytul = TYTULY[linia][kraj].format(r=roz, k=kolor)
                produkty[linia].append((sku, asin, tytul))
                catalog.append({"asin": asin, "marketplace_id": mp, "title": tytul, "brand": BRAND,
                                "image_small": None, "image_main": None, "fetched_at": utcnow()})
                listings.append({"sku": sku, "marketplace_id": mp, "asin": asin, "title": tytul,
                                 "price": round(CENY[linia] * kurs * R.uniform(0.9, 1.2), 2),
                                 "quantity": R.randint(0, 900), "status": "Active",
                                 "fulfillment": R.choice(["AMAZON_EU", "AMAZON_EU", "DEFAULT"]),
                                 "listing_id": nowe_id(), "opened_on": None, "asin_inferred": False,
                                 "fetched_at": utcnow()})
    wstaw(session, CatalogItem, catalog)
    wstaw(session, Listing, listings)

    # ---- portfolia ----
    portfele = {}
    for linia, nazwa, _ in LINIE:
        pf = int(nowe_id()[-12:])
        portfele[linia] = pf
        session.add(Portfolio(portfolio_id=pf, profile_id=pid, name=nazwa, state="enabled",
                              budget_amount=None, budget_policy=None))
    session.flush()

    # ---- kampanie ----
    # klucz, nazwa, produkt, typ, linia, budzet, cpc, cvr, kliki/dzien, placement_bidding, strategia
    plan_kampanii = [
        ("bed_auto", "SP_Bedding_Auto", "SP", "AUTO", "bedding", 40, 0.42, 0.085, 70,
         [{"placement": "PLACEMENT_TOP", "percentage": 15}, {"placement": "PLACEMENT_PRODUCT_PAGE", "percentage": 0}], "LEGACY_FOR_SALES"),
        ("bed_exact", "SP_Bedding_Exact", "SP", "MANUAL", "bedding", 60, 0.55, 0.11, 90,
         [{"placement": "PLACEMENT_TOP", "percentage": 25}, {"placement": "PLACEMENT_PRODUCT_PAGE", "percentage": 5}, {"placement": "SITE_AMAZON_BUSINESS", "percentage": 10}], "LEGACY_FOR_SALES"),
        ("cur_broad", "SP_Curtains_Broad", "SP", "MANUAL", "curtains", 35, 0.48, 0.055, 60,
         [{"placement": "PLACEMENT_TOP", "percentage": 0}, {"placement": "PLACEMENT_PRODUCT_PAGE", "percentage": 0}], "AUTO_FOR_SALES"),
        ("cur_auto", "SP_Curtains_Auto", "SP", "AUTO", "curtains", 25, 0.38, 0.07, 40,
         [{"placement": "PLACEMENT_TOP", "percentage": 10}, {"placement": "PLACEMENT_PRODUCT_PAGE", "percentage": 0}], "LEGACY_FOR_SALES"),
        ("tow_exact", "SP_Towels_Exact", "SP", "MANUAL", "towels", 20, 0.36, 0.12, 55,
         [{"placement": "PLACEMENT_TOP", "percentage": 30}, {"placement": "PLACEMENT_PRODUCT_PAGE", "percentage": 0}], "LEGACY_FOR_SALES"),
        ("brand", "SP_Brand_Defense", "SP", "MANUAL", "bedding", 15, 0.22, 0.20, 25,
         [{"placement": "PLACEMENT_TOP", "percentage": 50}, {"placement": "PLACEMENT_PRODUCT_PAGE", "percentage": 0}], "LEGACY_FOR_SALES"),
        ("comp", "SP_Competitors_ASIN", "SP", "MANUAL", "bedding", 30, 0.62, 0.05, 45,
         [{"placement": "PLACEMENT_TOP", "percentage": 0}, {"placement": "PLACEMENT_PRODUCT_PAGE", "percentage": 20}], "LEGACY_FOR_SALES"),
        ("sb_bed", "SB_Bedding_Brand", "SB", "MANUAL", "bedding", 30, 0.70, 0.06, 35,
         [{"placement": "TOP_OF_SEARCH", "percentage": 0}, {"placement": "DETAIL_PAGE", "percentage": 0}], "MAXIMIZE_IMMEDIATE_SALES"),
        ("sd_views", "SD_Retargeting_Views", "SD", "T00020", "bedding", 20, 0.30, 0.045, 30, None, "CPC"),
    ]

    kampanie: dict[str, dict] = {}
    for klucz, nazwa, prod, typ, linia, budzet, cpc, cvr, kliki, pb, strat in plan_kampanii:
        cid = nowe_id()
        session.add(Campaign(
            campaign_id=cid, profile_id=pid, ad_product=prod, portfolio_id=portfele[linia],
            name=nazwa, state="ENABLED", targeting_type=typ,
            budget_amount=round(budzet * kurs, 2), budget_type="DAILY",
            bidding_strategy=strat, placement_bidding=pb,
            start_date=START - dt.timedelta(days=R.randint(60, 400)), end_date=None,
        ))
        kampanie[klucz] = {"id": cid, "nazwa": nazwa, "prod": prod, "typ": typ, "linia": linia,
                           "budzet": round(budzet * kurs, 2), "cpc": cpc * kurs,
                           "cvr": cvr * 0.85 / acos_mn, "kliki": kliki * wolumen}
    session.flush()

    # ---- grupy, keywordy, targety, reklamy, negatywy ----
    targety: list[dict] = []
    reklamy: list[dict] = []
    for klucz, k in kampanie.items():
        gid = nowe_id()
        k["gid"] = gid
        session.add(AdGroup(ad_group_id=gid, campaign_id=k["id"], profile_id=pid, ad_product=k["prod"],
                            name=k["nazwa"].split("_", 1)[1].replace("_", " "), state="ENABLED",
                            default_bid=round(k["cpc"] * 1.1, 2)))
        session.flush()
        linia = k["linia"]
        aov = CENY[linia] * kurs * 1.15

        if k["prod"] in ("SP", "SD"):
            for sku, asin, _ in R.sample(produkty[linia], k=min(6, len(produkty[linia]))):
                aid = nowe_id()
                session.add(ProductAd(ad_id=aid, ad_group_id=gid, campaign_id=k["id"], profile_id=pid,
                                      ad_product=k["prod"], asin=asin, sku=sku,
                                      state="ENABLED" if R.random() < 0.9 else "PAUSED"))
                reklamy.append({"id": aid, "cid": k["id"], "gid": gid, "prod": k["prod"], "asin": asin, "sku": sku,
                                "kliki": k["kliki"] / 6, "cpc": k["cpc"], "cvr": k["cvr"], "aov": aov})

        if k["typ"] == "AUTO":
            for typ_auto, txt, mn in (("QUERY_HIGH_REL_MATCHES", "close-match", 1.4),
                                      ("QUERY_BROAD_REL_MATCHES", "loose-match", 0.8),
                                      ("ASIN_SUBSTITUTE_RELATED", "substitutes", 0.6),
                                      ("ASIN_ACCESSORY_RELATED", "complements", 0.3)):
                tid = nowe_id()
                bid = round(k["cpc"] * mn * 0.9, 2)
                session.add(Target(target_id=tid, ad_group_id=gid, campaign_id=k["id"], profile_id=pid, ad_product="SP",
                                   expression_type="AUTO", expression=[{"type": typ_auto}],
                                   resolved_expression=json.dumps([{"type": typ_auto}]), state="ENABLED", bid=bid))
                targety.append({"id": tid, "cid": k["id"], "gid": gid, "prod": "SP", "kind": "TARGET", "text": txt,
                                "match": "TARGETING_EXPRESSION_PREDEFINED", "bid": bid, "cpc": k["cpc"] * mn,
                                "cvr": k["cvr"] * (1.2 if mn > 1 else 0.7), "kliki": k["kliki"] * mn / 3.1,
                                "aov": aov, "auto": True, "linia": linia})
        elif klucz == "comp":
            for asin in KONKURENCJA:
                tid = nowe_id()
                bid = round(k["cpc"], 2)
                session.add(Target(target_id=tid, ad_group_id=gid, campaign_id=k["id"], profile_id=pid, ad_product="SP",
                                   expression_type="MANUAL", expression=[{"type": "ASIN_SAME_AS", "value": asin}],
                                   resolved_expression=json.dumps([{"type": "ASIN_SAME_AS", "value": asin}]), state="ENABLED", bid=bid))
                targety.append({"id": tid, "cid": k["id"], "gid": gid, "prod": "SP", "kind": "TARGET", "text": f"ASIN {asin}",
                                "match": "TARGETING_EXPRESSION", "bid": bid, "cpc": k["cpc"] * R.uniform(0.8, 1.3),
                                "cvr": k["cvr"] * R.uniform(0.5, 1.5), "kliki": k["kliki"] / 5, "aov": aov,
                                "auto": False, "linia": linia})
        elif k["prod"] == "SD":
            tid = nowe_id()
            session.add(Target(target_id=tid, ad_group_id=gid, campaign_id=k["id"], profile_id=pid, ad_product="SD",
                               expression_type="MANUAL", expression=[{"type": "views", "value": "similarProduct"}],
                               resolved_expression="views similarProduct 30 dni", state="ENABLED", bid=round(k["cpc"], 2)))
            targety.append({"id": tid, "cid": k["id"], "gid": gid, "prod": "SD", "kind": "TARGET",
                            "text": "Oglądali podobne produkty (30 dni)", "match": "TARGETING_EXPRESSION",
                            "bid": round(k["cpc"], 2), "cpc": k["cpc"], "cvr": k["cvr"], "kliki": k["kliki"],
                            "aov": aov, "auto": False, "linia": linia})
        else:
            lista = slowa["brand"] if klucz == "brand" else slowa[linia]
            if "Exact" in k["nazwa"] or klucz == "brand":
                matche = ["EXACT"]
            elif "Broad" in k["nazwa"]:
                matche = ["BROAD", "PHRASE"]
            else:
                matche = ["EXACT", "PHRASE"]
            for i, kw in enumerate(lista):
                for m in matche:
                    kid = nowe_id()
                    bid = round(k["cpc"] * R.uniform(0.8, 1.3), 2)
                    session.add(Keyword(keyword_id=kid, ad_group_id=gid, campaign_id=k["id"], profile_id=pid,
                                        ad_product=k["prod"], keyword_text=kw, match_type=m, state="ENABLED", bid=bid))
                    # pierwszy keyword broad w zaslonach: klika, nie sprzedaje
                    zly = klucz == "cur_broad" and i == 0 and m == "BROAD"
                    targety.append({"id": kid, "cid": k["id"], "gid": gid, "prod": k["prod"], "kind": "KEYWORD", "text": kw,
                                    "match": m, "bid": bid, "cpc": k["cpc"] * R.uniform(0.7, 1.4),
                                    "cvr": 0.004 if zly else k["cvr"] * R.uniform(0.6, 1.5) / (1 + i * 0.12),
                                    "kliki": k["kliki"] * (1.6 if zly else 1.0) / (len(lista) * len(matche)) * (2.0 if i == 0 else 1.0),
                                    "aov": aov, "auto": False, "linia": linia})
                    if i < 2 and R.random() < 0.5:
                        session.add(BidRecommendation(profile_id=pid, entity_type="KEYWORD", entity_id=kid,
                                                      low=round(bid * 0.7, 2), median=round(bid * 1.05, 2),
                                                      high=round(bid * 1.4, 2), theme="CONVERSION_OPPORTUNITIES",
                                                      fetched_at=utcnow()))
        if k["prod"] == "SP" and klucz in ("bed_auto", "cur_auto", "bed_exact"):
            for s in slowa["smieci"][:3]:
                session.add(NegativeKeyword(keyword_id=nowe_id(), ad_group_id=gid, campaign_id=k["id"], profile_id=pid,
                                            level="AD_GROUP", keyword_text=slowa[linia][0] + s,
                                            match_type="NEGATIVE_PHRASE", state="ENABLED"))
            session.add(NegativeTarget(target_id=nowe_id(), ad_group_id=gid, campaign_id=k["id"], profile_id=pid,
                                       level="AD_GROUP", expression_type="ASIN_SAME_AS",
                                       value=f"ASIN_SAME_AS {KONKURENCJA[-1]}", state="ENABLED"))
    session.flush()

    # ---- metryki dzienne ----
    po_id = {k["id"]: k for k in kampanie.values()}
    camp_rows, plc_rows, tgt_rows, st_rows, prod_rows = [], [], [], [], []
    for d in dni:
        per_camp: dict[str, list[dict]] = {}
        for t in targety:
            m = metryki(t["kliki"], t["cpc"], t["cvr"], t["aov"], d)
            tgt_rows.append({"date": d, "profile_id": pid, "campaign_id": t["cid"], "ad_group_id": t["gid"],
                             "target_id": t["id"], "ad_product": t["prod"], "target_kind": t["kind"],
                             "target_text": t["text"], "match_type": t["match"], "bid": t["bid"], **m})
            per_camp.setdefault(t["cid"], []).append(m)

            if t["prod"] == "SP" and m["clicks"] and R.random() < 0.7:
                if t["auto"]:
                    frazy = [R.choice(slowa[t["linia"]])]
                    if R.random() < 0.5:
                        frazy.append(R.choice(slowa["perelki"]))
                    if R.random() < 0.4:
                        frazy.append(R.choice(slowa[t["linia"]]) + R.choice(slowa["smieci"]))
                    if R.random() < 0.06:
                        frazy.append(R.choice(KONKURENCJA).lower())
                elif t["kind"] == "KEYWORD":
                    frazy = [t["text"]]
                    if t["match"] != "EXACT" and R.random() < 0.5:
                        frazy.append(t["text"] + R.choice(slowa["smieci"]))
                else:
                    frazy = [R.choice(KONKURENCJA).lower()]
                for fr in dict.fromkeys(frazy):
                    smiec = fr.startswith("b0") or any(s.strip() in fr for s in slowa["smieci"])
                    perelka = fr in slowa["perelki"]
                    cvr = (0.03 if fr.startswith("b0") else 0.002) if smiec else (0.16 if perelka else t["cvr"])
                    mm = metryki(max(1.0, t["kliki"] / len(frazy)), t["cpc"], cvr, t["aov"], d)
                    st_rows.append({"date": d, "profile_id": pid, "campaign_id": t["cid"], "ad_product": "SP",
                                    "ad_group_id": t["gid"], "target_id": t["id"], "search_term": fr,
                                    "target_text": t["text"], "match_type": t["match"], **mm})

        for cid, rows in per_camp.items():
            s = suma(rows)
            kamp = po_id[cid]
            if kamp["nazwa"] == "SP_Towels_Exact":
                s["cost"] = round(kamp["budzet"] * R.uniform(0.985, 1.0), 2)
            camp_rows.append({"date": d, "profile_id": pid, "campaign_id": cid, "ad_product": kamp["prod"], **s})
            if kamp["prod"] == "SP":
                for pl, u in (("PLACEMENT_TOP", 0.35), ("PLACEMENT_REST_OF_SEARCH", 0.45), ("PLACEMENT_PRODUCT_PAGE", 0.20)):
                    r = czesc(s, u)
                    if kamp["nazwa"] == "SP_Bedding_Exact" and pl == "PLACEMENT_TOP":
                        r["orders"] = int(r["orders"] * 1.8); r["sales"] = round(r["sales"] * 1.9, 2); r["units"] = int(r["units"] * 1.8)
                    elif pl == "PLACEMENT_PRODUCT_PAGE":
                        r["orders"] = int(r["orders"] * 0.6); r["sales"] = round(r["sales"] * 0.6, 2)
                    plc_rows.append({"date": d, "profile_id": pid, "campaign_id": cid, "placement": pl, **r})

        for a in reklamy:
            if R.random() < 0.85:
                m = metryki(a["kliki"], a["cpc"], a["cvr"], a["aov"], d)
                prod_rows.append({"date": d, "profile_id": pid, "campaign_id": a["cid"], "ad_product": a["prod"],
                                  "ad_group_id": a["gid"], "asin": a["asin"], "sku": a["sku"], **m})

    wstaw(session, TargetDaily, tgt_rows)
    wstaw(session, SearchTermDaily, st_rows)
    wstaw(session, CampaignDaily, camp_rows)
    wstaw(session, PlacementDaily, plc_rows)
    wstaw(session, ProductDaily, prod_rows)

    # ---- sprzedaz calkowita i podzial FBA ----
    ts_rows, fb_rows = [], []
    sprzedaz_dnia = {}
    for r in camp_rows:
        sprzedaz_dnia[r["date"]] = sprzedaz_dnia.get(r["date"], 0.0) + r["sales"]
    for d in dni:
        total = round(sprzedaz_dnia.get(d, 0.0) / R.uniform(0.28, 0.36), 2)
        zam = int(total / (CENY["bedding"] * kurs * 1.1))
        ts_rows.append({"date": d, "profile_id": pid, "asin": None, "sku": None, "sales": total, "units": int(zam * 1.15),
                        "orders": zam, "sessions": int(zam / R.uniform(0.04, 0.07)), "source": "SPAPI", "created_at": utcnow()})
        for linia, _, udz in LINIE:
            for sku, asin, _ in produkty[linia][:4]:
                cz = total * udz / 4 * R.uniform(0.5, 1.5)
                ts_rows.append({"date": d, "profile_id": pid, "asin": asin, "sku": sku, "sales": round(cz, 2),
                                "units": max(1, int(cz / (CENY[linia] * kurs))),
                                "orders": max(1, int(cz / (CENY[linia] * kurs) * 0.9)),
                                "sessions": None, "source": "SPAPI", "created_at": utcnow()})
        fba = R.uniform(0.55, 0.68)
        for ch, u in (("FBA", fba), ("MERCHANT", 1 - fba)):
            fb_rows.append({"date": d, "profile_id": pid, "channel": ch, "sales": round(total * u, 2),
                            "units": int(zam * 1.15 * u), "orders": int(zam * u), "currency": waluta,
                            "fetched_at": utcnow()})
    wstaw(session, TotalSalesDaily, ts_rows)
    wstaw(session, FulfillmentDaily, fb_rows)

    # ---- kolejka zatwierdzen i historia zmian ----
    kw_targety = [t for t in targety if t["kind"] == "KEYWORD" and t["prod"] == "SP"]
    teraz = utcnow()

    def plan(kind, summary, payload, planned, status, dni_temu, source="SUGGESTION"):
        created = teraz - dt.timedelta(days=dni_temu, hours=R.randint(1, 9))
        session.add(PendingPlan(id=str(uuid.uuid4()), profile_id=pid, kind=kind, summary=summary,
                                payload={**payload, "planned": planned, "source": source, "rule_id": None},
                                item_count=len(planned), status=status, created_at=created,
                                expires_at=created + dt.timedelta(minutes=120),
                                applied_at=(created + dt.timedelta(minutes=R.randint(3, 40))) if status == "APPLIED" else None))
        if status == "APPLIED":
            for p in planned:
                session.add(ChangeLog(ts=created + dt.timedelta(minutes=5), profile_id=pid, entity_type=p["entity_type"],
                                      entity_id=str(p["id"]), field=p["field"], old_value=str(p.get("old")),
                                      new_value=str(p.get("new")), source=source, rule_id=None, status="APPLIED", error=None))

    wyb = R.sample(kw_targety, 3)
    plan("bids", "Zmiana stawek dla 3 pozycji", {},
         [{"id": t["id"], "kind": "KEYWORD", "entity_type": "KEYWORD", "field": "bid", "old": t["bid"],
           "new": round(t["bid"] * 0.88, 2), "reason": f"ACOS powyżej odniesienia: {t['text']}"} for t in wyb], "PENDING", 0)
    auto, cel = kampanie["bed_auto"], kampanie["bed_exact"]
    stawka = round(cel["cpc"] * 1.15, 2)
    plan("harvest", "Dodanie 1 keywordow", {},
         [{"id": cel["gid"], "entity_type": "KEYWORD", "field": "new_keyword", "old": None,
           "new": f"{slowa['perelki'][0]} (EXACT)",
           "new_label": f"{slowa['perelki'][0]} · exact · stawka {stawka} · z {auto['nazwa']} → do {cel['nazwa']}",
           "reason": "Fraza z kampanii automatycznej sprzedaje dobrze",
           "item": {"campaignId": cel["id"], "adGroupId": cel["gid"], "keywordText": slowa["perelki"][0],
                    "matchType": "EXACT", "bid": stawka}, "zrodla": 1}], "PENDING", 0)
    cb = kampanie["cur_broad"]
    plan("negatives", "Dodanie 2 negatywow (AD_GROUP)", {"level": "AD_GROUP"},
         [{"id": cb["gid"], "entity_type": "NEGATIVE", "field": "keywordText", "old": None,
           "new": f"{slowa['curtains'][0]}{s} (NEGATIVE_PHRASE)", "reason": "Klika, nie sprzedaje",
           "item": {"campaignId": cb["id"], "adGroupId": cb["gid"], "keywordText": slowa["curtains"][0] + s,
                    "matchType": "NEGATIVE_PHRASE"}} for s in slowa["smieci"][:2]], "PENDING", 0)
    for dni_temu in (2, 5, 9, 14, 21, 30):
        wyb = R.sample(kw_targety, R.randint(1, 4))
        plan("bids", f"Zmiana stawek dla {len(wyb)} pozycji", {},
             [{"id": t["id"], "kind": "KEYWORD", "entity_type": "KEYWORD", "field": "bid",
               "old": round(t["bid"] * R.uniform(0.85, 1.15), 2), "new": t["bid"]} for t in wyb],
             "APPLIED", dni_temu, source=R.choice(["SUGGESTION", "MANUAL", "RULE"]))
    tw = kampanie["tow_exact"]
    plan("budgets", "Zmiana budzetu dla 1 kampanii", {},
         [{"id": tw["id"], "entity_type": "CAMPAIGN", "field": "budget", "old": round(tw["budzet"] * 0.8, 2), "new": tw["budzet"]}],
         "APPLIED", 12)
    plan("state", "Wstrzymanie 1 pozycji (KEYWORD)", {"entity_type": "KEYWORD", "state": "PAUSED"},
         [{"id": kw_targety[-1]["id"], "entity_type": "KEYWORD", "field": "state", "old": "ENABLED", "new": "PAUSED"}],
         "APPLIED", 18, source="MANUAL")

    stat["kampanie"] += len(kampanie)
    stat["targety"] += len(targety)
    stat["metryki"] += len(tgt_rows) + len(st_rows) + len(camp_rows) + len(plc_rows) + len(prod_rows)


# --------------------------------------------------------------------------- #
# Reguly i przebiegi synchronizacji (wspolne dla konta)
# --------------------------------------------------------------------------- #

def reguly(session) -> None:
    teraz = utcnow()
    definicje = [
        ("Wykluczaj frazy bez sprzedaży",
         "Fraza z 15+ kliknięciami i zerem zamówień w 30 dni trafia jako negatyw phrase.",
         "SEARCH_TERM", 30, [{"metric": "clicks", "op": ">=", "value": 15}, {"metric": "orders", "op": "==", "value": 0}],
         {"type": "ADD_NEGATIVE_KEYWORD", "match_type": "NEGATIVE_PHRASE"}),
        ("Stawki do ACOS 25%",
         "Keyword z 5+ zamówieniami: stawka przeliczona tak, żeby ACOS zbliżył się do 25% (krok max 15%).",
         "TARGET", 30, [{"metric": "orders", "op": ">=", "value": 5}],
         {"type": "SET_BID_TARGET_ACOS", "target_acos": 25, "step": 15}),
        ("Góra wyników, gdy się opłaca",
         "Placement z ACOS poniżej 15% i 10+ zamówieniami dostaje +10 pkt modyfikatora.",
         "PLACEMENT", 30, [{"metric": "acos", "op": "<", "value": 15}, {"metric": "orders", "op": ">=", "value": 10}],
         {"type": "SET_PLACEMENT_MODIFIER", "step": 10}),
    ]
    for nazwa, opis, scope, lb, cond, act in definicje:
        r = Rule(name=nazwa, description=opis, enabled=True, profile_id=None, scope=scope, lookback_days=lb,
                 filters=None, conditions=cond, action=act, limits={"max_items": 25}, schedule_hours=6,
                 last_run_at=teraz - dt.timedelta(hours=R.randint(1, 5)))
        session.add(r)
        session.flush()
        for dni_temu in (0, 1, 2, 3):
            session.add(RuleRun(rule_id=r.id, ts=teraz - dt.timedelta(days=dni_temu, hours=R.randint(0, 6)),
                                profile_id=None, evaluated=R.randint(200, 900), matched=R.randint(0, 12),
                                plan_id=None, status="OK", detail="sugestie zapisane, kolejka bez zmian"))
    for dni_temu in range(0, 7):
        st = teraz - dt.timedelta(days=dni_temu, hours=3)
        session.add(SyncRun(kind="reports", profile_id=None, status="OK", started_at=st,
                            finished_at=st + dt.timedelta(minutes=R.randint(12, 28)), rows=R.randint(1500, 4200),
                            detail=json.dumps({"window": [(KONIEC - dt.timedelta(days=dni_temu + 2)).isoformat(),
                                                          (KONIEC - dt.timedelta(days=dni_temu)).isoformat()]})))


# --------------------------------------------------------------------------- #

def main() -> None:
    ap = argparse.ArgumentParser(description="Baza demonstracyjna panelu (sztuczne dane, bez Amazona)")
    ap.add_argument("--reset", action="store_true", help="wyczysc baze przed zasianiem (kasuje WSZYSTKO)")
    args = ap.parse_args()

    init_db()
    with session_scope() as s:
        if args.reset:
            for t in reversed(Base.metadata.sorted_tables):
                s.execute(t.delete())
            s.flush()
        elif s.execute(text("select count(*) from profiles")).scalar():
            sys.exit("Baza nie jest pusta - uzyj --reset (uwaga: kasuje wszystko)")

        stat = {"kampanie": 0, "targety": 0, "metryki": 0}
        for pid, kraj, waluta, mp, strefa, wol, acos_mn in RYNKI:
            rynek(s, pid, kraj, waluta, mp, strefa, wol, acos_mn, stat)
            print(f"  {kraj}: gotowe", flush=True)
        reguly(s)
    print(f"Demo: {len(RYNKI)} rynki, {stat['kampanie']} kampanii, {stat['targety']} targetow, "
          f"{stat['metryki']} wierszy metryk, {START}..{KONIEC}")


if __name__ == "__main__":
    main()
