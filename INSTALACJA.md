# Instalacja AdsPanelu na nowym komputerze

Paczka `AdsPanel-kod.zip` zawiera **całą aplikację w aktualnej wersji**:
Sponsored Products, Sponsored Brands i Sponsored Display, sprzedaż całkowitą
z SP-API, reguły, anomalie, n-gramy i kolejkę zatwierdzeń.

## To jest czysta instalacja

W paczce **nie ma żadnych danych z Amazona**: ani bazy, ani kluczy, ani
nazw kampanii, produktów czy identyfikatorów jakiegokolwiek konta. Sam kod.

Instancja jest przeznaczona pod **inne konto reklamowe** — po podłączeniu
własnych kluczy zbuduje własną historię od zera. Nic z poprzedniego konta
tu nie trafia i nie ma czego czyścić po instalacji.

## Czego wymaga komputer

- **Python 3.11 lub nowszy**
- **Node.js 20 lub nowszy**

Sprawdzisz jedną komendą:

```bash
python --version && node --version
```

---

## 1. Rozpakuj i zainstaluj zależności

```bash
cd AdsPanel
```

```bash
python -m venv .venv
```

```bash
.venv/Scripts/pip install -r requirements.txt
```

```bash
cd frontend && npm install && cd ..
```

Na Linuksie i macOS ścieżka to `.venv/bin/pip`. Instalacja frontendu
zajmuje kilka minut i około 1,5 GB — to normalne, `node_modules` zawsze
tyle waży i dlatego nie ma go w paczce.

## 2. Konfiguracja

```bash
cp .env.example .env
```

Otwórz `.env` i uzupełnij:

| Pole | Skąd wziąć |
|---|---|
| `ADS_CLIENT_ID`, `ADS_CLIENT_SECRET` | Security Profile w konsoli deweloperskiej Amazona |
| `ADS_REFRESH_TOKEN` | wygeneruje `authorize.py` w kroku 3 |
| `SP_CLIENT_ID`, `SP_CLIENT_SECRET`, `SP_REFRESH_TOKEN` | osobna aplikacja SP-API (sprzedaż całkowita, TACOS) |
| `ADS_REGION` | `EU` dla rynków europejskich |

Klucze SP-API są opcjonalne. Bez nich panel działa, ale nie policzy TACOS
i nie zobaczy produktów, których nigdy nie reklamowano.

Instrukcja zdobycia obu kompletów kluczy leży w
`docs/dostep-amazon-instrukcja.html`.

Frontend potrzebuje własnego pliku — adres API jest wkompilowywany w build,
więc musi być znany przed uruchomieniem:

```bash
echo NEXT_PUBLIC_API_URL=http://localhost:8000 > frontend/.env.local
```

## 3. Token dostępowy

```bash
.venv/Scripts/python backend/scripts/authorize.py
```

Skrypt otworzy przeglądarkę i nasłuchuje na `http://localhost:9090/redirect`.
**Ten sam adres musi być wpisany w Allowed Return URLs** w Security Profile —
co do znaku, bez ukośnika na końcu. Każda różnica kończy się błędem
`redirect_uri mismatch`.

Po zalogowaniu skrypt wypisze `ADS_REFRESH_TOKEN` — wklej go do `.env`.

Sprawdź, czy wszystko się zgadza:

```bash
.venv/Scripts/python backend/scripts/cli.py health
```

## 4. Baza danych

Baza powstaje pusta i wypełnia się danymi nowego konta.

```bash
.venv/Scripts/python backend/scripts/cli.py initdb
```

```bash
.venv/Scripts/python backend/scripts/cli.py profiles
```

```bash
.venv/Scripts/python backend/scripts/cli.py sync --days 60
```

Pierwszy sync trwa **kilkadziesiąt minut** — Amazon generuje raporty po
swojej stronie i panel czeka na gotowe pliki. To normalne, nie zawieszenie.

Historia fraz wyszukiwania sięga u Amazona około **65 dni wstecz**. Starszych
danych nie da się już pobrać, więc jeśli zależy Ci na historii, zrób sync
możliwie wcześnie.

## 5. Hasło do panelu

```bash
.venv/Scripts/python backend/scripts/cli.py setpassword
```

Hasło nie jest nigdzie zapisywane — do `.env` trafia wyłącznie jego skrót
argon2. Zapomnianego nie da się odczytać, ustawia się nowe tą samą komendą.

## 6. Uruchomienie

Backend:

```bash
.venv/Scripts/python -m uvicorn app.main:app --app-dir backend --port 8000
```

Frontend, w drugim oknie:

```bash
cd frontend && npm run dev
```

Panel: **http://localhost:3000**

---

## Dane opcjonalne

Po pierwszym uruchomieniu warto dociągnąć resztę. Każda komenda działa
niezależnie, więc można je puszczać pojedynczo.

```bash
.venv/Scripts/python backend/scripts/cli.py listings
```
Pełna baza produktowa z Seller Central — także produkty, których nigdy nie
reklamowano. Ads API ich nie zna.

```bash
.venv/Scripts/python backend/scripts/cli.py catalog
```
Tytuły i miniatury produktów.

```bash
.venv/Scripts/python backend/scripts/cli.py sales --days 60
```
Sprzedaż całkowita — mianownik do TACOS.

```bash
.venv/Scripts/python backend/scripts/cli.py sb
```
```bash
.venv/Scripts/python backend/scripts/cli.py sd
```
Encje Sponsored Brands i Sponsored Display.

```bash
.venv/Scripts/python backend/scripts/cli.py coverage
```
Pokazuje, ile dni danych ma każdy rynek w każdym typie raportu. Dobra
kontrola po pierwszym synchronizowaniu.

---

## Co warto wiedzieć

**Żadna zmiana nie trafia do Amazona bez zatwierdzenia.** Reguły i sugestie
wyłącznie proponują — każda zmiana czeka w zakładce „Do zatwierdzenia" na
ręczną akceptację. Nie ma trybu automatycznego i nie ma flagi, która by go
włączała.

**Automatyczne pobieranie danych to wyłącznie odczyt.** Panel co 6 godzin
dociąga raporty, a raz na dobę Sponsored Brands i Display. Niczego przy tym
nie zmienia na koncie.

**Panel musi być uruchomiony, żeby pobierać dane.** Na wyłączonym komputerze
harmonogram nie działa. Zaległy nocny przebieg nadrobi się po starcie, jeśli
od planowanej godziny nie minęło więcej niż 12 godzin. Docelowe rozwiązanie
to serwer — instrukcja w `deploy/`.

## Gdy coś nie działa

| Objaw | Przyczyna |
|---|---|
| `redirect_uri mismatch` | adres w Security Profile różni się od `ADS_REDIRECT_URI` — porównaj znak po znaku |
| Panel prosi o hasło w kółko | nie ustawiono `AUTH_SECRET` albo `AUTH_PASSWORD_HASH` — puść `setpassword` |
| „Failed to fetch" w przeglądarce | backend nie działa, albo adres z `frontend/.env.local` nie zgadza się z portem backendu |
| Puste tabele mimo udanego syncu | dane są, ale wybrany zakres dat ich nie obejmuje — sprawdź `cli.py coverage` |
| Sync kończy się na 401 | wygasł refresh token — powtórz krok 3 |
