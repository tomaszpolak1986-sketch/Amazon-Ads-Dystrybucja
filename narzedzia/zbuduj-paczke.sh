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
# Wersja juz wydana do magazynu (wydaj.sh) - bierzemy DOKLADNIE te obrazy.
# Ponowne budowanie daje te sama zawartosc, ale inne identyfikatory, a wtedy
# pierwsza nocna aktualizacja u klienta uznalaby wersje z magazynu za nowa
# (sprawdzone na tescie 1.1.0).
if docker pull -q "$REJESTR/adspanel-api:$WERSJA" >/dev/null 2>&1 \
   && docker pull -q "$REJESTR/adspanel-web:$WERSJA" >/dev/null 2>&1; then
    echo "== Obrazy $WERSJA z magazynu (wersja juz wydana)"
    docker tag "$REJESTR/adspanel-api:$WERSJA" "adspanel-api:$WERSJA"
    docker tag "$REJESTR/adspanel-web:$WERSJA" "adspanel-web:$WERSJA"
else
    zbuduj_obrazy "$WERSJA"
fi
docker pull -q postgres:17-alpine

echo "== Licencja: $KLIENT do $DO"
LICENCJA=$("$PY" "$ROOT/narzedzia/licencja.py" wydaj --klient "$KLIENT" --do "$DO" | grep '^LICENCJA=' | cut -d= -f2-)
[ -n "$LICENCJA" ] || { echo "Nie udalo sie wystawic licencji"; exit 1; }
# Ta sama licencja w magazynie - aktualizacje automatyczne jej nie podmienia
publikuj_licencje "$SLUG" "$LICENCJA"
echo "== Licencja opublikowana w magazynie (ta sama co w paczce)"

echo "== Skladanie paczki w $CEL"
rm -rf "$CEL"; mkdir -p "$CEL/pliki"
docker save "adspanel-api:$WERSJA" "adspanel-web:$WERSJA" postgres:17-alpine \
    | gzip -6 > "$CEL/pliki/obrazy-$WERSJA.tar.gz"
# Linux nie uruchomi skryptu z windowsowymi koncami linii (CRLF) - a git na
# Windows potrafi je dopisac. Kopiujemy wiec zawsze z konwersja do LF.
for f in docker-compose.yml instaluj.sh aktualizuj.sh aktualizuj-auto.sh kopia.sh INSTRUKCJA.md; do
    sed 's/\r$//' "dystrybucja/$f" > "$CEL/pliki/$f"
done
sed "s/\r$//; s|^LICENCJA=.*|LICENCJA=$LICENCJA|" dystrybucja/adspanel.env.wzor > "$CEL/pliki/adspanel.env.wzor"
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
