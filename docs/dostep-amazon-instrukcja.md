# Dostępy do konta Amazon

Podłączamy wewnętrzne narzędzie do zarządzania Waszymi kampaniami. Potrzebne są
**dwie osobne integracje** — reklamowa i sprzedażowa. Można je nadać niezależnie;
pierwsza wystarczy, żeby zacząć.

- Część I: ok. 10 minut
- Część II: ok. 15 minut plus oczekiwanie na akceptację Amazona

---

## Część I — Amazon Ads API

Daje narzędziu dostęp do kampanii: wydatków, sprzedaży z reklam i fraz
wyszukiwania. To integracja podstawowa — bez niej narzędzie nie ma czego pokazywać.

### Zacznijcie od jednego sprawdzenia

W konsoli reklamowej, w ustawieniach konta, jest lista autoryzowanych aplikacji.
To jedno spojrzenie decyduje, czy czeka Was 10 minut, czy dodatkowo dwa dni.

- **Coś tam jest** — dostęp API już istnieje, pomijacie krok 1.
- **Pusto** — trzeba złożyć wniosek, krok 1.

### 1. Wniosek o dostęp do Ads API
*(tylko gdy dostępu jeszcze nie ma — 1–2 dni)*

Wejście prowadzi z dokumentacji Amazon Ads API, przez **Get started** /
**Request access**. Wniosek składa właściciel konta albo administrator — Amazon
wiąże dostęp z kontem, nie z narzędziem.

W formularzu pada pytanie, kto i po co będzie korzystał z API. Zgodna z prawdą
odpowiedź brzmi: *wewnętrzne narzędzie do zarządzania własnymi kampaniami
reklamowymi*. To wystarcza — nie jest to aplikacja dla klientów zewnętrznych.

> Amazon czasem dopytuje mailem. Warto sprawdzać skrzynkę tej samej osoby, która
> wysyłała wniosek.

### 2. Client ID i Client Secret
*(ok. 5 minut — konsola deweloperska)*

To dane identyfikujące **aplikację**, nie osobę. Znajdują się w Security Profile
powiązanym z Waszym kontem reklamowym.

    developer.amazon.com → Login with Amazon → Security Profile → Web Settings

- **Client ID** — zaczyna się od `amzn1.application-oa2-client.`
- **Client Secret** — ukryty, pokazuje się po kliknięciu *Show Secret*

### 3. Adres zwrotny
*(ok. 1 minuta — ten sam ekran)*

W zakładce **Web Settings → Edit** jest pole **Allowed Return URLs**. Dodajcie
tam dokładnie:

    http://localhost:9090/redirect

**Musi być dokładnie taki ciąg.** Bez ukośnika na końcu, z `http` — nie `https` —
i z portem `9090`. Każda różnica, nawet jeden znak, kończy się błędem
`redirect_uri mismatch`.

> Adres wskazuje na komputer lokalny. Nie otwiera niczego na zewnątrz i nie jest
> dostępny z internetu — służy wyłącznie do odebrania odpowiedzi Amazona podczas
> jednorazowego logowania.

### 4. Rola użytkownika
*(ok. 1 minuta — konsola reklamowa)*

Osoba, która będzie korzystać z narzędzia, musi mieć na koncie reklamowym rolę
pozwalającą **zarządzać kampaniami**. Sam podgląd nie wystarczy — narzędzie nie
zobaczy wtedy części danych.

---

## Część II — raporty sprzedaży (SP-API)

Druga, całkowicie osobna integracja. Wymaga uprawnień właściciela konta, dlatego
trafia do Was, a nie do użytkownika subkonta.

### Dlaczego to osobna sprawa

Ads API zna wyłącznie sprzedaż *przypisaną do reklam*. Sprzedaży organicznej nie
zna wcale. Bez niej nie da się policzyć TACOS, czyli stosunku wydatków na reklamę
do **całego** przychodu.

Tę brakującą część daje Selling Partner API. To odrębne API, z odrębną rejestracją
i odrębnymi kluczami — istniejącej aplikacji reklamowej nie da się do tego użyć.

Daje też pełną listę ofert: ceny, stany magazynowe i produkty, których nigdy nie
reklamowano. Bez tego narzędzie widzi tylko to, co już jest w kampaniach.

### 1. Rejestracja dewelopera
*(Seller Central — konto główne)*

    Seller Central → Apps and Services → Develop Apps

> Jeśli konto zostało już przeniesione do Solution Provider Portal, ta sama strona
> jest dostępna stamtąd.

### 2. Typ integracji i rola

W sekcji **Data Access** wybierzcie opcję dla integracji własnej firmy:

    Private Developer: I build application(s) that integrate
    my own company with Amazon Services APIs

W sekcji **Roles** zaznaczcie **wyłącznie**:

    Brand Analytics

**Nie zaznaczajcie nic więcej.** Role oznaczone jako *restricted* i wszystko
związane z danymi kupujących uruchamiają ankietę bezpieczeństwa i znacznie dłuższą
procedurę. Do raportu sprzedaży nie są potrzebne — nie prosimy o żadne dane
osobowe klientów.

### 3. Klucze aplikacji
*(po akceptacji Amazona)*

Po zatwierdzeniu w tym samym miejscu pojawią się **Client ID** i **Client Secret**
— inne niż te z części I. Obie integracje mają własne, niezależne klucze.

> Rola Brand Analytics bywa nadawana z opóźnieniem — u nas trwało to około pół
> godziny po akceptacji. Jeśli pierwsze połączenie nie działa, zwykle wystarczy
> odczekać.

---

## Zakres — co to oznacza dla konta

| | |
|---|---|
| **TAK** | Narzędzie **czyta** dane kampanii i sprzedaży: wydatki, przychód, frazy wyszukiwania, stany magazynowe. |
| **TAK** | Może wprowadzać zmiany w kampaniach — stawki, budżety, wykluczenia — ale **każda wymaga osobnego, ręcznego zatwierdzenia**. Nic nie dzieje się automatycznie. |
| **NIE** | Nie ma dostępu do danych osobowych kupujących ani do metod płatności. |
| **NIE** | Nie zmienia niczego po stronie sprzedażowej — ofert, cen, stanów magazynowych ani zamówień. |
| **NIE** | Nie udostępnia niczego na zewnątrz. Dane pozostają na naszym serwerze. |
| **TAK** | Dostęp można cofnąć w każdej chwili — usuwając aplikację z listy autoryzowanych. |

---

**Do odesłania — część I:** Client ID i Client Secret z Security Profile, plus
potwierdzenie, że adres zwrotny został dodany.

**Do odesłania — część II:** Client ID i Client Secret z Seller Central.

Client Secret to hasło do konta. Prześlijcie oba menedżerem haseł albo
wiadomością, którą da się później usunąć — nie zwykłym mailem, który zostanie
w archiwum na lata.
