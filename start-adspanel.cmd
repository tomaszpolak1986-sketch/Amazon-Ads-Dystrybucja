@echo off
rem ===================================================================
rem  AdsPanel - uruchomienie backendu i frontendu.
rem
rem  Backend po starcie sam sprawdza swiezosc danych (90 sekund pozniej)
rem  i dociaga zaleglosci: raporty reklamowe, sprzedaz calkowita oraz
rem  podzial FBA. Potem odswieza je co 6 godzin, a Sponsored Brands
rem  i Display raz na dobe o 4:00.
rem
rem  Dlatego wystarczy, ze ten plik odpali sie po zalogowaniu do Windows -
rem  reszta dzieje sie bez udzialu czlowieka.
rem
rem  Zatrzymanie: zamknij dwa okna "AdsPanel API" i "AdsPanel Web".
rem  Wylaczenie autostartu: usun skrot z folderu shell:startup.
rem ===================================================================

cd /d "%~dp0"

rem Okna startuja zminimalizowane, zeby nie zaslaniac pulpitu po zalogowaniu.
start "AdsPanel API" /min "%~dp0.venv\Scripts\python.exe" -m uvicorn app.main:app --app-dir backend --port 8000 --host 127.0.0.1

rem Frontend potrzebuje wlasnej powloki, bo npm na Windows jest skryptem .cmd.
start "AdsPanel Web" /min cmd /c "cd /d "%~dp0frontend" && npm run dev"

exit
