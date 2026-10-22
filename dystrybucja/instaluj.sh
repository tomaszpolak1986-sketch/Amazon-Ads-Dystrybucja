#!/bin/sh
# AdsPanel - pierwsza instalacja. Uruchomienie: sudo sh instaluj.sh
#
# Jesli w katalogu jest plik rejestr.token, panel instaluje sie z wlaczonymi
# aktualizacjami automatycznymi (nowe wersje i przedluzenia licencji pobiera
# sam, raz na dobe). Bez niego - aktualizacje reczne z paczek ZIP.
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
KLIENT=$(cat KLIENT)
REJESTR=$(cat REJESTR)
echo "== Wczytuje obrazy AdsPanel $WERSJA (kilka minut)"
gunzip -c "obrazy-$WERSJA.tar.gz" | docker load

AUTO=0
if [ -f rejestr.token ]; then
    echo "== Logowanie do magazynu aktualizacji ($REJESTR)"
    # Token tylko do odczytu. Docker zapisuje go w /root/.docker/config.json
    # (dostep wylacznie dla roota), wiec plik z paczki nie jest juz potrzebny.
    tr -d ' \r\n' < rejestr.token | docker login "${REJESTR%%/*}" -u "${REJESTR#*/}" --password-stdin
    rm -f rejestr.token
    AUTO=1
fi
# Ponowne uruchomienie instalatora (np. po doinstalowaniu crona) nie moze
# przelaczyc panelu na tryb reczny - token z paczki jest juz wtedy usuniety.
if [ -f .env ] && grep -q '^WERSJA=stabilna' .env; then
    AUTO=1
fi

if [ ! -f .env ]; then
    # Haslo do bazy generujemy tutaj - nikt go nie musi znac ani wpisywac.
    POSTGRES_PASSWORD=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')
    printf 'WERSJA=%s\nPOSTGRES_PASSWORD=%s\nPANEL_PORT=8080\nKLIENT=%s\nREJESTR=%s\n' \
        "$WERSJA" "$POSTGRES_PASSWORD" "$KLIENT" "$REJESTR" > .env
    chmod 600 .env
    echo "== Utworzono .env (haslo bazy wygenerowane, port panelu 8080)"
elif [ "$AUTO" = 0 ]; then
    sed -i "s/^WERSJA=.*/WERSJA=$WERSJA/" .env
fi

if [ "$AUTO" = 1 ]; then
    # Wczytane obrazy dostaja nazwe z magazynu z etykieta "stabilna" -
    # od teraz aktualizuj-auto.sh przesuwa ja na kolejne wydania. Przy
    # ponownym uruchomieniu nie cofamy juz pobranej, nowszej wersji.
    for s in api web; do
        docker image inspect "$REJESTR/adspanel-$s:stabilna" >/dev/null 2>&1 \
            || docker tag "adspanel-$s:$WERSJA" "$REJESTR/adspanel-$s:stabilna"
    done
    sed -i '/^WERSJA=/d; /^OBRAZ_API=/d; /^OBRAZ_WEB=/d' .env
    printf 'WERSJA=stabilna\nOBRAZ_API=%s/adspanel-api\nOBRAZ_WEB=%s/adspanel-web\n' "$REJESTR" "$REJESTR" >> .env
    if [ -d /etc/cron.d ]; then
        printf '30 3 * * * root sh %s/aktualizuj-auto.sh >> /var/log/adspanel-aktualizacje.log 2>&1\n' "$(pwd)" \
            > /etc/cron.d/adspanel-aktualizacje
        echo "== Aktualizacje automatyczne wlaczone (codziennie o 3:30, log: /var/log/adspanel-aktualizacje.log)"
    else
        # Minimalny Debian bywa bez crona - instalacji nie przerywamy,
        # ale bez niego nocne aktualizacje sie nie odpala.
        echo "!! Brak crona (/etc/cron.d) - aktualizacje automatyczne NIE beda sie uruchamiac."
        echo "!! Doinstaluj: sudo apt install cron   i uruchom ponownie: sudo sh instaluj.sh"
    fi
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
