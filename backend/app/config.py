"""Konfiguracja aplikacji - czytana z .env w katalogu głównym projektu."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Regionalne endpointy Amazon Ads API
ADS_ENDPOINTS = {
    "NA": "https://advertising-api.amazon.com",
    "EU": "https://advertising-api-eu.amazon.com",
    "FE": "https://advertising-api-fe.amazon.com",
}

LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"

# Regionalne endpointy Selling Partner API. Ten sam token LWA co w Ads API -
# ale wystawiony dla innej aplikacji, wiec kluczy nie da sie mieszac.
SP_ENDPOINTS = {
    "NA": "https://sellingpartnerapi-na.amazon.com",
    "EU": "https://sellingpartnerapi-eu.amazon.com",
    "FE": "https://sellingpartnerapi-fe.amazon.com",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ads_client_id: str = ""
    ads_client_secret: str = ""
    ads_refresh_token: str = ""
    ads_region: str = "EU"
    # Adres zwrotny przy laczeniu konta (Allowed Return URLs w Security
    # Profile). localhost, bo Amazon przyjmuje http wylacznie dla localhost -
    # a panel w sieci firmowej chodzi po http. Kod z paska wkleja sie w panelu.
    ads_redirect_uri: str = "http://localhost:9090/redirect"

    # --- Selling Partner API (sprzedaz calkowita do TACOS) ---
    # Osobna aplikacja niz reklamowa: inne klucze, inna rejestracja. Ads API
    # nie zna sprzedazy organicznej, wiec mianownik do TACOS musi przyjsc stad.
    sp_client_id: str = ""
    sp_client_secret: str = ""
    sp_refresh_token: str = ""
    sp_region: str = "EU"

    # --- automatyczne odswiezanie danych ---
    # Pobieranie raportow to wylacznie odczyt - nic nie trafia do Amazona,
    # wiec nie ma tu czego zatwierdzac. Okno kilku dni wstecz, bo Amazon
    # dopisuje konwersje z opoznieniem i wczorajszy dzien jeszcze rosnie.
    auto_refresh_enabled: bool = True
    auto_refresh_hours: int = 6
    auto_refresh_days: int = 3

    # Sponsored Brands i Display maja wlasny rytm. Ich raporty generuja sie
    # 20-60 minut - wielokrotnie dluzej niz SP - wiec wolanie ich razem
    # z reszta co kilka godzin tylko zapychaloby kolejke u Amazona i nic nie
    # wnosilo. Raz na dobe, w nocy, w zupelnosci wystarczy.
    sb_sd_refresh_enabled: bool = True
    sb_sd_refresh_hour: int = 4
    sb_sd_refresh_days: int = 14

    # Jak dlugo policzony plan czeka na decyzje. Chodzi o to, zeby nie
    # zatwierdzic obliczen zrobionych na danych, ktore w miedzyczasie sie
    # zmienily - a dane odswiezaja sie co auto_refresh_hours, nie co kwadrans.
    # Piętnaście minut bylo za ostre: wystarczylo odejsc od biurka.
    plan_ttl_minutes: int = 120

    # --- sufit docelowego ACOS ---
    # Sugestie porownuja kazda pozycje ze srednim ACOS rynku. Sama srednia nie
    # wystarcza: rynek chodzacy na 50% ma zla srednia, wiec "podciagniecie do
    # sredniej" znaczyloby tam swiadome pchanie rentownych pozycji w gore.
    # Odniesieniem jest wiec MNIEJSZA z dwoch wartosci - srednia rynku albo ten
    # sufit. Dzieki temu na rynkach zdrowych (DE/FR/IT/ES chodza po ~15-16%)
    # nic sie nie zmienia, a slaby rynek jest ciagniety w dol, nie do wlasnej
    # slabej normy.
    #
    # 30% to prog opłacalności przy prowizji Amazona ~15% i marzy brutto ~35%.
    # Uwaga: panel liczy ACOS z kosztu netto i sprzedazy brutto, wiec 30% tutaj
    # odpowiada okolo 36% liczonym na marzy netto - kto chce ostrozniej, niech
    # ustawi 25.
    acos_ceiling: float = 30.0

    # --- rynki pomijane przy pobieraniu ---
    # Kody krajow po przecinku, np. "TR,AE,SA". Konto reklamowe potrafi miec
    # profile na rynkach, na ktorych sie nie sprzedaje - kazdy taki rynek to
    # osobny raport, czyli czas przebiegu i zuzyty limit SP-API za dane, ktore
    # sa zerami. Ustawienie, a nie stala w kodzie, bo ta sama aplikacja
    # obsluguje rozne konta i dla kogos innego te rynki moga byc kluczowe.
    skip_markets: str = ""

    database_url: str = "sqlite:///./data/adspanel.db"
    tz: str = "Europe/Warsaw"
    log_level: str = "INFO"

    # --- dostep do panelu ---
    auth_password_hash: str = ""
    auth_secret: str = ""
    # Ciasteczko Secure wymaga HTTPS. Przy dostepie po http w sieci lokalnej
    # przegladarka odrzucilaby je i logowanie nie dzialaloby wcale.
    auth_cookie_secure: bool = False
    # Awaryjny wylacznik na czas pracy lokalnej. NIGDY na serwerze dostepnym
    # z internetu - bez uwierzytelniania kazdy moze wyslac zmiany na konto.
    auth_disabled: bool = False
    # Skad wolno wolac API (frontend). Rozdzielone przecinkami.
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    # Dokumentacja API (/docs). U klienta wylaczona - opisuje cala strukture
    # backendu kazdemu, kto trafi na adres, jeszcze przed zalogowaniem.
    api_docs: bool = True

    # --- licencja ---
    # Klucz wystawiony przez autora panelu (narzedzia/licencja.py). Wymagany
    # tylko w obrazie dla klienta - patrz app/_wydanie.py.
    licencja: str = ""

    # --- automatyczne kolejkowanie ---
    # Regula uruchomiona przez scheduler tworzy plan tylko wtedy, gdy laczny
    # wydatek dotknietych pozycji siega tej kwoty. Ponizej progu zapisuje
    # znalezisko w historii, ale nie zapelnia kolejki zatwierdzen.
    # Uruchomienie reczne tworzy plan zawsze - user sam o nie poprosil.
    auto_queue_min_cost: float = 50.0

    @property
    def ads_base_url(self) -> str:
        region = self.ads_region.upper()
        if region not in ADS_ENDPOINTS:
            raise ValueError(f"Nieznany region '{self.ads_region}'. Dozwolone: {list(ADS_ENDPOINTS)}")
        return ADS_ENDPOINTS[region]

    @property
    def skip_market_list(self) -> set[str]:
        """Kody krajow do pominiecia, wielkimi literami."""
        return {k.strip().upper() for k in self.skip_markets.split(",") if k.strip()}

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def auth_ready(self) -> bool:
        return bool(self.auth_password_hash and self.auth_secret)

    @property
    def is_configured(self) -> bool:
        return bool(self.ads_client_id and self.ads_client_secret and self.ads_refresh_token)

    @property
    def sp_configured(self) -> bool:
        """Czy da sie pobierac sprzedaz calkowita bez importu CSV."""
        return bool(self.sp_client_id and self.sp_client_secret and self.sp_refresh_token)

    @property
    def sp_base_url(self) -> str:
        return SP_ENDPOINTS.get(self.sp_region.upper(), SP_ENDPOINTS["EU"])

    def resolved_database_url(self) -> str:
        """Zamienia względną ścieżkę SQLite na absolutną (żeby działało z każdego cwd)."""
        url = self.database_url
        prefix = "sqlite:///./"
        if url.startswith(prefix):
            abs_path = (PROJECT_ROOT / url[len(prefix):]).resolve()
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            return f"sqlite:///{abs_path.as_posix()}"
        return url


settings = Settings()
