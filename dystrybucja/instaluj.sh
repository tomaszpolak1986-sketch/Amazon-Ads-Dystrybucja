#!/bin/sh
# AdsPanel - pierwsza instalacja. Uruchomienie: sudo sh instaluj.sh
set -eu
cd "$(dirname "$0")"

if [ "$(id -u)" -ne 0 ]; then
    echo "Uruchom jako root: sudo sh instaluj.sh"
    exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
    echo "Brak Dockera z wtyczka Compose. Instrukcja: https://docs.docker.com/engine/install/"
    exit 1
fi

WERSJA=$(cat WERSJA)
echo "== Wczytuje obrazy AdsPanel $WERSJA (kilka minut)"
gunzip -c "obrazy-$WERSJA.tar.gz" | docker load

if [ ! -f .env ]; then
    # Haslo do bazy generujemy tutaj - nikt go nie musi znac ani wpisywac.
    POSTGRES_PASSWORD=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')
    printf 'WERSJA=%s\nPOSTGRES_PASSWORD=%s\nPANEL_PORT=8080\n' "$WERSJA" "$POSTGRES_PASSWORD" > .env
    chmod 600 .env
    echo "== Utworzono .env (haslo bazy wygenerowane, port panelu 8080)"
else
    sed -i "s/^WERSJA=.*/WERSJA=$WERSJA/" .env
fi

if [ ! -f adspanel.env ]; then
    cp adspanel.env.wzor adspanel.env
    echo "== Utworzono adspanel.env z wzoru"
fi

mkdir -p data kopie
# Backend dziala jako uzytkownik 10001 i sam zapisuje do adspanel.env
# (haslo, token Amazona) oraz do data/ (logi).
chown 10001:10001 adspanel.env data
chmod 600 adspanel.env
chmod 700 kopie

echo "== Uruchamiam"
docker compose up -d

printf '== Czekam na backend'
for _ in $(seq 1 60); do
    if docker compose exec -T api curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
        echo " - dziala."
        break
    fi
    printf '.'
    sleep 2
done

PORT=$(grep '^PANEL_PORT=' .env | cut -d= -f2)
echo
echo "Gotowe. Panel: http://<adres-serwera>:$PORT"
echo "Dalsze kroki: INSTRUKCJA.md, punkt 4 (haslo i klucze Amazona)."
