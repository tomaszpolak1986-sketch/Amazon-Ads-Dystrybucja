#!/bin/sh
# AdsPanel - automatyczna aktualizacja. Uruchamiana przez crona raz na dobe
# (instaluj.sh zaklada /etc/cron.d/adspanel-aktualizacje), mozna tez recznie:
#   sudo sh aktualizuj-auto.sh
#
# 1. Licencja: pobiera klucz wystawiony dla tego klienta i podmienia go
#    w adspanel.env - tylko gdy backend potwierdzi podpis i date.
# 2. Aplikacja: pobiera wersje "stabilna". Jesli jest nowa - kopia bazy,
#    restart, sprawdzenie. Gdy nowa wersja nie wstanie w 3 minuty, wraca
#    do poprzedniej.
#
# Wylaczenie automatycznych aktualizacji: usun /etc/cron.d/adspanel-aktualizacje
set -u
cd "$(dirname "$0")"

log() { echo "$(date '+%F %T') $*"; }
env_get() { grep "^$1=" .env | cut -d= -f2-; }

WERSJA=$(env_get WERSJA)
REJESTR=$(env_get REJESTR)
KLIENT=$(env_get KLIENT)
OBRAZ_API=$(env_get OBRAZ_API)
OBRAZ_WEB=$(env_get OBRAZ_WEB)

if [ "$WERSJA" != "stabilna" ] || [ -z "$REJESTR" ]; then
    log "Tryb aktualizacji recznych (WERSJA=$WERSJA) - nic nie robie."
    exit 0
fi

backend_dziala() {
    for _ in $(seq 1 90); do
        docker compose exec -T api curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1 && return 0
        sleep 2
    done
    return 1
}

# --- 1. licencja -----------------------------------------------------------
NOWA_LICENCJA=0
OBRAZ_LIC="$REJESTR/adspanel-licencja:$KLIENT"
if docker pull -q "$OBRAZ_LIC" >/dev/null 2>&1; then
    CID=$(docker create "$OBRAZ_LIC" brak)
    # docker cp do stdout oddaje archiwum tar z jednym plikiem
    NOWA=$(docker cp "$CID:/licencja.txt" - | tar -xOf - | tr -d ' \r\n')
    docker rm "$CID" >/dev/null
    OBECNA=$(grep '^LICENCJA=' adspanel.env | cut -d= -f2-)
    if [ -n "$NOWA" ] && [ "$NOWA" != "$OBECNA" ]; then
        if docker compose exec -T api adspanel licencja "$NOWA" >/dev/null 2>&1; then
            # Klucz to base64url z kropka - bez znakow specjalnych dla sed
            sed -i "s|^LICENCJA=.*|LICENCJA=$NOWA|" adspanel.env
            NOWA_LICENCJA=1
            log "Nowy klucz licencyjny: $(docker compose exec -T api adspanel licencja "$NOWA" 2>/dev/null)"
        else
            log "Klucz licencyjny z magazynu odrzucony (zly podpis albo wygasl) - zostaje obecny."
        fi
    fi
else
    log "Nie udalo sie pobrac licencji ($OBRAZ_LIC) - zostaje obecna."
fi

# --- 2. aplikacja ----------------------------------------------------------
PRZED_API=$(docker image inspect -f '{{.Id}}' "$OBRAZ_API:stabilna" 2>/dev/null || true)
PRZED_WEB=$(docker image inspect -f '{{.Id}}' "$OBRAZ_WEB:stabilna" 2>/dev/null || true)

# Zachowujemy biezaca wersje pod osobna nazwa - pull przesunie "stabilna".
[ -n "$PRZED_API" ] && docker tag "$OBRAZ_API:stabilna" "$OBRAZ_API:poprzednia"
[ -n "$PRZED_WEB" ] && docker tag "$OBRAZ_WEB:stabilna" "$OBRAZ_WEB:poprzednia"

if ! docker pull -q "$OBRAZ_API:stabilna" >/dev/null 2>&1 || ! docker pull -q "$OBRAZ_WEB:stabilna" >/dev/null 2>&1; then
    log "Nie udalo sie pobrac nowej wersji z $REJESTR (siec albo token) - dziala dotychczasowa."
    [ "$NOWA_LICENCJA" = 1 ] && docker compose restart api >/dev/null 2>&1
    exit 1
fi

PO_API=$(docker image inspect -f '{{.Id}}' "$OBRAZ_API:stabilna")
PO_WEB=$(docker image inspect -f '{{.Id}}' "$OBRAZ_WEB:stabilna")

if [ "$PRZED_API" = "$PO_API" ] && [ "$PRZED_WEB" = "$PO_WEB" ]; then
    if [ "$NOWA_LICENCJA" = 1 ]; then
        docker compose restart api >/dev/null 2>&1
        backend_dziala && log "Licencja zastosowana." || log "UWAGA: backend nie wstal po zmianie licencji."
    else
        log "Brak nowej wersji."
    fi
    exit 0
fi

# Wersja, ktora juz raz nie wstala, nie jest probowana co noc od nowa -
# czekamy na nastepne wydanie.
if [ -f .odrzucona ] && [ "$(cat .odrzucona)" = "$PO_API $PO_WEB" ]; then
    docker tag "$OBRAZ_API:poprzednia" "$OBRAZ_API:stabilna"
    docker tag "$OBRAZ_WEB:poprzednia" "$OBRAZ_WEB:stabilna"
    log "Dostepna wersja zostala wczesniej odrzucona - czekam na nastepna."
    [ "$NOWA_LICENCJA" = 1 ] && docker compose restart api >/dev/null 2>&1
    exit 0
fi

log "Nowa wersja - kopia bazy i aktualizacja"
sh kopia.sh
docker compose up -d >/dev/null 2>&1
# up -d odtwarza tylko kontenery ze zmienionym obrazem - nowa licencja
# przy niezmienionym backendzie wymaga jawnego restartu
[ "$NOWA_LICENCJA" = 1 ] && [ "$PRZED_API" = "$PO_API" ] && docker compose restart api >/dev/null 2>&1

if backend_dziala; then
    WER=$(docker compose exec -T api curl -fsS http://127.0.0.1:8000/api/health 2>/dev/null | sed -n 's/.*"wersja":"\([^"]*\)".*/\1/p')
    log "Zaktualizowano - dziala wersja $WER."
    docker image prune -f >/dev/null 2>&1
    exit 0
fi

log "BLAD: nowa wersja nie wstala - wracam do poprzedniej."
echo "$PO_API $PO_WEB" > .odrzucona
docker tag "$OBRAZ_API:poprzednia" "$OBRAZ_API:stabilna"
docker tag "$OBRAZ_WEB:poprzednia" "$OBRAZ_WEB:stabilna"
docker compose up -d >/dev/null 2>&1
backend_dziala && log "Przywrocono poprzednia wersje." || log "BLAD: poprzednia wersja tez nie wstala - potrzebna interwencja."
exit 1
