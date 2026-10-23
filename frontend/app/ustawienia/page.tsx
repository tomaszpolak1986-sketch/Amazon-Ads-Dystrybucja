"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ExternalLink, KeyRound, Loader2, Lock, ShieldAlert, XCircle } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Badge, Button, Card, ErrorState, Skeleton } from "@/components/ui";
import { ApiError, api, type AdminStan, type KontoAmazon } from "@/lib/api";

/**
 * Ustawienia -> Konto Amazon.
 *
 * Klient wpisuje tu klucze sam, bez informatyka i bez terminala. Backend
 * sprawdza kazdy krok w Amazonie od razu i zwraca komunikat po polsku.
 *
 * Strone chroni osobne haslo administratora: pracownicy znaja haslo do
 * panelu, ale kluczy nie zobacza ani nie zmienia. Po zapisaniu klucze nie
 * wracaja do przegladarki wcale - widac tylko stan polaczenia i koncowki.
 */
export default function UstawieniaPage() {
  const admin = useQuery({ queryKey: ["admin-stan"], queryFn: () => api.adminStan() });

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4">
      <div>
        <h1 className="text-lg font-semibold">Konto Amazon</h1>
        <p className="text-sm text-muted">
          Klucze dostępu do Amazona. Zapisują się wyłącznie na serwerze tego panelu i nie są nigdzie
          wysyłane poza Amazon.
        </p>
      </div>
      {admin.isPending && <Skeleton rows={4} />}
      {admin.isError && <ErrorState message={(admin.error as Error).message} />}
      {admin.data && !admin.data.odblokowane && <Odblokuj />}
      {admin.data?.odblokowane && <Zawartosc admin={admin.data} />}
    </div>
  );
}

function Zawartosc({ admin }: { admin: AdminStan }) {
  const qc = useQueryClient();
  const konto = useQuery({
    queryKey: ["konto-amazon"],
    queryFn: async () => {
      try {
        return await api.kontoAmazon();
      } catch (err) {
        // Odblokowanie wygasa po 30 min - wracamy do ekranu hasla
        if (err instanceof ApiError && err.status === 403) void qc.invalidateQueries({ queryKey: ["admin-stan"] });
        throw err;
      }
    },
    retry: false,
  });

  return (
    <>
      <HasloAdministratora admin={admin} />
      {konto.isPending && <Skeleton rows={8} />}
      {konto.isError && <ErrorState message={(konto.error as Error).message} />}
      {konto.data && (
        <>
          <AdsKarta stan={konto.data} />
          <SpKarta stan={konto.data} />
        </>
      )}
    </>
  );
}

// --------------------------------------------------------------------------- //
// Wspolne
// --------------------------------------------------------------------------- //

/** Tresc `detail` z bledu API ("400 {"detail": "..."}"). */
function komunikat(err: unknown): string {
  const msg = err instanceof Error ? err.message : String(err);
  try {
    const detail = JSON.parse(msg.slice(msg.indexOf("{"))).detail;
    if (typeof detail === "string") return detail;
  } catch {
    // nie-JSON - pokazujemy surowy tekst
  }
  return msg;
}

function Wynik({ ok, children }: { ok: boolean; children: ReactNode }) {
  const Ikona = ok ? CheckCircle2 : XCircle;
  return (
    <p
      className={
        "flex items-start gap-2 rounded-lg px-3 py-2 text-xs " +
        (ok ? "bg-good-soft text-good" : "bg-bad-soft text-bad")
      }
    >
      <Ikona className="mt-px size-4 shrink-0" />
      <span>{children}</span>
    </p>
  );
}

function Pole({ etykieta, podpowiedz, children }: { etykieta: string; podpowiedz?: ReactNode; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs font-medium text-muted">{etykieta}</span>
      {children}
      {podpowiedz && <span className="text-[11px] text-muted">{podpowiedz}</span>}
    </label>
  );
}

const inputCls =
  "w-full rounded-lg border border-border bg-bg px-2.5 py-1.5 font-mono text-xs outline-none focus:border-accent";

