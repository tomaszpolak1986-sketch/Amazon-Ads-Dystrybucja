# Wspolne dla skryptow wydania. Dolaczane przez `. narzedzia/_wspolne.sh`.

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
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
