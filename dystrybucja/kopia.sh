#!/bin/sh
# AdsPanel - kopia bazy do kopie/. Codziennie z crona (INSTRUKCJA.md, punkt 6).
set -eu
cd "$(dirname "$0")"

PLIK="kopie/adspanel-$(date +%F_%H%M).sql.gz"
docker compose exec -T db pg_dump -U panel adspanel | gzip > "$PLIK"
chmod 600 "$PLIK"
echo "Kopia: $PLIK ($(du -h "$PLIK" | cut -f1))"

# Zostawiamy 14 najnowszych
ls -1t kopie/adspanel-*.sql.gz | tail -n +15 | xargs -r rm --
