# Jak wygenerować refresh token do Amazon Ads

Token nie leży nigdzie gotowy w panelu Amazona — trzeba go raz wygenerować.
Załączony skrypt robi to za Ciebie. Zajmuje to około 5 minut.

## Czego potrzebujesz

- **Python 3.9 lub nowszy** — sprawdź komendą `python --version`.
  Jeśli go nie ma: [python.org/downloads](https://www.python.org/downloads/),
  przy instalacji zaznacz **„Add Python to PATH"**.
- **Client ID i Client Secret** ze swojego Security Profile.

Skrypt nie instaluje żadnych dodatkowych bibliotek i nie łączy się z niczym
poza Amazonem. Możesz go otworzyć w notatniku i przeczytać w całości.

## Krok 1 — Client ID i Client Secret

```
developer.amazon.com  →  Login with Amazon  →  Security Profile  →  Web Settings
```

- **Client ID** zaczyna się od `amzn1.application-oa2-client.`
- **Client Secret** pokazuje się po kliknięciu *Show Secret*

## Krok 2 — adres zwrotny

W tym samym miejscu, **Web Settings → Edit**, w polu **Allowed Return URLs**
dodaj dokładnie:

```
http://localhost:9090/redirect
```

Musi się zgadzać co do znaku: bez ukośnika na końcu, `http` a nie `https`,
port `9090`. Każda różnica kończy się błędem `redirect_uri mismatch`.

Ten adres wskazuje na Twój własny komputer. Nie otwiera niczego na zewnątrz
i nie jest dostępny z internetu — służy tylko do odebrania odpowiedzi Amazona.

## Krok 3 — uruchom skrypt

Otwórz terminal w folderze z plikiem i wpisz:

```
python token-amazon-ads.py
```

Skrypt zapyta o Client ID i Client Secret (sekret nie będzie widoczny podczas
wpisywania — to normalne), a potem sam otworzy przeglądarkę.

**Zaloguj się na to konto Amazon, które ma zostać podłączone.** Jeśli w
przeglądarce masz zalogowane inne, użyj okna prywatnego — skrypt wypisze link,
który możesz tam wkleić.

Po potwierdzeniu zgody wróć do terminala. Token pojawi się na ekranie między
liniami `====`.

### Gdyby przeglądarka nie zadziałała

Skrypt przejdzie w tryb ręczny i poprosi o adres z paska przeglądarki.
Skopiuj **cały** adres — strona się nie załaduje i tak ma być, liczy się
tylko to, co Amazon dopisał do adresu. Kod w nim żyje około 5 minut.

## Krok 4 — przekaż token

Skopiuj całą linię z tokenem (zaczyna się od `Atzr|`).

**Nie wysyłaj go zwykłym mailem.** To hasło do Twojego konta reklamowego,
a mail zostaje w archiwum na lata. Bezpieczne sposoby:

- menedżer haseł z funkcją udostępniania (Bitwarden Send, 1Password),
- notatka samoniszcząca (np. `privnote.com`),
- wiadomość, którą da się usunąć (Signal, WhatsApp) — i usuń ją po potwierdzeniu.

## Bezpieczeństwo

Dostęp cofniesz w każdej chwili: konsola Amazon Ads → ustawienia konta →
autoryzowane aplikacje. Token przestaje wtedy działać natychmiast.

Token traci ważność również po zmianie hasła do konta Amazon. Wtedy wystarczy
uruchomić skrypt ponownie.
