"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ExternalLink, Loader2, XCircle } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Badge, Button, Card, ErrorState, Skeleton } from "@/components/ui";
import { api, type KontoAmazon } from "@/lib/api";

/**
 * Ustawienia -> Konto Amazon.
 *
 * Klient wpisuje tu klucze sam, bez informatyka i bez terminala. Backend
 * sprawdza kazdy krok w Amazonie od razu i zwraca komunikat po polsku;
 * sekrety nigdy nie wracaja do przegladarki (tylko ostatnie znaki).
 */
export default function UstawieniaPage() {
  const konto = useQuery({ queryKey: ["konto-amazon"], queryFn: () => api.kontoAmazon() });

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4">
      <div>
        <h1 className="text-lg font-semibold">Konto Amazon</h1>
        <p className="text-sm text-muted">
          Klucze dostępu do Amazona. Zapisują się wyłącznie na serwerze tego panelu i nie są nigdzie
          wysyłane poza Amazon.
        </p>
      </div>
      {konto.isPending && <Skeleton rows={8} />}
      {konto.isError && <ErrorState message={(konto.error as Error).message} />}
      {konto.data && (
        <>
          <AdsKarta stan={konto.data} />
          <SpKarta stan={konto.data} />
        </>
      )}
    </div>
  );
}

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

function Pole({
  etykieta,
  podpowiedz,
  children,
}: {
  etykieta: string;
  podpowiedz?: ReactNode;
  children: ReactNode;
}) {
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

function Region({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} className={inputCls}>
      <option value="EU">Europa (EU)</option>
      <option value="NA">Ameryka Północna (NA)</option>
      <option value="FE">Daleki Wschód (FE)</option>
    </select>
  );
}

// --------------------------------------------------------------------------- //
// Amazon Ads API
// --------------------------------------------------------------------------- //

function AdsKarta({ stan }: { stan: KontoAmazon }) {
  const qc = useQueryClient();
  const ads = stan.ads;
  const [clientId, setClientId] = useState(ads.client_id ?? "");
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
      setSekret("");
      odswiez(d);
      setWynik({ ok: true, opis: "Klucze są poprawne i zapisane. Teraz połącz konto reklamowe (krok 2)." });
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

  const kluczeZapisane = !!(ads.client_id && ads.client_secret);
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
          <Pole
            etykieta="Client Secret"
            podpowiedz={ads.client_secret ? `Zapisany (${ads.client_secret}). Zostaw puste, żeby go nie zmieniać.` : undefined}
          >
            <input
              type="password"
              value={sekret}
              onChange={(e) => setSekret(e.target.value)}
              placeholder={ads.client_secret ? "••••••••" : "amzn1.oa2-cs.v1.…"}
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
          <div>
            <Button variant="primary" onClick={() => zapisz.mutate()} disabled={zajete || !clientId}>
              {zapisz.isPending && <Loader2 className="size-3.5 animate-spin" />}
              Zapisz i sprawdź klucze
            </Button>
          </div>
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
                <Button onClick={otworzLogowanie} disabled={zajete || !kluczeZapisane}>
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
  const [clientId, setClientId] = useState(sp.client_id ?? "");
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
      setSekret("");
      setToken("");
      odswiez(d);
    },
    onError: blad,
  });
  const test = useMutation({ mutationFn: () => api.testSp(), onSuccess: odswiez, onError: blad });

  const skonfigurowane = !!(sp.client_id && sp.client_secret && sp.refresh_token);
  const zajete = zapisz.isPending || test.isPending;

  return (
    <Card
      title="Selling Partner API – sprzedaż (opcjonalnie)"
      subtitle="Sprzedaż całkowita do TACOS, pełna lista ofert, stany magazynowe. Osobna aplikacja niż reklamowa."
      action={skonfigurowane ? <Badge tone="good">Skonfigurowano</Badge> : <Badge>Nie skonfigurowano</Badge>}
    >
      <div className="flex flex-col gap-3 p-4">
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
          <Pole
            etykieta="Client Secret"
            podpowiedz={sp.client_secret ? `Zapisany (${sp.client_secret}). Puste = bez zmian.` : undefined}
          >
            <input
              type="password"
              value={sekret}
              onChange={(e) => setSekret(e.target.value)}
              placeholder={sp.client_secret ? "••••••••" : "amzn1.oa2-cs.v1.…"}
              className={inputCls}
              autoComplete="new-password"
            />
          </Pole>
          <Pole
            etykieta="Refresh Token"
            podpowiedz={sp.refresh_token ? `Zapisany (${sp.refresh_token}). Puste = bez zmian.` : undefined}
          >
            <input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder={sp.refresh_token ? "••••••••" : "Atzr|…"}
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
          <Button variant="primary" onClick={() => zapisz.mutate()} disabled={zajete || !clientId}>
            {zapisz.isPending && <Loader2 className="size-3.5 animate-spin" />}
            Zapisz i sprawdź
          </Button>
          {skonfigurowane && (
            <Button variant="ghost" onClick={() => test.mutate()} disabled={zajete}>
              {test.isPending && <Loader2 className="size-3.5 animate-spin" />}
              Sprawdź połączenie
            </Button>
          )}
        </div>
        {wynik && <Wynik ok={wynik.ok}>{wynik.opis}</Wynik>}
      </div>
    </Card>
  );
}
