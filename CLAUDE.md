# AdsPanel

Panel zarządzania kampaniami Amazon Ads — własna alternatywa dla Helium 10
Adtomic. Backend FastAPI + SQLAlchemy, frontend Next.js. Obsługuje wiele
rynków EU jednocześnie.

Kod, komentarze i interfejs są **po polsku**. Trzymaj się tego — komentarze
tłumaczą *dlaczego*, nie *co*, i zwykle odwołują się do konkretnego zachowania
Amazona, które je wymusiło.

---

## Zasada nadrzędna — nie do negocjacji

**Żadna zmiana nie idzie do Amazona bez ręcznego zatwierdzenia przez
użytkownika w zakładce „Do zatwierdzenia".**

Architektura to wymusza i tak ma zostać:

- Endpointy `/api/actions/*` **tylko liczą plan**. Nigdy nie wywołują Ads API.
- `POST /api/plans/apply` to **jedyna** droga zapisu do Amazona.
- Funkcje `_execute_*` w `api/actions.py` są prywatne i wołane wyłącznie
  z `apply_plan`.
- Reguły w tle (`rules/`) i sugestie **wyłącznie proponują** — tworzą plany
  w kolejce, nigdy ich nie stosują.

Nie ma flagi `auto_apply` ani `dry_run=False` i **nie wolno takiej dodawać**,
nawet gdy wygląda na wygodną. Użytkownik wielokrotnie to potwierdzał: każda
sugestia musi przejść przez jego kliknięcie.

Przy testowaniu: nie twórz planów „na próbę" w bazie użytkownika. Używaj
transakcji z `rollback()` albo od razu kasuj to, co utworzyłeś, i powiedz
o tym.

---

## Uruchomienie

```bash
.venv/Scripts/python -m uvicorn app.main:app --app-dir backend --port 8000
```

```bash
cd frontend && npm run dev
```

Backend **nie ma** `--reload` — po zmianie w Pythonie trzeba go zrestartować.

Sprawdzenie przed oddaniem pracy:

```bash
cd frontend && npx tsc --noEmit && npx eslint app components lib
```

CLI: `backend/scripts/cli.py` — `health`, `initdb`, `profiles`, `sync`,
`sales`, `listings`, `catalog --szeroko`, `coverage`.

---

## Pułapki, które już nas kosztowały

Każda z tych rzeczy została odkryta na prawdziwych danych, zwykle po tym, jak
coś pokazało błędną liczbę. Nie zakładaj, że jest inaczej — sprawdź na danych.

### Atrybucja i sprzedaż

- **`sales` (sales7d) JUŻ zawiera halo.** Sprawdzone co do grosza:
  `sales7d = attributedSalesSameSku7d + salesOtherSku7d`. Dodawanie
  `sales_other_sku` do `sales` to podwójne liczenie. ACOS liczony z `sales`
  jest poprawny.
- **Sprzedaż z ostatnich dni jest niepełna.** Amazon dolicza zamówienia przez
  7 dni. Na DE świeże dni mają ROAS niższy o ~25% od dojrzałych. Każdy sygnał
  oparty na sprzedaży musi mieć opóźnienie (patrz `ATTRIBUTION_LAG_DAYS`
  w `api/anomalies.py`). Koszt, kliknięcia i wyświetlenia są domknięte tego
  samego dnia.
- **TACOS liczy się tylko z dni mających oba zbiory.** Licznik i mianownik
  muszą pochodzić z tych samych dni, inaczej wychodzą absurdy typu „udział
  reklam 190%".

### Raporty Amazona

- **Nazwa raportu musi zawierać skrót zestawu kolumn.** Amazon cache'uje
  raporty po nazwie i przy zmienionych kolumnach odda stary, bez nowych pól.
  Patrz `_columns_tag()` w `ads/reports.py`.
