# AdsPanel

Własny panel do zarządzania i automatyzacji kampanii Amazon Ads (alternatywa dla Helium 10 Adtomic).
Wielorynkowy: DE / ES / IT w jednym widoku.

## Stan projektu

| Faza | Zakres | Status |
|------|--------|--------|
| 1 | Backend, integracja Ads API, ETL, analityka, akcje zapisu | **gotowe** |
| 2 | Frontend Next.js (dashboard, tabele, bulk edit, kolejka zatwierdzeń) | **gotowe** |
| 2b | Logowanie, widok kampanii, portfolia, wykluczenia, sugestie, edycja inline | **gotowe** |
| 3 | Silnik reguł (propozycje do zatwierdzenia) + scheduler | **gotowe** |
| 4 | Marketing Stream (dane godzinowe), SP-API (TACOS, stock guard) | do zrobienia |
| 5 | Deployment na VPS (Docker + Postgres) | do zrobienia |

## Wymagania

- Python 3.11+ (jest 3.14)
- Node.js 20+ (jest 24.19)
- Konto z dostępem do Amazon Ads API (LWA client_id, client_secret, refresh_token)

## Szybki start

```bash
cp .env.example .env      # i uzupełnij dane LWA
.venv/Scripts/python.exe backend/scripts/cli.py health
```

`health` sprawdza konfigurację, wymienia refresh token na access token i wypisuje
widoczne profile reklamowe. Jeśli to przejdzie, integracja działa.

### Pierwszy sync

```bash
.venv/Scripts/python.exe backend/scripts/cli.py sync --lookback 60
```

Kolejność: profile → encje (kampanie, grupy, keywordy, targety, negatywy, reklamy)
→ raporty dzienne (kampanie, placementy, targetowanie, search termy, produkty).

Pierwszy przebieg na 60–95 dniach potrwa kilkanaście minut — raporty v3 są
asynchroniczne, a okno jest cięte na kawałki po 31 dni.

### Panel (frontend)

```bash
cd frontend && npm run dev
```

Panel: http://localhost:3000 — wymaga działającego backendu na porcie 8000.

Ekrany: Przegląd · Portfolia · Kampanie · Targetowanie · Frazy wyszukiwania ·
Placementy · Wykluczenia · Okazje · Reguły · **Do zatwierdzenia** · Produkty ·
Historia zmian.

Kliknięcie kampanii otwiera widok szczegółowy z zakładkami — grupy reklam,
targetowanie, frazy, placementy, wykluczenia — odwzorowujący układ z konsoli Amazona.

Dostęp chroniony hasłem. Ustawienie:

```bash
.venv/Scripts/python.exe backend/scripts/cli.py setpassword
```

### Serwer API

```bash
.venv/Scripts/python.exe -m uvicorn app.main:app --reload --app-dir backend --port 8000
```

Dokumentacja interaktywna: http://localhost:8000/docs

### Dane demo (bez konta Amazon)

```bash
.venv/Scripts/python.exe backend/scripts/seed_demo.py --days 60 --reset
.venv/Scripts/python.exe backend/scripts/smoke_test.py
```

## Struktura

```
backend/app/
  config.py            konfiguracja z .env, endpointy regionalne
  db.py                sesje SQLAlchemy (SQLite lokalnie, Postgres na VPS)
  models.py            16 tabel: encje + fakty dzienne + plany + audyt
  timeutil.py          jednolity UTC w bazie, sufiks Z w API
  ads/
    auth.py            LWA: refresh_token -> access_token, cache z zapasem 5 min
    client.py          HTTP: retry 429/5xx, odświeżanie 401, paginacja nextToken
    entities.py        odczyt i zapis encji SP v3 (batch po 500)
    reports.py         Reporting v3: create -> poll -> pobranie gzip JSON
  etl/sync.py          mapowanie API -> baza, idempotentne okna dat
  rules/
    engine.py          ocena warunkow, budowa planow, scheduler
    templates.py       10 gotowych szablonow regul
  api/
    queries.py         agregacje dashboardu + wykrywanie okazji
    actions.py         plany zmian + zatwierdzanie + change_log
  main.py              FastAPI
frontend/
  app/                 8 ekranow (App Router, Next.js 16 + React 19)
  components/          Shell, DataTable (TanStack v9), BulkBar, wykresy
  lib/                 klient API, filtry, formatowanie, TanStack Query
backend/scripts/
  cli.py               health / initdb / profiles / sync / reports
  seed_demo.py         dane demonstracyjne
  smoke_test.py        test wszystkich endpointów
```

