# Wspolne dla skryptow wydania. Dolaczane przez `. narzedzia/_wspolne.sh`.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REJESTR="ghcr.io/tomaszpolak1986-sketch"
KLUCZE="/d/Claude Projekty/AdsPanel-KLUCZ-PRYWATNY"
PY="$ROOT/.venv/Scripts/python.exe"
export PATH="$PATH:/c/Program Files/Docker/Docker/resources/bin"

wymagaj_czystego_repo() {
    if [ -n "$(git -C "$ROOT" status --porcelain)" ]; then
        # Wydanie ma odpowiadac konkretnemu commitowi - inaczej nie wiadomo
        # potem, co dokladnie klient ma zainstalowane.
        echo "Niezacommitowane zmiany w repozytorium - najpierw commit."
        exit 1
    fi
}

# Buduje i sprawdza oba obrazy jako adspanel-api:<wersja> i adspanel-web:<wersja>
zbuduj_obrazy() {
    local wersja=$1
    cd "$ROOT"
    echo "== Backend $wersja (kompilacja Nuitka)"
    docker build -f backend/Dockerfile.wydanie --build-arg "WERSJA=$wersja" -t "adspanel-api:$wersja" .
    echo "== Frontend $wersja"
    docker build -f frontend/Dockerfile \
        --build-arg NEXT_PUBLIC_API_URL= \
        --build-arg API_INTERNAL_URL=http://api:8000 \
        -t "adspanel-web:$wersja" .

    echo "== Kontrola: w obrazie backendu nie moze byc kodu panelu"
    local zrodla
    zrodla=$(MSYS_NO_PATHCONV=1 docker run --rm --entrypoint sh "adspanel-api:$wersja" -c "find /app -name '*.py' | head -5")
    if [ -n "$zrodla" ]; then
        echo "BLAD - w obrazie sa pliki zrodlowe:"; echo "$zrodla"; exit 1
    fi
    MSYS_NO_PATHCONV=1 docker run --rm --entrypoint sh "adspanel-api:$wersja" -c \
        "cd /app && python -c 'import sys; sys.path.insert(0,\"backend\"); import app.main; from app._wydanie import WERSJA; print(\"skompilowany backend, wersja\", WERSJA)'"
}

slug() { echo "$1" | tr '[:upper:] ' '[:lower:]-'; }

# docker push z ponowieniem. GHCR potrafi odmowic ("denied") zaraz po
# utworzeniu nowego pakietu, zanim nada mu uprawnienia - sprawdzone przy
# pierwszym wydaniu 1.1.0. Po trzech nieudanych probach przerywamy wydanie.
wypchnij() {
    local proba
    for proba in 1 2 3; do
        docker push -q "$1" && return 0
        echo "Wysylanie $1 nie powiodlo sie (proba $proba/3) - ponawiam za 20 s"
        sleep 20
    done
    echo "BLAD: nie udalo sie wyslac $1 - wydanie przerwane."
    exit 1
}

# Publikuje klucz licencji klienta w magazynie (obraz z jednym plikiem).
# Uzywa go licencja-publikuj.sh i zbuduj-paczke.sh - paczka i magazyn maja
# wtedy ten sam klucz, wiec pierwsza noc u klienta nie podmienia licencji
# na "nowa" o tej samej dacie (sprawdzone na tescie 1.3.0).
publikuj_licencje() {
    local slug=$1 licencja=$2 tmp
    tmp=$(mktemp -d)
    printf '%s\n' "$licencja" > "$tmp/licencja.txt"
    printf 'FROM scratch\nCOPY licencja.txt /licencja.txt\n' > "$tmp/Dockerfile"
    docker build -q -t "$REJESTR/adspanel-licencja:$slug" "$tmp" >/dev/null
    rm -rf "$tmp"
    wypchnij "$REJESTR/adspanel-licencja:$slug"
}
