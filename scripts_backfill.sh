#!/bin/sh
# Przebieg naprawczy po awarii DNS: najpierw brakujace produkty DE, potem rynki,
# ktore poleglyby w calosci (FR/IT/ES). Sekwencyjnie - jeden rynek naraz.
set -u
cd "C:/Users/tomas/Claude Workspace/AdsPanel"
PY=./.venv/Scripts/python.exe
export PYTHONIOENCODING=utf-8

until grep -q "PODSUMOWANIE" data/sync-markets.log 2>/dev/null; do sleep 60; done
echo "=== glowny lancuch skonczony, zaczynam dogrywki ==="

echo "--- DE: raport produktow (95 dni) ---"
$PY -u backend/scripts/sync_all_markets.py --days 95 --only DE --kinds product \
    >> data/sync-backfill.log 2>&1
echo "DE produkty: $(grep -c 'wierszy z' data/sync-backfill.log) typow zapisanych"

echo "--- DE: sprawdzenie retencji fraz (okno najstarsze) ---"
$PY -u backend/scripts/cli.py reports --profile 3509384346294808 \
    --start 2026-05-21 --end 2026-06-20 --kinds search_term \
    >> data/sync-backfill.log 2>&1

echo "--- FR / IT / ES: powtorka po awarii DNS ---"
$PY -u backend/scripts/sync_all_markets.py --days 95 --only FR,IT,ES \
    >> data/sync-backfill.log 2>&1

echo "=== DOGRYWKI ZAKONCZONE ==="
$PY backend/scripts/cli.py coverage
