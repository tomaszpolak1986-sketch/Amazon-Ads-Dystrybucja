#!/bin/bash
# Wydaje nowa wersje do magazynu - klienci z aktualizacjami automatycznymi
# dostana ja najblizszej nocy.
#
#   bash narzedzia/wydaj.sh 1.1.0
#
# Wymaga Docker Desktop i jednorazowego `docker login ghcr.io` (token z prawem
# write:packages). Przed wydaniem: test (narzedzia/testuj.sh).
set -euo pipefail
. "$(dirname "$0")/_wspolne.sh"

WERSJA=${1:?podaj wersje, np. 1.1.0}
case "$WERSJA" in *test*|stabilna|poprzednia) echo "Nieprawidlowa nazwa wersji do wydania: $WERSJA"; exit 1;; esac
wymagaj_czystego_repo
zbuduj_obrazy "$WERSJA"

for s in api web; do
    docker tag "adspanel-$s:$WERSJA" "$REJESTR/adspanel-$s:$WERSJA"
    docker tag "adspanel-$s:$WERSJA" "$REJESTR/adspanel-$s:stabilna"
    wypchnij "$REJESTR/adspanel-$s:$WERSJA"
    wypchnij "$REJESTR/adspanel-$s:stabilna"
done
# Lekki tag - nie wymaga skonfigurowanej tozsamosci gita (tag -a jej wymaga
# i po cichu sie nie tworzyl).
git -C "$ROOT" tag "v$WERSJA" 2>/dev/null || echo "Tag v$WERSJA juz istnieje - pomijam."

echo
echo "Wydano $WERSJA (commit $(git -C "$ROOT" rev-parse --short HEAD))."
echo "Klienci z aktualizacjami automatycznymi: najblizszej nocy o 3:30."
echo "Kopia kodu na GitHubie: git push origin main --tags"