## Endpointy

**Analityka**

| Endpoint | Opis |
|----------|------|
| `GET /api/overview` | KPI + porównanie z poprzednim okresem + timeseries + placementy |
| `GET /api/markets` | DE/ES/IT obok siebie |
| `GET /api/campaigns` | tabela kampanii z ACOS/ROAS/CPC/CVR |
| `GET /api/targets` | keywordy i targety produktowe |
| `GET /api/search-terms` | raport fraz wyszukiwania |
| `GET /api/placements` | rozbicie po placementach |
| `GET /api/products` | wyniki per SKU/ASIN |

**Wykrywanie okazji**

| Endpoint | Opis |
|----------|------|
| `GET /api/opportunities/negatives` | frazy klikane bez konwersji → kandydaci na negatywy |
| `GET /api/opportunities/harvest` | frazy konwertujące, których nie ma jako EXACT, z sugerowanym bidem |
| `GET /api/ngrams` | analiza n-gramów — całe rodziny fraz przepalających budżet |

**Krok 1 — liczenie planu** (nie wysyła nic do Amazona)

| Endpoint | Opis |
|----------|------|
| `POST /api/actions/bids` | plan masowej zmiany stawek |
| `POST /api/actions/state` | plan pauzy/wznowienia keywordów, targetów, kampanii |
| `POST /api/actions/budgets` | plan zmiany budżetów |
| `POST /api/actions/negatives` | plan dodania negatywów |
| `POST /api/actions/harvest` | plan dodania fraz jako nowych keywordów |

Każdy zwraca `{requires_confirmation: true, plan_id, planned[], expires_at}`.

**Reguły**

| Endpoint | Opis |
|----------|------|
| `GET /api/rules/meta` | zakresy, metryki, operatory, dozwolone akcje, szablony |
| `GET/POST /api/rules` | lista i tworzenie reguł |
| `PUT/DELETE /api/rules/{id}` | edycja i usuwanie |
| `POST /api/rules/{id}/preview` | co reguła by złapała — nie tworzy planu |
| `POST /api/rules/{id}/run` | uruchamia regułę → propozycje w kolejce |
| `GET /api/rules/{id}/runs` | historia uruchomień |

**Krok 2 — zatwierdzenie** (dopiero to wysyła)

| Endpoint | Opis |
|----------|------|
| `GET /api/plans` | kolejka zatwierdzeń — plany ręczne i sugestie reguł |
| `POST /api/plans/apply` | zatwierdza plan i wysyła zmiany do Amazona |
| `POST /api/plans/cancel` | odrzuca plan |
| `GET /api/changes` | audyt wszystkich zmian (także odrzuconych podglądów) |

## Decyzje projektowe

**Żadna zmiana nie trafia do Amazona bez zatwierdzenia — bez wyjątków.**
To nie jest domyślne ustawienie, które da się przestawić flagą. Endpointy akcji
(`/api/actions/*`) wyłącznie **liczą plan**, zapisują go w tabeli `pending_plans`
i zwracają `plan_id`. Zapis wykonuje jedynie `POST /api/plans/apply` z tym
identyfikatorem. Funkcje `_execute_*` w [actions.py](backend/app/api/actions.py)
są prywatne i wołane tylko z `apply_plan`, więc nie istnieje ścieżka kodu, która
wysyła cokolwiek do Amazona z pominięciem podglądu — ani z interfejsu, ani
zapytaniem curl-em, ani z reguły.

