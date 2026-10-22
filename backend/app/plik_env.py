"""Zapis ustawien do pliku .env z poziomu panelu.

Plik jest zrodlem prawdy dla konfiguracji (czyta go pydantic przy starcie),
wiec zmiany z panelu musza trafic wlasnie tam - inaczej przepadlyby przy
restarcie. Edytujemy go w miejscu, linia po linii, zeby nie ruszac komentarzy
ani kolejnosci, ktore administrator moze chciec zachowac.

Po zapisie aktualizujemy tez obiekt `settings` w pamieci: ustawienia dzialaja
od razu, bez restartu backendu.
"""
from __future__ import annotations

import threading

from .config import PROJECT_ROOT, settings

_lock = threading.Lock()


def zapisz(zmiany: dict[str, str]) -> None:
    """Ustawia KLUCZ=wartosc w .env (dopisuje brakujace) i w `settings`."""
    sciezka = PROJECT_ROOT / ".env"
    with _lock:
        linie = sciezka.read_text(encoding="utf-8").splitlines() if sciezka.exists() else []
        for klucz, wartosc in zmiany.items():
            wpis = f"{klucz}={wartosc}"
            for i, ln in enumerate(linie):
                if ln.strip().startswith(f"{klucz}="):
                    linie[i] = wpis
                    break
            else:
                linie.append(wpis)
        # write_text nadpisuje ten sam plik (ten sam i-wezel) - wazne, bo
        # w Dockerze .env jest pojedynczym plikiem podmontowanym z hosta
        sciezka.write_text("\n".join(linie) + "\n", encoding="utf-8")

        for klucz, wartosc in zmiany.items():
            # Klucz spoza Settings (np. dopisany recznie przez administratora)
            # trafia tylko do pliku - pydantic odrzucilby nieznane pole
            if klucz.lower() in type(settings).model_fields:
                setattr(settings, klucz.lower(), wartosc)
