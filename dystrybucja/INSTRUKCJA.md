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
| Sieć wychodząca | HTTPS (443) do `*.amazon.com`, `*.amazonaws.com` (pliki raportów Amazona), `www.ecb.europa.eu` (kursy walut), `ghcr.io` i `pkg-containers.githubusercontent.com` (aktualizacje) |
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

**c) Klucze Amazon (Ads API i SP-API).** Po zalogowaniu do panelu wejdź
w **Ustawienia** w menu po lewej. Osoba z firmy, która ma dostęp do konta
Amazon, może to zrobić sama, bez dostępu do serwera:

0. **Hasło administratora** (pierwsza rzecz na stronie Ustawień). Musi być
   inne niż hasło do panelu. Pracownicy znający tylko hasło do panelu nie
   zobaczą ani nie zmienią kluczy Amazona. Zapisanych kluczy nie da się
   podejrzeć nawet z hasłem administratora: widać tylko ich końcówki.
   Zapomniane hasło administratora ustawisz na serwerze:
   `sudo docker compose exec api adspanel setadminpassword`, a potem
   `sudo docker compose restart api`.
1. **Amazon Ads API:** wpisz Client ID i Client Secret, a potem kliknij
   **Zapisz i sprawdź klucze**. Panel od razu sprawdzi je w Amazonie.
2. Kliknij **Połącz z Amazonem**, zaloguj się na konto z dostępem do reklam
   i kliknij **Allow**. Wklej do panelu adres z paska przeglądarki (strona
   pokaże błąd połączenia z `localhost`, to normalne).
3. **SP-API (opcjonalnie, sprzedaż całkowita / TACOS):** Client ID, Client
   Secret i Refresh Token z Seller Central, a potem **Zapisz i sprawdź**.

Panel zapisuje klucze w `adspanel.env` i stosuje je od razu, bez restartu.

Wariant z terminala (zapasowy): wpisz klucze w `adspanel.env`, potem
`sudo docker compose exec api adspanel autoryzacja url`, otwórz link,
zaloguj się, a skopiowany adres przekaż do
`sudo docker compose exec api adspanel autoryzacja token --code "<adres>"`
i uruchom `sudo docker compose restart api`.

**d) Pierwsze dane.** Zaraz po pierwszym połączeniu panel sam zaczyna
pobierać dane z ostatnich 30 dni. Ręcznie: przycisk **Sync** u góry panelu
albo:

```bash
sudo docker compose exec api adspanel sync --lookback 30
```

Pierwsze pobranie trwa od kilkunastu minut do kilku godzin. Potem panel sam
dociąga dane co 6 godzin.

Panel: `http://<adres-serwera>:8080`

## 5. Aktualizacje

### Automatyczne (domyślnie, jeśli paczka zawiera plik `rejestr.token`)

Instalator włącza je sam. Codziennie o 3:30 serwer sprawdza, czy dostawca
wydał nową wersję panelu albo przedłużył licencję. Jeśli tak:

1. robi kopię bazy,
2. pobiera i uruchamia nową wersję,
3. sprawdza, czy panel działa. Jeśli nie wstanie w ciągu 3 minut, sam
   wraca do poprzedniej wersji.

Wymaga połączenia wychodzącego HTTPS do `ghcr.io` i `pkg-containers.githubusercontent.com`.
Dziennik zmian: `/var/log/adspanel-aktualizacje.log`. Bieżącą wersję widać
w lewym dolnym rogu panelu.

| Co | Komenda |
|---|---|
| Sprawdzenie od razu, bez czekania na noc | `sudo sh aktualizuj-auto.sh` |
| Wyłączenie aktualizacji automatycznych | `sudo rm /etc/cron.d/adspanel-aktualizacje` |
| Ponowne włączenie | `echo "30 3 * * * root sh /opt/adspanel/aktualizuj-auto.sh >> /var/log/adspanel-aktualizacje.log 2>&1" \| sudo tee /etc/cron.d/adspanel-aktualizacje` |

### Ręczne, z paczki ZIP

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
- Na 14 dni przed końcem licencji panel pokazuje przypomnienie. Przy
  aktualizacjach automatycznych przedłużenie od dostawcy dociera samo,
  najbliższej nocy. W trybie ręcznym nowy klucz wpisz w `adspanel.env`
  (`LICENCJA=`) i uruchom `sudo docker compose restart api`. Po wygaśnięciu
  licencji panel się blokuje, ale dane pozostają nietknięte.
