# Przeniesienie AdsPanelu na inny komputer

Ta paczka zawiera całą aplikację: kod, bazę danych i konfigurację.
Nie zawiera rzeczy odtwarzalnych (`.venv`, `node_modules`, `.next`) — te
instaluje się jedną komendą na miejscu.

## Co jest w środku

| Katalog | Co to |
|---|---|
| `backend/` | API, silnik reguł, pobieranie raportów, migracje |
| `frontend/` | interfejs (Next.js) |
| `data/adspanel-transfer.db` | **baza — 690 tys. wierszy** |
| `deploy/` | instrukcja wdrożenia na serwer |
| `docs/` | instrukcje dostępu do API Amazona |
| `.env.example` | wzór konfiguracji |

**`.env` z kluczami przenosisz osobno** — nie ma go w paczce celowo.

## Czego wymaga nowy komputer

- **Python 3.11+** (tu było 3.14)
- **Node.js 20+** (tu było 24)

## Uruchomienie krok po kroku

### 1. Rozpakuj i wejdź do katalogu

```bash
cd AdsPanel
```

### 2. Baza danych

```bash
mv data/adspanel-transfer.db data/adspanel.db
```

Plik nazywa się inaczej, żeby nie dało się go pomylić z bazą roboczą.
Zrobiony jest przez mechanizm kopii SQLite, a nie przez zwykłe skopiowanie
pliku — dzięki temu zawiera też zapisy z dziennika WAL i jest spójny.

### 3. Backend

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # Windows
# .venv/bin/pip install -r requirements.txt        # Linux/Mac
```

### 4. Frontend

```bash
cd frontend
npm install
cd ..
```

### 5. Konfiguracja

Skopiuj `.env` ze starego komputera do katalogu głównego. Zawiera:

- klucze do Amazon Ads API (`ADS_*`)
- klucze do Selling Partner API (`SP_*`) — sprzedaż całkowita do TACOS
- hasło do panelu (`AUTH_PASSWORD_HASH`) i sekret sesji (`AUTH_SECRET`)

Jeśli nie masz starego `.env`, zacznij od `.env.example` i ustaw hasło:

```bash
.venv/Scripts/python backend/scripts/cli.py setpassword
```

Klucze do Amazona trzeba wtedy wziąć z Seller Central — instrukcje są
w `docs/`.

### 6. Sprawdzenie

```bash
.venv/Scripts/python backend/scripts/cli.py health
.venv/Scripts/python backend/scripts/cli.py coverage
```

Pierwsze sprawdza połączenie z Amazonem, drugie pokazuje, za jakie dni
są dane.

### 7. Start

Dwa procesy, w osobnych terminalach:

```bash
.venv/Scripts/python -m uvicorn app.main:app --app-dir backend --port 8000
```

```bash
cd frontend && npm run dev
```

Panel: <http://localhost:3000>

## Uwagi

**Danych fraz wyszukiwania starszych niż ~65 dni nie da się odzyskać** —
Amazon ich nie udostępnia. Dlatego baza jest w paczce, a nie do ściągnięcia
od nowa.

**Odświeżanie danych** chodzi automatycznie co 6 godzin po starcie backendu
(ustawienia `AUTO_REFRESH_*` w `.env`). To wyłącznie odczyt — nic nie trafia
do Amazona bez zatwierdzenia w panelu.

**Zasada, na której stoi cała aplikacja:** żadna zmiana nie idzie do Amazona
bez ręcznego zatwierdzenia w zakładce „Do zatwierdzenia". Reguły i sugestie
wyłącznie proponują.
