#!/bin/bash
# Buduje paczke instalacyjna dla klienta: obrazy bez kodu zrodlowego,
# skrypty, instrukcja i licencja w jednym pliku ZIP.
#
#   bash narzedzia/zbuduj-paczke.sh <wersja> "<klient>" <licencja-do RRRR-MM-DD>
#   bash narzedzia/zbuduj-paczke.sh 1.0.0 "Murando" 2027-10-31
#
# Wymaga Docker Desktop. Wynik: D:\Claude Projekty\AdsPanel-Paczki\
set -euo pipefail

WERSJA=${1:?podaj wersje, np. 1.0.0}
KLIENT=${2:?podaj nazwe klienta}
DO=${3:?podaj date waznosci licencji RRRR-MM-DD}

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SLUG=$(echo "$KLIENT" | tr '[:upper:] ' '[:lower:]-')
CEL="/d/Claude Projekty/AdsPanel-Paczki/$SLUG-$WERSJA"
PY="$ROOT/.venv/Scripts/python.exe"

cd "$ROOT"
if [ -n "$(git status --porcelain)" ]; then
    # Paczka ma odpowiadac konkretnemu commitowi - inaczej nie wiadomo
    # potem, co dokladnie klient ma zainstalowane.
    echo "Niezacommitowane zmiany w repozytorium - najpierw commit."
    exit 1
fi

echo "== Backend (kompilacja Nuitka, kilka-kilkanascie minut)"
docker build -f backend/Dockerfile.wydanie -t "adspanel-api:$WERSJA" .

echo "== Frontend"
docker build -f frontend/Dockerfile \
    --build-arg NEXT_PUBLIC_API_URL= \
    --build-arg API_INTERNAL_URL=http://api:8000 \
    -t "adspanel-web:$WERSJA" .

docker pull postgres:17-alpine

echo "== Kontrola: w obrazie backendu nie moze byc kodu panelu"
ZRODLA=$(docker run --rm --entrypoint sh "adspanel-api:$WERSJA" -c \
    "find /app -name '*.py' | head -5")
if [ -n "$ZRODLA" ]; then
    echo "BLAD - w obrazie sa pliki zrodlowe:"; echo "$ZRODLA"; exit 1
fi
docker run --rm --entrypoint sh "adspanel-api:$WERSJA" -c \
    "cd /app && python -c 'import sys; sys.path.insert(0,\"backend\"); import app.main; print(\"import skompilowanego backendu: ok\")'"

echo "== Licencja: $KLIENT do $DO"
LICENCJA=$("$PY" narzedzia/licencja.py wydaj --klient "$KLIENT" --do "$DO" | grep '^LICENCJA=' | cut -d= -f2-)
[ -n "$LICENCJA" ] || { echo "Nie udalo sie wystawic licencji"; exit 1; }

echo "== Skladanie paczki w $CEL"
rm -rf "$CEL"; mkdir -p "$CEL/pliki"
docker save "adspanel-api:$WERSJA" "adspanel-web:$WERSJA" postgres:17-alpine \
    | gzip -6 > "$CEL/pliki/obrazy-$WERSJA.tar.gz"
cp dystrybucja/docker-compose.yml dystrybucja/instaluj.sh dystrybucja/aktualizuj.sh \
   dystrybucja/kopia.sh dystrybucja/INSTRUKCJA.md "$CEL/pliki/"
sed "s|^LICENCJA=.*|LICENCJA=$LICENCJA|" dystrybucja/adspanel.env.wzor > "$CEL/pliki/adspanel.env.wzor"
echo "$WERSJA" > "$CEL/pliki/WERSJA"
git rev-parse --short HEAD > "$CEL/commit.txt"

ZIP_WIN=$(cygpath -w "$CEL/AdsPanel-$WERSJA.zip")
PLIKI_WIN=$(cygpath -w "$CEL/pliki")
powershell.exe -NoProfile -Command "Compress-Archive -Path '$PLIKI_WIN\\*' -DestinationPath '$ZIP_WIN' -Force"
rm -rf "$CEL/pliki"

echo
echo "Gotowe: $ZIP_WIN"
echo "Commit: $(cat "$CEL/commit.txt")  |  licencja: $KLIENT do $DO"