Dotyczy to tak samo zmian ręcznych, jak i przyszłych reguł: **reguła produkuje
sugestię, nie zmianę.** Każda ląduje w kolejce „Do zatwierdzenia" i czeka na
kliknięcie. Nie ma trybu automatycznego.

**Plan wygasa po 15 minutach.** Bez tego dałoby się zatwierdzić podgląd
policzony na danych sprzed godziny. Plan raz użyty lub odrzucony nie da się
odtworzyć — ponowna próba zwraca 409.

**Fakty dzienne przez replace-range, nie upsert.** Sync kasuje okno dat dla profilu
i wstawia dane na nowo. Dzięki temu ponowny sync nie duplikuje wierszy i zaciąga
dojrzałą atrybucję — Amazon domyka konwersje jeszcze kilka dni po kliknięciu.
Domyślne okno odświeżania to 14 dni.

**Sugerowany bid = przychód na kliknięcie × docelowy ACOS,** z capem na 3× obecny CPC.
Bez capa fraza z kilkoma szczęśliwymi konwersjami generuje absurdalną stawkę.

**Kolumny raportów mają fallback.** Zestawy kolumn różnią się per `reportTypeId`;
jeśli API odrzuci zestaw rozszerzony błędem 400, klient ponawia na kolumnach rdzeniowych.

**Change log jest kompletny.** Każda zmiana — ręczna i z reguły — trafia do `change_log`
ze starą i nową wartością, razem z policzonymi podglądami (status `DRY_RUN`).
To podstawa pod rollback w fazie 3.

**Wszystkie znaczniki czasu w bazie są w UTC**, a API serializuje je z sufiksem `Z`.
Mieszanie `func.now()` (UTC po stronie SQLite) z `datetime.now()` (czas lokalny procesu)
dawało wcześniej plan z 15-minutowym TTL wyglądający, jakby wygasał po 2 godzinach.

## Sugestie optymalizacyjne

Kolumna **Sugestia** na ekranach Kampanie, Targetowanie i w widoku kampanii wskazuje,
co warto zmienić — z uzasadnieniem po kliknięciu.

Progi są **względne wobec średniej konta**, nie sztywne. Konto z ACOS 16% i konto
z ACOS 45% potrzebują innych granic; sztywne 30% byłoby dla pierwszego bezużyteczne,
a dla drugiego alarmowałoby wszędzie.

Sugestie dotyczące stawek liczą **przychód na kliknięcie × docelowy ACOS**, a nie
procent od obecnej stawki — „obniż o 20%" nic nie wie o tym, ile dana pozycja zarabia.

| Kod | Kiedy |
|-----|-------|
| `budget_capped` | dobra kampania wyczerpuje budżet |
| `bid_up` / `bid_down` | stawka oderwana od realnego przychodu pozycji |
| `kill` | kliknięcia bez konwersji ponad próg |
| `placement_up` / `placement_down` | placement tańszy lub droższy od pozostałych |
| `not_serving` | aktywna kampania bez wyświetleń |
| `low_ctr` / `low_cvr` | trafność albo karta produktu odstaje od średniej |

## Edycja ręczna

Stawki, budżety i modyfikatory placementów edytuje się klikając wartość w tabeli.
Zmiany zbierają się lokalnie i dopiero przycisk „Podgląd i zatwierdź" tworzy z nich
**jeden plan**. Przy dziesięciu poprawkach to jedno zatwierdzenie zamiast dziesięciu —
zasada „każda zmiana wymaga zgody" zostaje zachowana, nie zamieniając się w udrękę.

## Nazewnictwo zgodne z Amazonem

Ads API zwraca kody techniczne, konsola pokazuje nazwy. Bez mapowania panelu nie
dałoby się zestawić z Amazonem.

| API | Panel |
|-----|-------|
| `QUERY_HIGH_REL_MATCHES`, `close-match` | Ściśle powiązane |
| `QUERY_BROAD_REL_MATCHES` | Luźno powiązane |
| `ASIN_SUBSTITUTE_RELATED` | Zamienniki |
| `ASIN_ACCESSORY_RELATED` | Uzupełniające |
| `category="2076158031" brand="16404400031"` | Kategoria + marka |

