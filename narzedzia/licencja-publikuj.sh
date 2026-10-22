#!/bin/bash
# Wystawia licencje i publikuje ja w magazynie - serwer klienta pobierze ja
# najblizszej nocy. Informatyk klienta nic nie musi robic.
#
#   bash narzedzia/licencja-publikuj.sh "Murando" 2027-04-30
set -euo pipefail
. "$(dirname "$0")/_wspolne.sh"

KLIENT=${1:?podaj klienta}
DO=${2:?podaj date waznosci RRRR-MM-DD}
SLUG=$(slug "$KLIENT")

LICENCJA=$("$PY" "$ROOT/narzedzia/licencja.py" wydaj --klient "$KLIENT" --do "$DO" | grep '^LICENCJA=' | cut -d= -f2-)
[ -n "$LICENCJA" ] || { echo "Nie udalo sie wystawic licencji"; exit 1; }

TMP=$(mktemp -d)
printf '%s\n' "$LICENCJA" > "$TMP/licencja.txt"
printf 'FROM scratch\nCOPY licencja.txt /licencja.txt\n' > "$TMP/Dockerfile"
docker build -q -t "$REJESTR/adspanel-licencja:$SLUG" "$TMP" >/dev/null
rm -rf "$TMP"
wypchnij "$REJESTR/adspanel-licencja:$SLUG"

echo "Opublikowano licencje: $KLIENT do $DO ($REJESTR/adspanel-licencja:$SLUG)."
echo "Serwer klienta przyjmie ja najblizszej nocy o 3:30."
echo "Recznie (bez magazynu): linia ponizej do adspanel.env + restart api"
echo "LICENCJA=$LICENCJA"
