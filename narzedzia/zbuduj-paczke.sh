#!/bin/bash
# Buduje paczke instalacyjna dla nowego klienta: obrazy bez kodu zrodlowego,
# skrypty, instrukcja i licencja w jednym pliku ZIP.
#
#   bash narzedzia/zbuduj-paczke.sh <wersja> "<klient>" <licencja-do RRRR-MM-DD>
#   bash narzedzia/zbuduj-paczke.sh 1.0.0 "Murando" 2027-01-31
#
# Jesli w folderze z kluczami lezy ghcr-odczyt.txt (token GitHub tylko do
# odczytu), paczka instaluje panel z aktualizacjami automatycznymi.
#
# Wymaga Docker Desktop. Wynik: D:\Claude Projekty\AdsPanel-Paczki\
set -euo pipefail
. "$(dirname "$0")/_wspolne.sh"

WERSJA=${1:?podaj wersje, np. 1.0.0}
KLIENT=${2:?podaj nazwe klienta}
DO=${3:?podaj date waznosci licencji RRRR-MM-DD}
SLUG=$(slug "$KLIENT")
CEL="/d/Claude Projekty/AdsPanel-Paczki/$SLUG-$WERSJA"

wymagaj_czystego_repo
zbuduj_obrazy "$WERSJA"
docker pull -q postgres:17-alpine

echo "== Licencja: $KLIENT do $DO"
LICENCJA=$("$PY" "$ROOT/narzedzia/licencja.py" wydaj --klient "$KLIENT" --do "$DO" | grep '^LICENCJA=' | cut -d= -f2-)
[ -n "$LICENCJA" ] || { echo "Nie udalo sie wystawic licencji"; exit 1; }

echo "== Skladanie paczki w $CEL"
rm -rf "$CEL"; mkdir -p "$CEL/pliki"
docker save "adspanel-api:$WERSJA" "adspanel-web:$WERSJA" postgres:17-alpine \
    | gzip -6 > "$CEL/pliki/obrazy-$WERSJA.tar.gz"
cp dystrybucja/docker-compose.yml dystrybucja/instaluj.sh dystrybucja/aktualizuj.sh \
   dystrybucja/aktualizuj-auto.sh dystrybucja/kopia.sh dystrybucja/INSTRUKCJA.md "$CEL/pliki/"
sed "s|^LICENCJA=.*|LICENCJA=$LICENCJA|" dystrybucja/adspanel.env.wzor > "$CEL/pliki/adspanel.env.wzor"
echo "$WERSJA" > "$CEL/pliki/WERSJA"
echo "$SLUG" > "$CEL/pliki/KLIENT"
echo "$REJESTR" > "$CEL/pliki/REJESTR"
if [ -f "$KLUCZE/ghcr-odczyt.txt" ]; then
    cp "$KLUCZE/ghcr-odczyt.txt" "$CEL/pliki/rejestr.token"
    echo "== Aktualizacje automatyczne: TAK (token z $KLUCZE/ghcr-odczyt.txt)"
else
    echo "== Aktualizacje automatyczne: NIE (brak $KLUCZE/ghcr-odczyt.txt)"
fi
git rev-parse --short HEAD > "$CEL/commit.txt"

ZIP_WIN=$(cygpath -w "$CEL/AdsPanel-$WERSJA.zip")
PLIKI_WIN=$(cygpath -w "$CEL/pliki")
powershell.exe -NoProfile -Command "Compress-Archive -Path '$PLIKI_WIN\\*' -DestinationPath '$ZIP_WIN' -Force"
rm -rf "$CEL/pliki"

echo
echo "Gotowe: $ZIP_WIN"
echo "Commit: $(cat "$CEL/commit.txt")  |  licencja: $KLIENT do $DO"