function Haslo({ value, onChange, autoFocus }: { value: string; onChange: (v: string) => void; autoFocus?: boolean }) {
  return (
    <input
      type="password"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={inputCls}
      autoComplete="new-password"
      autoFocus={autoFocus}
    />
  );
}

function Region({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} className={inputCls}>
      <option value="EU">Europa (EU)</option>
      <option value="NA">Ameryka Północna (NA)</option>
      <option value="FE">Daleki Wschód (FE)</option>
    </select>
  );
}

/** Wiersz podsumowania zapisanych kluczy - bez wartosci, tylko koncowka. */
function Zapisany({ etykieta, koncowka }: { etykieta: string; koncowka: string | null }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-border py-1.5 text-xs last:border-0">
      <span className="text-muted">{etykieta}</span>
      {koncowka ? (
        <span className="flex items-center gap-1.5 font-mono">
          <Lock className="size-3 text-muted" />
          zapisany {koncowka}
        </span>
      ) : (
        <span className="text-muted">brak</span>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Haslo administratora
// --------------------------------------------------------------------------- //

function Odblokuj() {
  const qc = useQueryClient();
  const [haslo, setHaslo] = useState("");
  const [blad, setBlad] = useState<string | null>(null);
  const odblokuj = useMutation({
    mutationFn: () => api.adminOdblokuj(haslo),
    onSuccess: (d) => {
      setHaslo("");
      qc.setQueryData(["admin-stan"], d);
      void qc.invalidateQueries({ queryKey: ["konto-amazon"] });
    },
    onError: (err) => setBlad(komunikat(err)),
  });

  return (
    <Card title="Ustawienia zablokowane" subtitle="Klucze Amazona może przeglądać i zmieniać tylko administrator.">
      <form
        className="flex flex-col gap-3 p-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (haslo) odblokuj.mutate();
        }}
      >
        <Pole etykieta="Hasło administratora">
          <Haslo value={haslo} onChange={(v) => { setHaslo(v); setBlad(null); }} autoFocus />
        </Pole>
        {blad && <Wynik ok={false}>{blad}</Wynik>}
        <div>
          <Button type="submit" variant="primary" disabled={!haslo || odblokuj.isPending}>
            {odblokuj.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <KeyRound className="size-3.5" />}
            Odblokuj
          </Button>
        </div>
        <p className="text-[11px] text-muted">
          Nie pamiętasz hasła? Administrator serwera może ustawić nowe:{" "}
          <code className="font-mono">adspanel setadminpassword</code>
        </p>
      </form>
    </Card>
  );
}

function HasloAdministratora({ admin }: { admin: AdminStan }) {
  const qc = useQueryClient();
  const [otwarte, setOtwarte] = useState(false);
  const [obecne, setObecne] = useState("");
  const [nowe, setNowe] = useState("");
  const [powtorz, setPowtorz] = useState("");
  const [wynik, setWynik] = useState<{ ok: boolean; opis: string } | null>(null);

  const zapisz = useMutation({
    mutationFn: () => api.adminHaslo(obecne, nowe),
    onSuccess: (d) => {
      qc.setQueryData(["admin-stan"], d);
      setObecne(""); setNowe(""); setPowtorz(""); setOtwarte(false);
      setWynik({ ok: true, opis: "Hasło administratora zapisane. Ustawienia są teraz chronione." });
    },
    onError: (err) => setWynik({ ok: false, opis: komunikat(err) }),
  });
  const zablokuj = useMutation({
    mutationFn: () => api.adminZablokuj(),
    onSuccess: (d) => {
      qc.removeQueries({ queryKey: ["konto-amazon"] });
      qc.setQueryData(["admin-stan"], d);
    },
  });

  const formularz = otwarte || !admin.ustawione;
  const rozne = powtorz !== "" && nowe !== powtorz;

  return (
    <Card
      title="Hasło administratora"
      subtitle={
        admin.ustawione
          ? "Ustawienia są chronione. Pracownicy z hasłem do panelu nie widzą ani nie zmienią kluczy."
          : undefined
      }
      action={
        admin.ustawione ? (
          <div className="flex gap-2">
            {!otwarte && (
              <Button size="sm" variant="ghost" onClick={() => { setOtwarte(true); setWynik(null); }}>
                Zmień hasło
              </Button>
            )}
            <Button size="sm" onClick={() => zablokuj.mutate()} disabled={zablokuj.isPending}>
              <Lock className="size-3.5" />
              Zablokuj
            </Button>
          </div>
        ) : (
          <Badge tone="warn">Nie ustawiono</Badge>
        )
      }
    >
      {(formularz || wynik) && (
        <div className="flex flex-col gap-3 p-4">
          {!admin.ustawione && (
            <p className="flex items-start gap-2 rounded-lg bg-warn-soft px-3 py-2 text-xs text-warn">
              <ShieldAlert className="mt-px size-4 shrink-0" />
              Każdy, kto zna hasło do panelu, może teraz zmienić klucze Amazona. Ustaw osobne hasło
              administratora – inne niż hasło do panelu – i nie podawaj go pracownikom.
            </p>
          )}
          {formularz && (
            <form
              className="flex flex-col gap-3"
              onSubmit={(e) => {
                e.preventDefault();
                if (nowe && !rozne) zapisz.mutate();
              }}
            >
              {admin.ustawione && (
                <Pole etykieta="Obecne hasło administratora">
                  <Haslo value={obecne} onChange={setObecne} />
                </Pole>
              )}
              <div className="grid gap-3 sm:grid-cols-2">
                <Pole etykieta="Nowe hasło (min. 10 znaków)">
                  <Haslo value={nowe} onChange={setNowe} />
                </Pole>
                <Pole etykieta="Powtórz" podpowiedz={rozne ? "Hasła się różnią." : undefined}>
                  <Haslo value={powtorz} onChange={setPowtorz} />
                </Pole>
              </div>
              <div className="flex gap-2">
                <Button type="submit" variant="primary" disabled={zapisz.isPending || nowe.length < 10 || nowe !== powtorz}>
                  {zapisz.isPending && <Loader2 className="size-3.5 animate-spin" />}
                  Zapisz hasło administratora
                </Button>
                {admin.ustawione && (
                  <Button variant="ghost" onClick={() => setOtwarte(false)}>
                    Anuluj
                  </Button>
                )}
              </div>
            </form>
          )}
          {wynik && <Wynik ok={wynik.ok}>{wynik.opis}</Wynik>}
        </div>
      )}
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// Amazon Ads API
// --------------------------------------------------------------------------- //

function AdsKarta({ stan }: { stan: KontoAmazon }) {
  const qc = useQueryClient();
  const ads = stan.ads;
  const kluczeZapisane = !!(ads.client_id && ads.client_secret);
  const [edycja, setEdycja] = useState(!kluczeZapisane);
  const [clientId, setClientId] = useState("");
  const [sekret, setSekret] = useState("");
  const [region, setRegion] = useState(ads.region);
  const [redirect, setRedirect] = useState(ads.redirect_uri);
  const [czekaNaAdres, setCzekaNaAdres] = useState(false);
  const [adres, setAdres] = useState("");
  const [link, setLink] = useState("");
  const [wynik, setWynik] = useState<{ ok: boolean; opis: string } | null>(null);

  const odswiez = (dane: KontoAmazon) => {
    qc.setQueryData(["konto-amazon"], dane);
    if (dane.test) setWynik(dane.test);
  };
  const blad = (err: unknown) => setWynik({ ok: false, opis: komunikat(err) });

  const zapisz = useMutation({
    mutationFn: () => api.zapiszAds({ client_id: clientId, client_secret: sekret, region, redirect_uri: redirect }),
    onSuccess: (d) => {
      // Po zapisie pola znikaja - wartosci nie zostaja ani w formularzu, ani w pamieci strony
      setClientId("");
      setSekret("");
      setEdycja(false);
      odswiez(d);
      setWynik({ ok: true, opis: "Klucze są poprawne i zapisane. Teraz połącz konto reklamowe." });
    },
    onError: blad,
  });

  const polacz = useMutation({
    mutationFn: () => api.polaczAds(adres),
    onSuccess: (d) => {
      setAdres("");
      setCzekaNaAdres(false);
      odswiez(d);
      void qc.invalidateQueries({ queryKey: ["profiles"] });
    },
    onError: blad,
  });

  const test = useMutation({ mutationFn: () => api.testAds(), onSuccess: odswiez, onError: blad });

  async function otworzLogowanie() {
    setWynik(null);
    // Okno otwieramy od razu, w kliknieciu - po await przegladarka
    // potraktowalaby je jako wyskakujace okienko i zablokowala.
    const okno = window.open("", "_blank");
    try {
      const { url } = await api.linkAds();
      // Zablokowane okno: zostajemy na stronie i pokazujemy link do klikniecia.
      // Przejscie w tej samej karcie zgubiloby pole na wklejenie adresu.
      if (okno) {
        okno.opener = null;
        okno.location.href = url;
      }
      setLink(url);
      setCzekaNaAdres(true);
    } catch (err) {
      okno?.close();
      blad(err);
    }
  }

  const zajete = zapisz.isPending || polacz.isPending || test.isPending;

  return (
    <Card
      title="Amazon Ads API – kampanie reklamowe"
      subtitle="Wydatki, sprzedaż z reklam, frazy wyszukiwania i zarządzanie kampaniami."
      action={
        ads.polaczone ? (
          <Badge tone="good">Połączono</Badge>
        ) : kluczeZapisane ? (
          <Badge tone="warn">Wymaga połączenia</Badge>
        ) : (
          <Badge>Nie skonfigurowano</Badge>
        )
      }
    >
      <div className="flex flex-col gap-5 p-4">
        <section className="flex flex-col gap-3">
          <h3 className="text-sm font-medium">Krok 1. Klucze aplikacji</h3>
          {!edycja ? (
            <>
              <div className="rounded-lg border border-border px-3 py-1">
                <Zapisany etykieta="Client ID" koncowka={ads.client_id} />
                <Zapisany etykieta="Client Secret" koncowka={ads.client_secret} />
                <div className="flex items-center justify-between py-1.5 text-xs">
                  <span className="text-muted">Region</span>
                  <span className="font-mono">{ads.region}</span>
                </div>
              </div>
              <div>
                <Button size="sm" variant="ghost" onClick={() => { setEdycja(true); setWynik(null); }} disabled={zajete}>
                  Zmień klucze
                </Button>
              </div>
            </>
          ) : (
            <>
              <p className="text-xs text-muted">
                developer.amazon.com → Login with Amazon → Security Profile zatwierdzony do Amazon Ads API
                → Web Settings.
              </p>
              <Pole etykieta="Client ID">
                <input
                  value={clientId}
                  onChange={(e) => setClientId(e.target.value)}
                  placeholder="amzn1.application-oa2-client.…"
                  className={inputCls}
                  autoComplete="off"
                  spellCheck={false}
                />
              </Pole>
              <Pole etykieta="Client Secret">
                <input
                  type="password"
                  value={sekret}
                  onChange={(e) => setSekret(e.target.value)}
                  placeholder="amzn1.oa2-cs.v1.…"
                  className={inputCls}
                  autoComplete="new-password"
                />
              </Pole>
              <div className="grid gap-3 sm:grid-cols-2">
                <Pole etykieta="Region">
                  <Region value={region} onChange={setRegion} />
                </Pole>
                <Pole
                  etykieta="Adres zwrotny"
                  podpowiedz="Musi być wpisany identycznie w „Allowed Return URLs” tego Security Profile."
                >
                  <input value={redirect} onChange={(e) => setRedirect(e.target.value)} className={inputCls} />
                </Pole>
              </div>
              <div className="flex gap-2">
                <Button variant="primary" onClick={() => zapisz.mutate()} disabled={zajete || !clientId || !sekret}>
                  {zapisz.isPending && <Loader2 className="size-3.5 animate-spin" />}
                  Zapisz i sprawdź klucze
                </Button>
                {kluczeZapisane && (
                  <Button variant="ghost" onClick={() => { setEdycja(false); setClientId(""); setSekret(""); }}>
                    Anuluj
                  </Button>
                )}
              </div>
            </>
          )}
        </section>

        <section className="flex flex-col gap-3 border-t border-border pt-4">
          <h3 className="text-sm font-medium">Krok 2. Połączenie z kontem reklamowym</h3>
          {!czekaNaAdres ? (
            <>
              <p className="text-xs text-muted">
                Otworzy się logowanie Amazon. Zaloguj się na konto, które ma dostęp do reklam firmy,
                i kliknij <b>Allow</b>.
              </p>
              <div className="flex flex-wrap gap-2">
                <Button onClick={otworzLogowanie} disabled={zajete || !kluczeZapisane || edycja}>
                  <ExternalLink className="size-3.5" />
                  {ads.polaczone ? "Połącz ponownie" : "Połącz z Amazonem"}
                </Button>
                {ads.polaczone && (
                  <Button variant="ghost" onClick={() => test.mutate()} disabled={zajete}>
                    {test.isPending && <Loader2 className="size-3.5 animate-spin" />}
                    Sprawdź połączenie
                  </Button>
                )}
              </div>
            </>
          ) : (
            <>
              <ol className="list-decimal space-y-1 pl-5 text-xs text-muted">
                <li>
                  W nowej karcie zaloguj się do Amazona i kliknij <b>Allow</b>. Karta się nie otworzyła?{" "}
                  <a href={link} target="_blank" rel="noopener noreferrer" className="text-accent underline">
                    Otwórz logowanie Amazon
                  </a>
                  .
                </li>
                <li>
                  Przeglądarka pokaże stronę z błędem połączenia (<i>localhost</i>) – tak ma być.
                </li>
                <li>Skopiuj <b>cały adres z paska przeglądarki</b> tamtej karty i wklej poniżej.</li>
              </ol>
              <Pole etykieta="Adres z paska przeglądarki">
                <input
                  value={adres}
                  onChange={(e) => setAdres(e.target.value)}
                  placeholder="http://localhost:9090/redirect?code=…"
                  className={inputCls}
                  autoComplete="off"
                  spellCheck={false}
                  autoFocus
                />
              </Pole>
              <div className="flex gap-2">
                <Button variant="primary" onClick={() => polacz.mutate()} disabled={zajete || !adres.trim()}>
                  {polacz.isPending && <Loader2 className="size-3.5 animate-spin" />}
                  Zakończ łączenie
                </Button>
                <Button variant="ghost" onClick={() => setCzekaNaAdres(false)} disabled={zajete}>
                  Anuluj
                </Button>
              </div>
            </>
          )}
          {ads.konta.length > 0 && (
            <p className="text-xs text-muted">
              ID konta Amazon: <span className="font-mono text-text">{ads.konta.join(", ")}</span>
            </p>
          )}
          {ads.profile.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted">
              Profile reklamowe:
              {ads.profile.map((p) => (
                <Badge key={`${p.kraj}-${p.konto}`} tone="accent">
                  {p.kraj} {p.konto ? `· ${p.konto}` : ""}
                </Badge>
              ))}
            </div>
          )}
        </section>

        {wynik && <Wynik ok={wynik.ok}>{wynik.opis}</Wynik>}
      </div>
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// Selling Partner API
// --------------------------------------------------------------------------- //

function SpKarta({ stan }: { stan: KontoAmazon }) {
  const qc = useQueryClient();
  const sp = stan.sp;
  const skonfigurowane = !!(sp.client_id && sp.client_secret && sp.refresh_token);
  const [edycja, setEdycja] = useState(!skonfigurowane);
  const [clientId, setClientId] = useState("");
  const [sekret, setSekret] = useState("");
  const [token, setToken] = useState("");
  const [region, setRegion] = useState(sp.region);
  const [wynik, setWynik] = useState<{ ok: boolean; opis: string } | null>(null);

  const odswiez = (dane: KontoAmazon) => {
    qc.setQueryData(["konto-amazon"], dane);
    if (dane.test) setWynik(dane.test);
  };
  const blad = (err: unknown) => setWynik({ ok: false, opis: komunikat(err) });

  const zapisz = useMutation({
    mutationFn: () => api.zapiszSp({ client_id: clientId, client_secret: sekret, refresh_token: token, region }),
    onSuccess: (d) => {
      setClientId("");
      setSekret("");
      setToken("");
      setEdycja(false);
      odswiez(d);
    },
    onError: blad,
  });
  const test = useMutation({ mutationFn: () => api.testSp(), onSuccess: odswiez, onError: blad });

  const zajete = zapisz.isPending || test.isPending;

  return (
    <Card
      title="Selling Partner API – sprzedaż (opcjonalnie)"
      subtitle="Sprzedaż całkowita do TACOS, pełna lista ofert, stany magazynowe. Osobna aplikacja niż reklamowa."
      action={skonfigurowane ? <Badge tone="good">Skonfigurowano</Badge> : <Badge>Nie skonfigurowano</Badge>}
    >
      <div className="flex flex-col gap-3 p-4">
        {!edycja ? (
          <>
            <div className="rounded-lg border border-border px-3 py-1">
              <Zapisany etykieta="Client ID" koncowka={sp.client_id} />
              <Zapisany etykieta="Client Secret" koncowka={sp.client_secret} />
              <Zapisany etykieta="Refresh Token" koncowka={sp.refresh_token} />
              <div className="flex items-center justify-between py-1.5 text-xs">
                <span className="text-muted">Region</span>
                <span className="font-mono">{sp.region}</span>
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button variant="ghost" onClick={() => test.mutate()} disabled={zajete}>
                {test.isPending && <Loader2 className="size-3.5 animate-spin" />}
                Sprawdź połączenie
              </Button>
              <Button size="sm" variant="ghost" onClick={() => { setEdycja(true); setWynik(null); }} disabled={zajete}>
                Zmień klucze
              </Button>
            </div>
          </>
        ) : (
          <>
            <p className="text-xs text-muted">
              Seller Central → Aplikacje i usługi → Develop Apps. Client ID i Secret: „LWA credentials”.
              Refresh Token: strzałka przy „Edit App” → Authorize.
            </p>
            <Pole etykieta="Client ID">
              <input
                value={clientId}
                onChange={(e) => setClientId(e.target.value)}
                placeholder="amzn1.application-oa2-client.…"
                className={inputCls}
                autoComplete="off"
                spellCheck={false}
              />
            </Pole>
            <div className="grid gap-3 sm:grid-cols-2">
              <Pole etykieta="Client Secret">
                <input
                  type="password"
                  value={sekret}
                  onChange={(e) => setSekret(e.target.value)}
                  placeholder="amzn1.oa2-cs.v1.…"
                  className={inputCls}
                  autoComplete="new-password"
                />
              </Pole>
              <Pole etykieta="Refresh Token">
                <input
                  type="password"
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  placeholder="Atzr|…"
                  className={inputCls}
                  autoComplete="new-password"
                />
              </Pole>
            </div>
            <div className="max-w-xs">
              <Pole etykieta="Region">
                <Region value={region} onChange={setRegion} />
              </Pole>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button variant="primary" onClick={() => zapisz.mutate()} disabled={zajete || !clientId || !sekret || !token}>
                {zapisz.isPending && <Loader2 className="size-3.5 animate-spin" />}
                Zapisz i sprawdź
              </Button>
              {skonfigurowane && (
                <Button variant="ghost" onClick={() => { setEdycja(false); setClientId(""); setSekret(""); setToken(""); }}>
                  Anuluj
                </Button>
              )}
            </div>
          </>
        )}
        {wynik && <Wynik ok={wynik.ok}>{wynik.opis}</Wynik>}
      </div>
    </Card>
  );
}
