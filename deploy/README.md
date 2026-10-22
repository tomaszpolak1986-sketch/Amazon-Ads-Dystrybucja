# Wdrożenie panelu na serwer

Instrukcja dopasowana do konkretnej maszyny: Debian 13, 3,8 GB RAM, Docker 29.7,
Compose v5.5, **port 8000 zajęty**, **Postgres już działa** na `127.0.0.1:5432`.

Panel nie stawia własnego Postgresa i nie zajmuje portu 8000 — korzysta z tego,
co na serwerze już jest.

---

## 1. Baza danych

Na serwerze, w istniejącym Postgresie:

```sql
CREATE USER panel WITH PASSWORD 'wygeneruj-mocne-haslo';
CREATE DATABASE adspanel OWNER panel;
```

## 2. Pliki na serwer

```bash
rsync -av --exclude .venv --exclude node_modules --exclude .next \
      --exclude data --exclude .git \
      ./ user@serwer:/opt/adspanel/
```

`data/` i `.env` są celowo pominięte — pierwsze przenosimy osobno (krok 4),
drugie tworzymy na miejscu.

## 3. Konfiguracja

Na serwerze `/opt/adspanel/.env` — skopiuj z `.env.example` i uzupełnij:

```
ADS_CLIENT_ID=...
ADS_CLIENT_SECRET=...
ADS_REFRESH_TOKEN=...
ADS_REGION=EU

DATABASE_URL=postgresql+psycopg://panel:HASLO@host.docker.internal:5432/adspanel
PANEL_API_PORT=8010
PANEL_WEB_PORT=3010
PANEL_PUBLIC_API_URL=https://panel.twojadomena.pl/api

# Te trzy trzeba zmienić względem wartości lokalnych — patrz niżej
AUTH_COOKIE_SECURE=true
AUTH_DISABLED=false
CORS_ORIGINS=https://panel.twojadomena.pl

AUTH_SECRET=          # openssl rand -hex 32
AUTH_PASSWORD_HASH=   # ustawiany komendą z kroku 6
```

Trzy ustawienia różnią się od lokalnych i każde z nich potrafi zepsuć panel
w sposób, który wygląda na coś innego:

- **`AUTH_COOKIE_SECURE=true`** — lokalnie jest `false`, bo panel chodzi po HTTP
  i przeglądarka odrzuciłaby ciasteczko `Secure`. Na serwerze musi być `true`,
  inaczej ciasteczko sesji pojedzie po sieci otwartym tekstem. Po włączeniu
  panel **wymaga HTTPS** — po HTTP logowanie po prostu nie zadziała i będzie
  wyglądać na błędne hasło.
- **`AUTH_DISABLED` musi zostać `false`.** To awaryjny wyłącznik na pracę
  lokalną. Na serwerze widocznym z internetu oznacza, że każdy może wysyłać
  zmiany na Twoje konto reklamowe.
- **`CORS_ORIGINS`** — adres frontendu. Zostawiony `localhost` sprawi, że
  przeglądarka zablokuje wszystkie zapytania do API.

`AUTH_SECRET` wygeneruj świeży (`openssl rand -hex 32`), nie przenoś lokalnego.

`PANEL_PUBLIC_API_URL` to adres, pod którym **przeglądarka** widzi API — nie
adres kontenera. Next wkompilowuje go na stałe w kod wysyłany do przeglądarki,
więc jego zmiana wymaga przebudowania obrazu `web`, nie tylko restartu.

Uprawnienia: `chmod 600 .env` — leżą tam klucze do konta reklamowego.

## 4. Przeniesienie danych

Pełne pobranie 95 dni dla sześciu rynków trwa około czterech godzin. Migracja
gotowej bazy trwa minuty, więc warto ją przenieść zamiast ściągać od nowa.

```bash
scp data/adspanel.db user@serwer:/tmp/adspanel.db
```

Na serwerze, zanim wstaną kontenery:

```bash
cd /opt/adspanel
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

DATABASE_URL='postgresql+psycopg://panel:HASLO@127.0.0.1:5432/adspanel' \
  .venv/bin/python backend/scripts/migrate_to_postgres.py \
  --source /tmp/adspanel.db --dry-run
```

`--dry-run` pokazuje liczby wierszy po obu stronach i nic nie zapisuje. Jeśli
wygląda dobrze, powtórz bez tej flagi. Skrypt na końcu porównuje liczby wiersz
po wierszu i zgłasza rozjazd, gdyby coś nie doszło.

Po migracji skasuj kopię: `rm /tmp/adspanel.db` — zawiera dane konta.

## 4b. Migracja schematu (SB, SD, pełna baza produktowa)

**Wykonaj to przed pierwszym startem**, jeśli baza na serwerze powstała przed
dołożeniem Sponsored Brands i Display.

```bash
python backend/scripts/migrate_sb_sd.py
```

Skrypt jest idempotentny — można go puścić wielokrotnie. Dodaje:

