#!/bin/sh
# AdsPanel - aktualizacja do wersji z tego katalogu. Uruchomienie: sudo sh aktualizuj.sh
#
# Rozpakuj nowa paczke DO TEGO SAMEGO katalogu co instalacja (nadpisze
# obrazy i skrypty; .env, adspanel.env, data/ i kopie/ zostaja nietkniete).
set -eu
cd "$(dirname "$0")"

if [ "$(id -u)" -ne 0 ]; then
    echo "Uruchom jako root: sudo sh aktualizuj.sh"
    exit 1
fi

WERSJA=$(cat WERSJA)
STARA=$(grep '^WERSJA=' .env | cut -d= -f2)
echo "== Aktualizacja $STARA -> $WERSJA"

echo "== Kopia bazy przed aktualizacja"
sh kopia.sh

gunzip -c "obrazy-$WERSJA.tar.gz" | docker load
if [ "$STARA" = "stabilna" ]; then
    # Tryb automatyczny: reczna paczka tylko przesuwa etykiete "stabilna",
    # zeby nocna aktualizacja nie przelaczyla sie potem na tryb reczny.
    OBRAZ_API=$(grep '^OBRAZ_API=' .env | cut -d= -f2-)
    OBRAZ_WEB=$(grep '^OBRAZ_WEB=' .env | cut -d= -f2-)
    docker tag "adspanel-api:$WERSJA" "$OBRAZ_API:stabilna"
    docker tag "adspanel-web:$WERSJA" "$OBRAZ_WEB:stabilna"
    STARA=""
else
    sed -i "s/^WERSJA=.*/WERSJA=$WERSJA/" .env
fi
docker compose up -d

# Stare obrazy zajmuja po kilkaset MB - zostawiamy tylko biezaca wersje.
if [ -n "$STARA" ] && [ "$STARA" != "$WERSJA" ]; then
    docker image rm "adspanel-api:$STARA" "adspanel-web:$STARA" 2>/dev/null || true
fi
echo "Gotowe - dziala wersja $WERSJA."
echo "W razie problemu powrot: ustaw WERSJA=$STARA w .env i 'docker compose up -d' (obrazy starej wersji trzeba wczytac z jej paczki)."
