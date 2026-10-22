# AdsPanel – instrukcja instalacji dla administratora

Panel do zarządzania kampaniami Amazon Ads. Działa w trzech kontenerach Docker
(baza PostgreSQL, backend, interfejs WWW). Wszystkie dane zostają na tym
serwerze.

## 1. Wymagania

| | |
|---|---|
| System | Linux x86-64, np. Ubuntu Server 24.04 LTS lub Debian 12 |
| Zasoby | 2 vCPU, 4 GB RAM, 40 GB dysku |
| Oprogramowanie | Docker Engine z wtyczką Compose ([instalacja](https://docs.docker.com/engine/install/)) |
| Sieć wychodząca | HTTPS (443) do `*.amazon.com`, `*.amazonaws.com` (pliki raportów Amazona), `www.ecb.europa.eu` (kursy walut) |
| Sieć przychodząca | jeden port panelu (domyślnie **8080**) – tylko z sieci firmowej / VPN |

Panel **nie musi** być dostępny z internetu i nie powinien być.

## 2. Instalacja

Skopiuj paczkę na serwer i rozpakuj do katalogu docelowego, np. `/opt/adspanel`:

```bash
sudo mkdir -p /opt/adspanel
sudo unzip AdsPanel-*.zip -d /opt/adspanel
cd /opt/adspanel
sudo sh instaluj.sh
```

Skrypt wczytuje obrazy, generuje hasło do bazy, ustawia uprawnienia i uruchamia
panel. Trwa kilka minut. Port zmienisz w pliku `.env` (`PANEL_PORT`), a potem
uruchom `sudo docker compose up -d`.

## 3. Zapora

Port panelu udostępnij wyłącznie w sieci firmowej. Przykład dla `ufw`:

```bash
sudo ufw allow from 192.168.0.0/16 to any port 8080 proto tcp
```

Docker publikuje porty z pominięciem reguł `ufw`. Jeśli serwer ma publiczny
adres IP, ogranicz dostęp na zaporze sieciowej przed serwerem albo w `.env`
ustaw `PANEL_PORT=<adres-IP-w-LAN>:8080`.

## 4. Konfiguracja panelu

Wszystkie ustawienia są w pliku `adspanel.env`. Zawiera on klucze do konta
Amazon, ma uprawnienia 600 i nie należy go nigdzie kopiować.

**a) Licencja.** Jeśli pole `LICENCJA=` jest puste, wpisz klucz od dostawcy
panelu.

**b) Hasło do panelu** (minimum 10 znaków):

```bash
sudo docker compose exec api adspanel setpassword
```

**c) Klucze Amazon Ads.** Wpisz w `adspanel.env` wartości `ADS_CLIENT_ID`
i `ADS_CLIENT_SECRET`. Następnie wygeneruj token:

```bash
sudo docker compose exec api adspanel autoryzacja url
```

Otwórz wyświetlony adres w przeglądarce na dowolnym komputerze, zaloguj się na
konto Amazon z dostępem do reklam firmy i kliknij **Allow**. Przeglądarka
pokaże błąd połączenia z `localhost` – to normalne. Skopiuj **cały adres
z paska** i w ciągu 5 minut uruchom:

```bash
sudo docker compose exec api adspanel autoryzacja token --code "<wklejony adres>"
```

**d) Klucze SP-API (opcjonalnie)** – `SP_CLIENT_ID`, `SP_CLIENT_SECRET`,
`SP_REFRESH_TOKEN` w `adspanel.env`. Dają sprzedaż całkowitą (TACOS).

**e) Zastosowanie zmian i pierwsze dane:**

```bash
sudo docker compose restart api
sudo docker compose exec api adspanel health
sudo docker compose exec api adspanel profiles
sudo docker compose exec api adspanel sync --lookback 30
```

Pierwsze pobranie trwa od kilkunastu minut do kilku godzin. Potem panel sam
dociąga dane co 6 godzin.

Panel: `http://<adres-serwera>:8080`

## 5. Aktualizacja

Dostawca przesyła nową paczkę `AdsPanel-<wersja>.zip`. Rozpakuj ją **do tego
samego katalogu** (nadpisz pliki) i uruchom:

```bash
cd /opt/adspanel
sudo sh aktualizuj.sh
```

Skrypt najpierw robi kopię bazy. Pliki `.env` i `adspanel.env` oraz katalogi
`data/` i `kopie/` pozostają nietknięte.

## 6. Kopie zapasowe

Codzienna kopia bazy o 3:00, przechowywane 14 ostatnich:

```bash
echo "0 3 * * * root sh /opt/adspanel/kopia.sh >> /var/log/adspanel-kopia.log 2>&1" | sudo tee /etc/cron.d/adspanel
```

Kopie trafiają do `/opt/adspanel/kopie/`. Warto objąć ten katalog i plik
`adspanel.env` firmowym systemem kopii.

Przywrócenie z kopii:

```bash
sudo docker compose stop api
gunzip -c kopie/<plik>.sql.gz | sudo docker compose exec -T db psql -U panel -d adspanel
sudo docker compose start api
```

Przywracaj do pustej bazy, czyli po świeżej instalacji, a nie na działający panel.

## 7. Codzienna obsługa

| Co | Komenda |
|---|---|
| Stan kontenerów | `sudo docker compose ps` |
| Logi backendu | `sudo docker compose logs -f api` |
| Restart | `sudo docker compose restart` |
| Zatrzymanie | `sudo docker compose down` (dane zostają) |
| Pokrycie danymi | `sudo docker compose exec api adspanel coverage` |

**Nie używaj** `docker compose down -v`, bo ta komenda usuwa bazę.

## Ważne

- Żadna zmiana nie trafia do Amazona bez ręcznego zatwierdzenia w panelu,
  w zakładce „Do zatwierdzenia”. Automatyczne pobieranie danych to wyłącznie
  odczyt.
- Oprogramowanie jest udostępnione na licencji. Kopiowanie, modyfikowanie
  i dekompilowanie obrazów jest niedozwolone (szczegóły w umowie licencyjnej).
- Gdy licencja wygaśnie, panel pokaże komunikat. Dane pozostaną nietknięte,
  a po wpisaniu nowego klucza i `sudo docker compose restart api` panel wróci
  do działania.
