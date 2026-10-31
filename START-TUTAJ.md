# AdsPanel – start na nowym komputerze

Czysta kopia aplikacji: cały kod z historią zmian, bez danych, kluczy Amazona
i haseł. Służy do dalszego rozwijania panelu i wydawania wersji klientom.

## 1. Programy do zainstalowania

- **Git** – https://git-scm.com
- **Python 3.11+** – https://www.python.org
- **Node.js 20+** – https://nodejs.org
- **Docker Desktop** – https://www.docker.com/products/docker-desktop
  (przy instalacji: WSL 2; w Settings → Resources → Disk image location
  wskaż dysk z dużą ilością miejsca)
- **Claude** (aplikacja desktop, zakładka Code)

## 2. Rozpakowanie

Rozpakuj ZIP np. do `D:\Claude Projekty\AdsPanel-Dystrybucja`
(ta ścieżka jest wpisana w skryptach – inna wymaga drobnej zmiany w
`narzedzia/_wspolne.sh` i `narzedzia/licencja.py`).

## 3. Klucz prywatny licencji

Skopiuj z kopii zapasowej (pendrive) folder
`D:\Claude Projekty\AdsPanel-KLUCZ-PRYWATNY` z plikami:
`licencja_prywatny.pem`, `ghcr-odczyt.txt`, `ghcr-wydawanie.txt`.
Bez niego nie wydasz ani nie przedłużysz licencji klienta.

## 4. Pierwsze polecenie dla Claude

Otwórz folder aplikacji w Claude i napisz:

> Przeczytaj CLAUDE.md i START-TUTAJ.md. Przygotuj środowisko: venv
> z requirements.txt, npm install we frontend, zaloguj Dockera do ghcr.io
> tokenem z ghcr-wydawanie.txt, połącz repozytorium z GitHubem
> (Amazon-Ads-Dystrybucja).

Wysyłanie kodu na GitHub (`git push`) poprosi raz o zalogowanie w przeglądarce.

## 5. Uruchomienie lokalnie (do rozwijania)

```bash
.venv/Scripts/python -m uvicorn app.main:app --app-dir backend --port 8000
```

```bash
cd frontend && npm run dev
```

Panel: http://localhost:3000 (lokalnie bez licencji; hasło ustawia
`python backend/scripts/cli.py setpassword`).