Nazwy kategorii i marek pobiera `etl/labels.py` do tabeli `targeting_labels`:
drzewo kategorii z `GET /sp/targets/categories`, marki z
`GET /sp/targets/category/{id}/refinements` — tylko dla kategorii faktycznie
występujących w targetach konta.

**Rodzaj targetu wynika z `match_type`, nie z obecności pola `keyword`** — Amazon
wypełnia je także dla auto-targetów wartością `close-match`, przez co wcześniej
wszystko lądowało jako słowo kluczowe.

## Reguły

Reguła = **zakres danych + warunki + akcja**. Warunki działają na tych samych
metrykach, które widzisz w tabelach: `impressions, clicks, cost, orders, sales,
units, ctr, cpc, cvr, acos, roas, cpa, bid`.

| Zakres | Dostępne akcje |
|--------|----------------|
| Frazy wyszukiwania | negatyw (exact/phrase), wykluczenie ASIN, przejęcie jako keyword |
| Keywordy i targety | zmiana stawki o %, stawka pod docelowy ACOS, pauza, wznowienie |
| Kampanie | zmiana budżetu o %, pauza, wznowienie |
| Placementy | podniesienie modyfikatora stawki |

Scheduler sprawdza co 30 minut, które reguły mają minięty interwał, i je uruchamia.
**Uruchomienie tworzy wyłącznie propozycję** — najgorsze, co zrobi źle skonfigurowana
reguła, to zapełni kolejkę zatwierdzeń.

Zabezpieczenia wbudowane w silnik:
- **limit pozycji na przebieg** (`max_items`) — reguła nie zaproponuje 5000 zmian naraz,
- **widełki stawek i budżetów** (`min_bid`/`max_bid`, `min_budget`/`max_budget`),
- **cap skoku** przy stawce pod ACOS (`max_change_pct`, domyślnie ±50%) — jedna szczęśliwa
  konwersja nie wywinduje stawki kilkukrotnie,
- **deduplikacja** — nie zaproponuje negatywu, który już istnieje, ani keyworda, który już masz,
- **ACOS bez sprzedaży** traktowany jako „nieskończenie zły" przy progach górnych; inaczej
  reguła „ACOS > 50%" pomijałaby właśnie najgorsze pozycje (te z zerową sprzedażą).

## Czego Ads API nie udostępnia

**Metryk Amazon Business (B2B).** Sprawdzone empirycznie: wymuszenie błędu 400
zwraca pełną listę dozwolonych kolumn dla każdego typu raportu. We wszystkich
sześciu (52–65 kolumn) **nie ma ani jednej kolumny B2B**. Dane B2B istnieją po
stronie sprzedażowej, w SP-API — to osobna integracja.

Pełne listy dozwolonych kolumn: `data/allowed-columns.json`.

## Limity Amazon Ads API

- retencja raportów: ~95 dni wstecz
- maksymalne okno raportu dziennego: 31 dni
- limity zapytań: klient ponawia 429/5xx z backoffem wykładniczym i honoruje `Retry-After`
- access token żyje 60 minut, odświeżany automatycznie z 5-minutowym zapasem
- **zlecenia raportów są deduplikowane po nazwie** — powtórzone zwraca 425 z ID
  istniejącego raportu; klient go przechwytuje i używa zamiast generować ponownie
- generowanie jednego raportu trwa 5–30 min; 20 raportów (95 dni × 5 typów)
  pobieranych po 6 równolegle schodzi w ~1 h zamiast 9 h sekwencyjnie
- `/v2/portfolios` i `/v2/portfolios/extended` **zostały wycofane** (404) —
  aktualny jest `POST /portfolios/list`
- domyślnie API zwraca też encje **zarchiwizowane**; sync filtruje na
  `ENABLED, PAUSED`, bo archiwum stanowiło 99,7% targetów na koncie
