# Nowa, niezależna instancja AdsPanelu

Ta paczka to **sam kod, bez danych i bez kluczy**. Służy do postawienia
osobnej aplikacji dla innego konta reklamowego.

Kod jest też na GitHubie (repozytorium prywatne):
`https://github.com/tomaszpolak1986-sketch/AmazonAds`. Paczka ZIP i
`git clone` dają to samo; klon ma tę przewagę, że `git pull` dociąga
późniejsze poprawki bez przenoszenia plików ręcznie.

Nie ma tu żadnej bazy ani `.env` celowo: instancja ma być niezależna,
a nie kopią tamtej. Nic z poprzedniego klienta się tu nie przeniesie.

## Czego wymaga komputer

- **Python 3.11+**
- **Node.js 20+**

## Instalacja

### 1. Zależności

```bash
python -m venv .venv
```

```bash
.venv/Scripts/pip install -r requirements.txt
```

```bash
cd frontend && npm install && cd ..
```

Na Linuksie i Macu ścieżka do pipa to `.venv/bin/pip`.

### 2. Konfiguracja

```bash
cp .env.example .env
```

Otwórz `.env` i wpisz dane **nowego** konta reklamowego:

```
ADS_CLIENT_ID=amzn1.application-oa2-client....
ADS_CLIENT_SECRET=amzn1.oa2-cs.v1....
ADS_REDIRECT_URI=http://localhost:9090/redirect
ADS_REGION=EU
```

`client_id` i `client_secret` pochodzą z Security Profile w konsoli Amazon Ads.
Jeśli klient jeszcze tego nie ma, instrukcja dla administratora konta jest
w `docs/dostep-api.html`.

### 3. Refresh token

```bash
.venv/Scripts/python backend/scripts/authorize.py login
```

Otworzy przeglądarkę, poprosi o zalogowanie na konto Amazon i sam zapisze
token w `.env`. **Zaloguj się na konto nowego klienta** — tu najłatwiej
o pomyłkę, bo przeglądarka potrafi pamiętać poprzednie logowanie. Sprawdź,
na kogo jesteś zalogowany, zanim potwierdzisz dostęp.

### 4. Hasło do panelu

```bash
.venv/Scripts/python backend/scripts/cli.py setpassword
```

### 5. Baza i pierwsze dane

```bash
.venv/Scripts/python backend/scripts/cli.py initdb
```

```bash
.venv/Scripts/python backend/scripts/cli.py health
```

```bash
.venv/Scripts/python backend/scripts/cli.py profiles
```

```bash
.venv/Scripts/python backend/scripts/cli.py sync --lookback 30
```

Kolejno: schemat bazy, sprawdzenie połączenia, pobranie profili reklamowych,
pierwsze pobranie danych. Ostatni krok trwa od kilkunastu minut do kilku
godzin — zależnie od liczby rynków i zakresu.

**Zacznij od 30 dni.** Pełne 95 dni dla kilku rynków to kilka godzin, a przy
zbyt szybkim tempie Amazon zwraca `QuotaExceeded`. Zakres można rozszerzyć
później — dane dobiorą się przyrostowo.

### 6. Start

W dwóch terminalach:

```bash
.venv/Scripts/python -m uvicorn app.main:app --app-dir backend --port 8000
```

```bash
cd frontend && npm run dev
```

Panel: <http://localhost:3000>

## Opcjonalnie: SP-API

Ads API nie zna sprzedaży organicznej ani asortymentu poza tym, co
reklamowane. SP-API dokłada trzy rzeczy — każda działa niezależnie, więc
można je włączać po kolei.

Wymaga osobnej aplikacji w Selling Partner API z rolą **Brand Analytics**.
Instrukcja dla administratora: `docs/dostep-sp-api.html`.

```
SP_CLIENT_ID=...
SP_CLIENT_SECRET=...
SP_REFRESH_TOKEN=...
SP_REGION=EU
```

```bash
.venv/Scripts/python backend/scripts/cli.py spcheck
```

**Sprzedaż całkowita — mianownik do TACOS.** Bez tego panel policzy ACOS,
ale przy TACOS postawi kreskę.

```bash
.venv/Scripts/python backend/scripts/cli.py sales --days 30
```

**Pełna baza produktowa.** Ads API zna tylko produkty, które kiedyś trafiły
do kampanii. Ten raport przynosi cały asortyment wraz z ceną, stanem
magazynowym i statusem oferty — korzysta z niego wyszukiwarka produktów przy
tworzeniu kampanii i przy dokładaniu produktów do grup.

```bash
.venv/Scripts/python backend/scripts/cli.py listings
```

**Tytuły i miniatury.** Bez nich `FBA-800-18-BIALY-5-NEW` i `b093zsryyw`
zostają gołymi kodami.

```bash
.venv/Scripts/python backend/scripts/cli.py catalog --szeroko
```

Flaga `--szeroko` obejmuje też ASIN-y konkurencji z fraz wyszukiwania
i z wykluczeń — to na nie patrzysz, decydując o wykluczeniu produktu. Bez
flagi pobiera tylko produkty własne. Przy kilku rynkach to kilkanaście minut
i tysiące wywołań API, więc puszczaj to raz, po pierwszym `sync`.

Alternatywnie, bez SP-API: zakładka **Sprzedaż całkowita** przyjmuje CSV
z raportu biznesowego Seller Central.

## Dwie instancje obok siebie na jednym komputerze

Trzeba rozdzielić porty, inaczej druga nie wstanie:

```
# .env drugiej instancji
ADS_REDIRECT_URI=http://localhost:9091/redirect
CORS_ORIGINS=http://localhost:3001
```

```bash
.venv/Scripts/python -m uvicorn app.main:app --app-dir backend --port 8001
```

```bash
cd frontend && npm run dev -- --port 3001
```

Frontend musi wiedzieć, gdzie szukać API:

```
# frontend/.env.local
NEXT_PUBLIC_API_URL=http://localhost:8001
```

## Modyfikowanie kodu niezależnie

Instancje rozjadą się w kodzie — to normalne i zamierzone. Warto od razu
założyć repozytorium, żeby dało się je porównywać i przenosić poprawki
w jedną stronę:

```bash
git init && git add -A && git commit -m "AdsPanel: punkt wyjscia"
```

`.gitignore` już wyklucza `.env`, bazę i katalogi odtwarzalne, więc klucze
nie trafią do repozytorium.

## Zasada, która obowiązuje w każdej instancji

Żadna zmiana nie idzie do Amazona bez ręcznego zatwierdzenia w zakładce
**Do zatwierdzenia**. Reguły i sugestie wyłącznie proponują — nigdy nie
wykonują. Nie ma flagi, która by to wyłączała, i nie należy jej dodawać.
