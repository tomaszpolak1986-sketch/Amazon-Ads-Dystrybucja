"""Znacznik wydania.

W repozytorium False - lokalnie panel chodzi bez licencji. Dockerfile przed
kompilacja nadpisuje ten plik wartoscia True, a Nuitka wkompilowuje ja na
stale. Celowo nie jest to zmienna srodowiskowa: te ustawia klient w swoim
pliku konfiguracyjnym, wiec moglby licencje po prostu wylaczyc.
"""
WYMAGA_LICENCJI = False
