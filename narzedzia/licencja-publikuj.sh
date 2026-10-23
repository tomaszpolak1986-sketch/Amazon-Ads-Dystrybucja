#!/bin/bash
# Wystawia licencje i publikuje ja w magazynie - serwer klienta pobierze ja
# najblizszej nocy. Informatyk klienta nic nie musi robic.
#
#   bash narzedzia/licencja-publikuj.sh "Murando" 2027-04-30
#   bash narzedzia/licencja-publikuj.sh "Murando" 2027-04-30 A1B2C3D4E5F6G7
#
# Trzeci argument (opcjonalny): ID kont Amazon klienta po przecinku - licencja
# dziala wtedy tylko z tymi kontami (ID w panelu: Ustawienia -> Ads API).
set -euo pipefail
. "$(dirname "$0")/_wspolne.sh"

KLIENT=${1:?podaj klienta}
DO=${2:?podaj date waznosci RRRR-MM-DD}
SLUG=$(slug "$KLIENT")
KONTA_ARG=()
IFS=',' read -ra _K <<< "${3:-}"; for k in "${_K[@]}"; do [ -n "$k" ] && KONTA_ARG+=(--konto "$k"); done

LICENCJA=$("$PY" "$ROOT/narzedzia/licencja.py" wydaj --klient "$KLIENT" --do "$DO" "${KONTA_ARG[@]}" | grep '^LICENCJA=' | cut -d= -f2-)
[ -n "$LICENCJA" ] || { echo "Nie udalo sie wystawic licencji"; exit 1; }

publikuj_licencje "$SLUG" "$LICENCJA"

echo "Opublikowano licencje: $KLIENT do $DO ($REJESTR/adspanel-licencja:$SLUG)."
echo "Serwer klienta przyjmie ja najblizszej nocy o 3:30."
echo "Recznie (bez magazynu): linia ponizej do adspanel.env + restart api"
echo "LICENCJA=$LICENCJA"