- **Raport ofert (`GET_MERCHANT_LISTINGS_ALL_DATA`) ma różny układ na każdym
  rynku.** DE 32 kolumny, NL 28, FR 18 — bez kolumny ASIN w ogóle. Nagłówki
  idą za **językiem konta**, nie rynku (raport dla Francji ma nagłówki po
  niemiecku). Dlatego `sp/listings_report.py` mapuje po aliasach nazw,
  a `Status` wykrywa po wartościach.
- **Na FR ASIN trzeba wyłuskać z `Produkt-ID` i tylko przy typie 1.** Przy
  innym typie jest tam EAN — wzięcie go za ASIN podpina cudze zdjęcie.
- **Odstępy między raportami.** SP-API zwraca `QuotaExceeded` przy zbyt
  częstym tworzeniu (`MIN_CREATE_INTERVAL_S = 65`). Ads API: interwał
  odpytywania 60 s z narastaniem, inaczej lawina 429.

### Zapis do Amazona

- **Duplikat wykluczenia to błąd 207.** `plan_negatives` odsiewa istniejące,
  a sugestie ich nie proponują. Nie usuwaj tego filtra.
- **ASIN-a nie wyklucza się negatywnym keywordem.** To osobny zasób
  (negative targeting). Negatyw z ASIN-em po prostu nie zadziała.
- **Modyfikatory placementów są ustawieniem kampanii, nie grupy reklam.**
  Amazon nie ma ich per grupa — nie da się tego dorobić.
- **Wykluczenia produktowe istnieją tylko na poziomie grupy reklam.**
  Frazowe mogą być na poziomie kampanii albo grupy.
- **Każdy typ obiektu ma własny zasób w Ads API.** W `_execute_state` jest
  jawna mapa typów; nie dodawaj gałęzi `else`, bo wyśle identyfikatory na
  niewłaściwy endpoint i Amazon po cichu je odrzuci.

### Wydajność

- Indeks `ix_product_daily_join (ad_group_id, sku, date)` — złączenie
  `product_ads` z metrykami. Zmierzone: 5,2 s → 1,7 s.
- Słownik nazw kategorii i marek (`targeting_labels`, ~258 tys. wierszy) jest
  cache'owany w pamięci procesu, ważność sprawdzana liczbą wierszy. Bez tego
  każde wejście na Sugestie kosztowało 2,3 s.
- **Szerokie indeksy złożone na tabelach metryk pogarszają sprawę** —
  sprawdzone: skan sekwencyjny bije losowe skoki do tabeli, a widok łączony
  (bez filtra profilu) zwalnia nawet o 68%. Nie dodawaj ich „na wszelki
  wypadek".

---

## Układ kodu

```
backend/app/
  api/actions.py       plany i ich wykonanie — jedyna droga zapisu
  api/queries.py       agregacje dla dashboardu
  api/advice.py        reguły produkujące sugestie
  api/anomalies.py     nagłe zmiany + rozbicie na frazy
  api/effectiveness.py co działo się po zmianach
  ads/                 klient Ads API i operacje na encjach
  sp/                  klient SP-API, raporty sprzedaży i ofert
  etl/                 pobieranie i zapis danych
frontend/
  app/                 strony (Next.js App Router)
  components/          komponenty współdzielone
  lib/api.ts           klient API i typy odpowiedzi
```

---

## Zasady pracy

- **Sprawdzaj na danych, nie zakładaj.** Prawie każda pułapka wyżej wyszła
  dopiero po policzeniu czegoś na prawdziwej bazie.
- **Nie ukrywaj filtrowania.** Gdy widok odsiewa pozycje, ma powiedzieć ile
  i dlaczego (`hidden` w `ngram_analysis`, komunikaty przy planach).
- **Mów wprost, czego nie wiadomo.** Panel woli postawić kreskę niż pokazać
  liczbę bez pokrycia — tak jest z TACOS bez sprzedaży całkowitej i z oceną
  zmian, która czeka 7 dni na dojrzałość danych.