- tabelę `listings` (pełna baza produktowa z SP-API) wraz z `asin_inferred`,
- kolumnę `ad_product` w `product_ads` i w trzech tabelach metryk,
- indeks `ix_product_daily_join` (złączenie produktów z metrykami: 5,2 s → 1,7 s).

Kolumna `ad_product` **nie jest kosmetyką**. Bez niej synchronizacja jednego
typu kampanii archiwizuje albo kasuje dane pozostałych — sprawdzone w trzech
niezależnych miejscach kodu. Wszystko, co już w tabelach leży, pochodzi ze
Sponsored Products, więc migracja nadaje `'SP'` jako wartość domyślną i nie
trzeba niczego przepisywać.

Na Postgresie kolumna dodawana jest z `DEFAULT` po stronie SQL — sam `NOT NULL`
przewróciłby się na istniejących wierszach.

## 5. Budowanie i start

Obraz frontendu buduje się z Next.js, co przy 3,8 GB RAM potrafi się otrzeć
o limit. Bezpieczniej zbudować go lokalnie i przesłać gotowy:

```bash
# lokalnie
docker build -f frontend/Dockerfile \
  --build-arg NEXT_PUBLIC_API_URL=https://panel.twojadomena.pl/api \
  -t adspanel-web:latest .
docker save adspanel-web:latest | gzip | ssh user@serwer 'gunzip | docker load'
```

Backend jest lekki i spokojnie zbuduje się na miejscu:

```bash
# na serwerze
cd /opt/adspanel

# Kontener działa jako uid 10001, a podmontowany ./data należy do Ciebie —
# bez tego zapis logów padnie na uprawnieniach, a objawi się jako milcząco
# pusty katalog logów.
mkdir -p data && sudo chown -R 10001:10001 data

docker compose build api
docker compose up -d
docker compose ps
```

## 6. Hasło do panelu

```bash
docker compose exec api python backend/scripts/cli.py setpassword
```

## 7. Reverse proxy

Oba serwisy słuchają wyłącznie na `127.0.0.1` — na świat wypuszcza je dopiero
Twój proxy. Ścieżka `/api` musi trafić do backendu, reszta do frontendu:

```nginx
location /api/ {
    proxy_pass http://127.0.0.1:8010;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}

location / {
    proxy_pass http://127.0.0.1:3010;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Przy `AUTH_COOKIE_SECURE=true` panel wymaga HTTPS — po HTTP przeglądarka
odrzuci ciasteczko sesji, a objaw będzie wyglądał jak błędne hasło.

## 8. Sprawdzenie

```bash
curl -s http://127.0.0.1:8010/api/health
docker compose logs -f api
```

Pełny przegląd po zalogowaniu:

```bash
docker compose exec api python backend/scripts/cli.py coverage
```

---

## Co robić dalej

**Codzienne odświeżanie danych** — cron na serwerze, poza kontenerem albo w nim:

```
30 6 * * * cd /opt/adspanel && docker compose exec -T api \
  python backend/scripts/sync_all_markets.py --days 14 >> data/sync-cron.log 2>&1
```

Czternaście dni wystarcza do bieżącej pracy; pełne 95 dni ma sens raz na kwartał,
bo starsze dane już się nie zmieniają.

**Kopie zapasowe** — codzienny zrzut bazy:

```
0 3 * * * pg_dump -U panel adspanel | gzip > /var/backups/adspanel-$(date +\%F).sql.gz
```

## Czego wdrożenie NIE zmienia

Reguła jest ta sama co lokalnie: **żadna zmiana nie trafia do Amazona bez
zatwierdzenia w panelu**. Scheduler reguł chodzi też na serwerze, ale wyłącznie
tworzy sugestie — jedyną drogą zapisu pozostaje ręczne zatwierdzenie planu.

## Harmonogram na serwerze

Panel sam pobiera dane — to jedyny powód, dla którego warto go trzymać na
serwerze zamiast na laptopie:

| zadanie | rytm |
|---|---|
| reguły (tworzą wyłącznie sugestie) | co 30 min |
| odświeżanie Sponsored Products | co 6 h, okno 3 dni |
| Sponsored Brands i Display | raz na dobę o 4:00, okno 14 dni |

SB i SD mają osobny rytm, bo ich raporty generują się **20–60 minut** — przy
odświeżaniu co 6 godzin kolejne zlecenie trafiałoby na jeszcze nieukończone
poprzednie. Zmierzone na koncie: trzy raporty SB za 30 dni to 81 minut.

Do zmiany w `.env`:

```
SB_SD_REFRESH_ENABLED=true
SB_SD_REFRESH_HOUR=4
SB_SD_REFRESH_DAYS=14
```

Zadanie ma dwunastogodzinną tolerancję spóźnienia — na serwerze chodzącym non
stop nie ma to znaczenia, ale gdyby maszyna była wyłączona o 4:00, przebieg
nadrobi się po starcie.

Całe pobieranie to **wyłącznie odczyt**. Zasada „żadna zmiana nie idzie do
Amazona bez ręcznego zatwierdzenia" obowiązuje tak samo na serwerze.
