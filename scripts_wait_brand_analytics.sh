#!/bin/sh
# Czeka, az Amazon rozpropaguje role Brand Analytics.
# Konczy sie, gdy raport przestanie zwracac 403 - albo po godzinie prob.
cd "C:/Users/tomas/Claude Workspace/AdsPanel"
PY=./.venv/Scripts/python.exe
export PYTHONIOENCODING=utf-8

i=0
while [ $i -lt 20 ]; do
  i=$((i+1))
  KOD=$($PY -c "
import sys; sys.path.insert(0,'backend')
import httpx
from app.config import Settings, LWA_TOKEN_URL
s = Settings()
try:
    t = httpx.post(LWA_TOKEN_URL, data={'grant_type':'refresh_token','refresh_token':s.sp_refresh_token,
        'client_id':s.sp_client_id,'client_secret':s.sp_client_secret}, timeout=40)
    if t.status_code != 200:
        print('LWA' + str(t.status_code)); raise SystemExit
    h = {'x-amz-access-token': t.json()['access_token'], 'Accept':'application/json'}
    r = httpx.get(s.sp_base_url + '/reports/2021-06-30/reports', headers=h, timeout=45,
                  params={'reportTypes':'GET_SALES_AND_TRAFFIC_REPORT','marketplaceIds':'A1PA6795UKMFR9'})
    print(r.status_code)
except Exception as e:
    print('ERR')
" 2>/dev/null)
  echo "proba $i: $KOD"
  if [ "$KOD" = "200" ]; then
    echo "=== BRAND ANALYTICS DZIALA ==="
    exit 0
  fi
  sleep 180
done
echo "=== po godzinie nadal 403 ==="
exit 1
