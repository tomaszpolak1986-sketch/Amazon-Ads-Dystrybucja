"""Model danych panelu: encje reklamowe + fakty dzienne z raportow."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    BigInteger,
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .timeutil import utcnow


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )


# --------------------------------------------------------------------------- #
# Encje (stan konta - synchronizowane z Ads API)
# --------------------------------------------------------------------------- #


class Profile(Base, TimestampMixin):
    """Profil reklamowy = jedno konto na jednym marketplace (DE / ES / IT ...)."""

    __tablename__ = "profiles"

    profile_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    country_code: Mapped[str | None] = mapped_column(String(8))
    currency_code: Mapped[str | None] = mapped_column(String(8))
    timezone: Mapped[str | None] = mapped_column(String(64))
    marketplace_id: Mapped[str | None] = mapped_column(String(32))
    account_name: Mapped[str | None] = mapped_column(String(255))
    account_type: Mapped[str | None] = mapped_column(String(32))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    daily_budget: Mapped[float | None] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Portfolio(Base, TimestampMixin):
    __tablename__ = "portfolios"

    portfolio_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.profile_id"), index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    state: Mapped[str | None] = mapped_column(String(32))
    budget_amount: Mapped[float | None] = mapped_column(Float)
    budget_policy: Mapped[str | None] = mapped_column(String(32))


class Campaign(Base, TimestampMixin):
    __tablename__ = "campaigns"

    campaign_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.profile_id"), index=True)
    ad_product: Mapped[str] = mapped_column(String(24), default="SP")  # SP | SB | SD
    portfolio_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    name: Mapped[str | None] = mapped_column(String(512))
    state: Mapped[str | None] = mapped_column(String(32), index=True)
    targeting_type: Mapped[str | None] = mapped_column(String(32))  # MANUAL | AUTO
    budget_amount: Mapped[float | None] = mapped_column(Float)
    budget_type: Mapped[str | None] = mapped_column(String(32))
    bidding_strategy: Mapped[str | None] = mapped_column(String(64))
    placement_bidding: Mapped[dict | None] = mapped_column(JSON)
    start_date: Mapped[dt.date | None] = mapped_column(Date)
    end_date: Mapped[dt.date | None] = mapped_column(Date)
    raw: Mapped[dict | None] = mapped_column(JSON)

    __table_args__ = (Index("ix_campaigns_profile_state", "profile_id", "state"),)


class AdGroup(Base, TimestampMixin):
    __tablename__ = "ad_groups"

    ad_group_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(String(64), index=True)
    profile_id: Mapped[int] = mapped_column(BigInteger, index=True)
    ad_product: Mapped[str] = mapped_column(String(24), default="SP")
    name: Mapped[str | None] = mapped_column(String(512))
    state: Mapped[str | None] = mapped_column(String(32))
    default_bid: Mapped[float | None] = mapped_column(Float)
    raw: Mapped[dict | None] = mapped_column(JSON)


class Keyword(Base, TimestampMixin):
    """Keyword pozytywny (SP/SB)."""

    __tablename__ = "keywords"

    keyword_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ad_group_id: Mapped[str] = mapped_column(String(64), index=True)
    campaign_id: Mapped[str] = mapped_column(String(64), index=True)
    profile_id: Mapped[int] = mapped_column(BigInteger, index=True)
    ad_product: Mapped[str] = mapped_column(String(24), default="SP")
    keyword_text: Mapped[str | None] = mapped_column(String(512), index=True)
    match_type: Mapped[str | None] = mapped_column(String(64))
    state: Mapped[str | None] = mapped_column(String(32))
    bid: Mapped[float | None] = mapped_column(Float)
    raw: Mapped[dict | None] = mapped_column(JSON)


class NegativeKeyword(Base, TimestampMixin):
    __tablename__ = "negative_keywords"

    keyword_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ad_group_id: Mapped[str | None] = mapped_column(String(64), index=True)
    campaign_id: Mapped[str] = mapped_column(String(64), index=True)
    profile_id: Mapped[int] = mapped_column(BigInteger, index=True)
    level: Mapped[str] = mapped_column(String(16), default="AD_GROUP")  # AD_GROUP | CAMPAIGN
    keyword_text: Mapped[str | None] = mapped_column(String(512), index=True)
    match_type: Mapped[str | None] = mapped_column(String(64))
    state: Mapped[str | None] = mapped_column(String(32))


class TargetingLabel(Base):
    """Slownik nazw dla identyfikatorow uzywanych w targetowaniu.

    Amazon w raportach i encjach zwraca same identyfikatory kategorii i marek
    (brand="16404400031"), a w panelu pokazuje nazwy. Bez tego slownika panel
    nie da sie zestawic z konsola reklamowa.

    Nazwy sa specyficzne dla rynku - ta sama kategoria ma inna nazwe na DE i IT -
    dlatego kluczem jest para (marketplace, identyfikator).
    """

    __tablename__ = "targeting_labels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    marketplace_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)  # CATEGORY | BRAND
    ext_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(255))
    parent_id: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("marketplace_id", "kind", "ext_id", name="uq_targeting_label"),
    )


class NegativeTarget(Base, TimestampMixin):
    """Wykluczone ASIN-y i marki w targetowaniu produktowym.

    Odpowiednik negatywnych keywordow, tylko dla targetowania po produktach.
    W panelu Amazona to zakladka "Negative targeting".
    """

    __tablename__ = "negative_targets"

    target_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ad_group_id: Mapped[str | None] = mapped_column(String(64), index=True)
    campaign_id: Mapped[str] = mapped_column(String(64), index=True)
    profile_id: Mapped[int] = mapped_column(BigInteger, index=True)
    level: Mapped[str] = mapped_column(String(16), default="AD_GROUP")
    expression_type: Mapped[str | None] = mapped_column(String(32))
    # Czytelna postac wykluczenia, np. "ASIN_SAME_AS B08XYZ" albo "BRAND_SAME_AS 123"
    value: Mapped[str | None] = mapped_column(String(255), index=True)
    state: Mapped[str | None] = mapped_column(String(32))
    raw: Mapped[dict | None] = mapped_column(JSON)


class Target(Base, TimestampMixin):
    """Targeting produktowy/kategoryjny oraz auto-targety (close-match itd.)."""

    __tablename__ = "targets"

    target_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ad_group_id: Mapped[str] = mapped_column(String(64), index=True)
    campaign_id: Mapped[str] = mapped_column(String(64), index=True)
    profile_id: Mapped[int] = mapped_column(BigInteger, index=True)
    ad_product: Mapped[str] = mapped_column(String(24), default="SP")
    expression_type: Mapped[str | None] = mapped_column(String(32))  # AUTO | MANUAL
    expression: Mapped[list | dict | None] = mapped_column(JSON)
    resolved_expression: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str | None] = mapped_column(String(32))
    bid: Mapped[float | None] = mapped_column(Float)
    raw: Mapped[dict | None] = mapped_column(JSON)


class ProductAd(Base, TimestampMixin):
    __tablename__ = "product_ads"

    ad_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ad_group_id: Mapped[str] = mapped_column(String(64), index=True)
    campaign_id: Mapped[str] = mapped_column(String(64), index=True)
    profile_id: Mapped[int] = mapped_column(BigInteger, index=True)
    # SP i SD leza w tej samej tabeli; bez tej kolumny archiwizacja jednego
    # typu kasowalaby reklamy drugiego, bo nie ma ich we wlasnej liscie.
    ad_product: Mapped[str] = mapped_column(String(24), default="SP", index=True)
    asin: Mapped[str | None] = mapped_column(String(32), index=True)
    sku: Mapped[str | None] = mapped_column(String(128), index=True)
    state: Mapped[str | None] = mapped_column(String(32))


# --------------------------------------------------------------------------- #
# Fakty dzienne (raporty)
# --------------------------------------------------------------------------- #


class MetricsMixin:
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    orders: Mapped[int] = mapped_column(Integer, default=0)          # purchases 7d
    sales: Mapped[float] = mapped_column(Float, default=0.0)         # attributed sales 7d
    units: Mapped[int] = mapped_column(Integer, default=0)
    orders_same_sku: Mapped[int] = mapped_column(Integer, default=0)
    sales_same_sku: Mapped[float] = mapped_column(Float, default=0.0)

    # Sprzedaz INNYCH produktow, ktora wywolala ta reklama - tzw. efekt halo.
    # Klient klika w listwe, kupuje tez narozniki i klej. Bez tej kolumny
    # keyword promujacy jeden produkt wyglada na nierentowny, choc naped
    # sprzedazy katalogu. Amazon oddaje ja tylko dla targetow, fraz i produktow,
    # wiec przy kampaniach i placementach zostaje pusta.
    sales_other_sku: Mapped[float | None] = mapped_column(Float)
    units_other_sku: Mapped[int | None] = mapped_column(Integer)

    # Udzial wyswietlen na gorze wynikow wyszukiwania (0-100). Amazon podaje go
    # tylko dla kampanii, placementow i targetow - dla fraz i produktow zostaje
    # pusty, dlatego kolumna jest opcjonalna, a nie zerowa. Zero znaczyloby
    # "nie wyswietlamy sie na gorze", a brak danych to co innego.
    tos_impression_share: Mapped[float | None] = mapped_column(Float, nullable=True)


class CampaignDaily(Base, MetricsMixin):
    __tablename__ = "metrics_campaign_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    profile_id: Mapped[int] = mapped_column(BigInteger, index=True)
    campaign_id: Mapped[str] = mapped_column(String(64), index=True)
    ad_product: Mapped[str] = mapped_column(String(24), default="SP")

    __table_args__ = (
        UniqueConstraint("date", "profile_id", "campaign_id", "ad_product",
                         name="uq_campaign_daily"),
    )


class PlacementDaily(Base, MetricsMixin):
    """Rozbicie po placementach - podstawa pod optymalizacje mnoznikow."""

    __tablename__ = "metrics_placement_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    profile_id: Mapped[int] = mapped_column(BigInteger, index=True)
    campaign_id: Mapped[str] = mapped_column(String(64), index=True)
    placement: Mapped[str] = mapped_column(String(64))

    __table_args__ = (
        UniqueConstraint("date", "profile_id", "campaign_id", "placement",
                         name="uq_placement_daily"),
    )


class TargetDaily(Base, MetricsMixin):
    """Wyniki keywordow i targetow produktowych."""

    __tablename__ = "metrics_target_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    profile_id: Mapped[int] = mapped_column(BigInteger, index=True)
    campaign_id: Mapped[str] = mapped_column(String(64), index=True)
    ad_group_id: Mapped[str | None] = mapped_column(String(64), index=True)
    target_id: Mapped[str] = mapped_column(String(64), index=True)
    ad_product: Mapped[str] = mapped_column(String(24), default="SP", index=True)
    target_kind: Mapped[str] = mapped_column(String(16), default="KEYWORD")  # KEYWORD | TARGET
    target_text: Mapped[str | None] = mapped_column(String(512))
    match_type: Mapped[str | None] = mapped_column(String(64))
    bid: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (
        UniqueConstraint("date", "profile_id", "target_id", name="uq_target_daily"),
    )


class SearchTermDaily(Base, MetricsMixin):
    __tablename__ = "metrics_search_term_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    profile_id: Mapped[int] = mapped_column(BigInteger, index=True)
    campaign_id: Mapped[str] = mapped_column(String(64), index=True)
    ad_product: Mapped[str] = mapped_column(String(24), default="SP", index=True)
    ad_group_id: Mapped[str | None] = mapped_column(String(64), index=True)
    target_id: Mapped[str | None] = mapped_column(String(64), index=True)
    search_term: Mapped[str] = mapped_column(String(512), index=True)
    target_text: Mapped[str | None] = mapped_column(String(512))
    match_type: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        UniqueConstraint(
            "date", "profile_id", "campaign_id", "target_id", "search_term",
            name="uq_search_term_daily",
        ),
    )


class ProductDaily(Base, MetricsMixin):
    __tablename__ = "metrics_product_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    profile_id: Mapped[int] = mapped_column(BigInteger, index=True)
    campaign_id: Mapped[str] = mapped_column(String(64), index=True)
    ad_product: Mapped[str] = mapped_column(String(24), default="SP", index=True)
    ad_group_id: Mapped[str | None] = mapped_column(String(64), index=True)
    asin: Mapped[str | None] = mapped_column(String(32), index=True)
    sku: Mapped[str | None] = mapped_column(String(128), index=True)

    __table_args__ = (
        UniqueConstraint("date", "profile_id", "campaign_id", "ad_group_id", "sku", "asin",
                         name="uq_product_daily"),
        # Widok "produkty w grupach reklam" laczy te tabele z product_ads po
        # parze (ad_group_id, sku). Osobne indeksy na obu kolumnach nie
        # wystarczaly - baza korzystala z jednego i dobierala reszte wierszami.
        # Zmierzone na 300 tys. wierszy: 5,2 s -> 1,7 s, kosztem 16 MB.
        Index("ix_product_daily_join", "ad_group_id", "sku", "date"),
    )


# --------------------------------------------------------------------------- #
# Operacyjne
# --------------------------------------------------------------------------- #


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    profile_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    status: Mapped[str] = mapped_column(String(24), default="RUNNING")
    started_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    rows: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[str | None] = mapped_column(Text)


class DismissedSuggestion(Base):
    """Sugestia odrzucona przez uzytkownika - nie pokazujemy jej ponownie.

    Odrzucenie jest decyzja, nie zmiana w Amazonie: "znam ten target, zostaje
    jak jest". Bez tej tabeli taka sugestia wracala przy kazdym odswiezeniu
    i zaslaniala te, ktore naprawde czekaja na decyzje.

    Klucz to tozsamosc sugestii (zakres + obiekt + kod reguly), nie akcji -
    dzieki temu da sie odrzucic takze sugestie czysto informacyjna. `until`
    puste oznacza na stale; z data - do tego dnia, potem sugestia moze wrocic.
    """

    __tablename__ = "dismissed_suggestions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(BigInteger, index=True)
    scope: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(512))
    code: Mapped[str] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(String(512))
    entity_name: Mapped[str | None] = mapped_column(String(512))
    dismissed_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    until: Mapped[dt.datetime | None] = mapped_column(DateTime)

    __table_args__ = (
        UniqueConstraint("profile_id", "scope", "entity_id", "code", name="uq_dismissed_suggestion"),
    )


class FxRate(Base):
    """Kurs waluty wobec EUR na dany dzien - do raportow zbiorczych.

    Trzymamy "ile EUR za 1 jednostke" (GBP 2026-09-30 = 1.1701), bo tak sie
    mnozy kwote. EBC podaje odwrotnie, przeliczenie robi `etl/fx.py`.
    Dni bez notowania maja kurs przeniesiony z ostatniego dnia roboczego.
    """

    __tablename__ = "fx_rates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    currency: Mapped[str] = mapped_column(String(8), index=True)
    rate_eur: Mapped[float] = mapped_column(Float)

    __table_args__ = (
        UniqueConstraint("date", "currency", name="uq_fx_rate"),
    )


class Rule(Base, TimestampMixin):
    """Regula automatyzacji.

    Regula NIGDY nie wykonuje zmian. Przy uruchomieniu ocenia dane i tworzy
    PendingPlan ze zrodlem RULE, ktory czeka na zatwierdzenie w panelu.

    Struktura `conditions`:
        {"all": [{"metric": "clicks", "op": ">=", "value": 50},
                 {"metric": "orders", "op": "==", "value": 0}]}
    Obok "all" (koniunkcja) mozna uzyc "any" (alternatywa).

    Struktura `action`:
        {"type": "ADD_NEGATIVE_KEYWORD", "match_type": "NEGATIVE_EXACT"}
        {"type": "SET_BID_PCT", "percent": -20}
        {"type": "SET_PLACEMENT_MODIFIER", "max_percent": 100, "step": 10}
    """

    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    # None = wszystkie rynki
    profile_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    # TARGET | SEARCH_TERM | CAMPAIGN | PLACEMENT | PRODUCT
    scope: Mapped[str] = mapped_column(String(24))
    lookback_days: Mapped[int] = mapped_column(Integer, default=30)

    filters: Mapped[dict | None] = mapped_column(JSON)
    conditions: Mapped[dict] = mapped_column(JSON)
    action: Mapped[dict] = mapped_column(JSON)
    limits: Mapped[dict | None] = mapped_column(JSON)

    # Harmonogram: None = tylko recznie; inaczej co ile godzin sprawdzac
    schedule_hours: Mapped[int | None] = mapped_column(Integer)
    last_run_at: Mapped[dt.datetime | None] = mapped_column(DateTime)


class RuleRun(Base):
    """Historia uruchomien reguly - co znalazla i czy powstal plan."""

    __tablename__ = "rule_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("rules.id"), index=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    profile_id: Mapped[int | None] = mapped_column(BigInteger)
    evaluated: Mapped[int] = mapped_column(Integer, default=0)
    matched: Mapped[int] = mapped_column(Integer, default=0)
    plan_id: Mapped[str | None] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(24), default="OK")
    detail: Mapped[str | None] = mapped_column(Text)


class PendingPlan(Base):
    """Zatwierdzony plan zmian - jedyna droga do zapisu w Amazonie.

    Endpointy akcji NIGDY nie wykonuja zmian od razu: licza plan, zapisuja go tutaj
    i zwracaja plan_id. Dopiero /api/actions/apply z tym identyfikatorem wysyla
    zmiany. Dzieki temu nie da sie nic nadpisac bez wczesniejszego podgladu -
    ani z interfejsu, ani przypadkowym zapytaniem curl-em.
    """

    __tablename__ = "pending_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_id: Mapped[int] = mapped_column(BigInteger, index=True)
    kind: Mapped[str] = mapped_column(String(32))
    summary: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime)
    applied_at: Mapped[dt.datetime | None] = mapped_column(DateTime)


class ChangeLog(Base):
    """Audyt kazdej zmiany wypchnietej do Amazona - recznej i automatycznej."""

    __tablename__ = "change_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    profile_id: Mapped[int] = mapped_column(BigInteger, index=True)
    entity_type: Mapped[str] = mapped_column(String(32))   # CAMPAIGN | AD_GROUP | KEYWORD | TARGET
    entity_id: Mapped[str] = mapped_column(String(512), index=True)
    field: Mapped[str] = mapped_column(String(64))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), default="MANUAL")  # MANUAL | RULE
    rule_id: Mapped[int | None] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(24), default="APPLIED")  # APPLIED | FAILED | DRY_RUN
    error: Mapped[str | None] = mapped_column(Text)


class Listing(Base):
    """Oferta w Seller Central - cala baza produktowa, nie tylko reklamowana.

    Ads API zna wylacznie produkty, ktore kiedys trafily do kampanii. Na DE to
    2954 SKU wobec 5077 realnych ofert - ponad dwoch tysiecy produktow panel
    wczesniej nie widzial w ogole. Ta tabela zamyka te luke, a przy okazji
    przynosi stan magazynowy i cene, ktorych Ads API nie oddaje.
    """

    __tablename__ = "listings"

    sku: Mapped[str] = mapped_column(String(128), primary_key=True)
    marketplace_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    asin: Mapped[str | None] = mapped_column(String(32), index=True)
    title: Mapped[str | None] = mapped_column(String(512))
    price: Mapped[float | None] = mapped_column(Float)
    quantity: Mapped[int | None] = mapped_column(Integer)
    # Active | Inactive | Incomplete - w raporcie bywa zlokalizowany
    status: Mapped[str | None] = mapped_column(String(64), index=True)
    # DEFAULT = wysylka wlasna, AMAZON_* = FBA
    fulfillment: Mapped[str | None] = mapped_column(String(32))
    listing_id: Mapped[str | None] = mapped_column(String(64))
    opened_on: Mapped[dt.date | None] = mapped_column(Date)
    # ASIN wziety z innego rynku, bo raport tego rynku go nie podal (tak jest
    # na FR). Flaga zostaje, zeby nie mylic tego z danymi zrodlowymi.
    asin_inferred: Mapped[bool] = mapped_column(Boolean, default=False)
    fetched_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class TotalSalesDaily(Base):
    """Sprzedaz calkowita - reklamowa i organiczna razem.

    Ads API nie zna sprzedazy organicznej: kazda jego kolumna ze sprzedaza jest
    przypisana do reklamy. Bez tej tabeli nie da sie policzyc TACOS, bo brakuje
    mianownika. Dane biora sie z raportu biznesowego Seller Central (import CSV)
    albo z recznego wpisu.

    `asin` puste oznacza wiersz zbiorczy dla calego rynku na dany dzien. Gdy dla
    danego dnia sa oba rodzaje wierszy, zbiorczy ma pierwszenstwo - jest blizej
    prawdy niz suma pozycji, ktore uzytkownik akurat wyeksportowal.
    """

    __tablename__ = "total_sales_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    profile_id: Mapped[int] = mapped_column(BigInteger, index=True)
    asin: Mapped[str | None] = mapped_column(String(32), index=True)
    sku: Mapped[str | None] = mapped_column(String(128), index=True)

    sales: Mapped[float] = mapped_column(Float, default=0.0)
    units: Mapped[int] = mapped_column(Integer, default=0)
    orders: Mapped[int] = mapped_column(Integer, default=0)
    # Sesje bywaja w raporcie biznesowym; przydaja sie do konwersji organicznej
    sessions: Mapped[int | None] = mapped_column(Integer)

    source: Mapped[str] = mapped_column(String(16), default="CSV")  # CSV | MANUAL
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("date", "profile_id", "asin", name="uq_total_sales_daily"),
    )


class BidRecommendation(Base):
    """Rekomendacje stawek od Amazona - punkt odniesienia, nie zalecenie.

    Amazon optymalizuje je pod konwersje i widocznosc, nie pod TWOJ docelowy
    ACOS, wiec bywaja wyzsze niz to, co sie oplaca. Trzymamy je obok wlasnych
    sugestii, zeby dalo sie je zestawic - a nie zeby je stosowac wprost.

    Pobierane wsadowo per grupa reklam i zapamietywane, bo API jest ostro
    limitowane i odpytywanie przy kazdym otwarciu tabeli konczyloby sie 429.
    """

    __tablename__ = "bid_recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(BigInteger, index=True)
    entity_type: Mapped[str] = mapped_column(String(16), default="KEYWORD")  # KEYWORD | TARGET
    entity_id: Mapped[str] = mapped_column(String(512), index=True)

    low: Mapped[float | None] = mapped_column(Float)
    median: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    theme: Mapped[str | None] = mapped_column(String(48))

    fetched_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("profile_id", "entity_type", "entity_id", name="uq_bid_reco"),
    )


class CatalogItem(Base):
    """Dane katalogowe produktu - tytul i miniatura.

    Pobierane z SP-API i zapamietywane, bo odpytywanie katalogu przy kazdym
    otwarciu tabeli byloby wolne i podlega limitom. Zdjecia zmieniaja sie
    rzadko, wiec odswiezamy je tylko wtedy, gdy sa naprawde stare.

    Klucz zawiera marketplace, bo ten sam ASIN ma inny tytul na kazdym rynku.
    """

    __tablename__ = "catalog_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asin: Mapped[str] = mapped_column(String(32), index=True)
    marketplace_id: Mapped[str] = mapped_column(String(32), index=True)

    title: Mapped[str | None] = mapped_column(String(512))
    brand: Mapped[str | None] = mapped_column(String(128))
    # Miniatura (do ~120 px) i obraz glowny w pelnym rozmiarze
    image_small: Mapped[str | None] = mapped_column(String(512))
    image_main: Mapped[str | None] = mapped_column(String(512))

    fetched_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("asin", "marketplace_id", name="uq_catalog_item"),
    )


class FulfillmentDaily(Base):
    """Sprzedaz calkowita w rozbiciu na kanal realizacji: FBA albo wysylka wlasna.

    Zrodlem jest raport zamowien, nie raport sprzedazy. Ten drugi w ogole nie
    zna kanalu realizacji - sprawdzone na zywym raporcie, nie ma tam takiego
    pola. Dopiero `GET_FLAT_FILE_ALL_ORDERS_DATA_BY_ORDER_DATE_GENERAL` niesie
    kolumne `fulfillment-channel`.

    Trzymamy wylacznie sumy. Raport zrodlowy zawiera dane kupujacych - miasto,
    kod pocztowy, nazwe firmy - i zadne z nich nie trafia do bazy.

    Kwoty sa w walucie rynku, stad kolumna `currency`: jeden raport potrafi
    niesc EUR, GBP i SEK naraz, wiec sumowanie ich razem dawaloby liczbe bez
    znaczenia.
    """

    __tablename__ = "fulfillment_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    profile_id: Mapped[int] = mapped_column(BigInteger, index=True)
    # FBA | MERCHANT | INNY
    channel: Mapped[str] = mapped_column(String(16), index=True)

    sales: Mapped[float] = mapped_column(Float, default=0.0)
    units: Mapped[int] = mapped_column(Integer, default=0)
    orders: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str | None] = mapped_column(String(8))

    fetched_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("date", "profile_id", "channel", name="uq_fulfillment_daily"),
    )
