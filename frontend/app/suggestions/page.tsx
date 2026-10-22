"use client";

/* eslint-disable @typescript-eslint/no-explicit-any */

import clsx from "clsx";
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  Ban,
  Check,
  Info,
  Loader2,
  ShieldCheck,
  EyeOff,
  Undo2,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useMemo, useState } from "react";
import { DataTable } from "@/components/DataTable";
import { EditableNumber } from "@/components/EditableCell";
import { Badge, Button, Card, ErrorState, Skeleton } from "@/components/ui";
import { api, type SuggestionActionType, type SuggestionRow } from "@/lib/api";
import { useFilters, useRangeParams } from "@/lib/filters";
import { acosTone, count, money, num, pct } from "@/lib/format";
import type { DismissedSuggestion } from "@/lib/api";
import { planKindLabel } from "@/lib/labels";
import { useData } from "@/lib/useData";

const SCOPE_LABEL: Record<string, string> = {
  CAMPAIGN: "Kampania",
  TARGET: "Targetowanie",
  PLACEMENT: "Placement",
  SEARCH_TERM: "Fraza",
  PRODUCT: "Produkt",
};

const ACTION_LABEL: Record<string, string> = {
  SET_BID: "Zmiana stawki",
  SET_BUDGET: "Zmiana budżetu",
  ADD_NEGATIVE: "Wykluczenie frazy",
  ADD_NEGATIVE_ASIN: "Wykluczenie ASIN",
  PAUSE_TARGET: "Wstrzymanie targetu",
  PAUSE_PRODUCT: "Wstrzymanie produktu",
  SET_PLACEMENT: "Modyfikator placementu",
  HARVEST: "Nowy keyword",
};

// Zakladki grupuja po rodzaju zmiany, nie po zrodle - inaczej "wykluczenia"
// bylyby rozrzucone miedzy frazami a targetami produktowymi
const GROUPS: Array<{ key: string; label: string; types: SuggestionActionType[] }> = [
  { key: "bids", label: "Stawki", types: ["SET_BID"] },
  { key: "budgets", label: "Budżety", types: ["SET_BUDGET"] },
  { key: "placements", label: "Placementy", types: ["SET_PLACEMENT"] },
  { key: "negatives", label: "Wykluczenia fraz", types: ["ADD_NEGATIVE"] },
  { key: "negasins", label: "Wykluczenia ASIN", types: ["ADD_NEGATIVE_ASIN"] },
  { key: "pauses", label: "Wstrzymania", types: ["PAUSE_TARGET"] },
  { key: "harvest", label: "Nowe keywordy", types: ["HARVEST"] },
  { key: "products", label: "Produkty", types: ["PAUSE_PRODUCT"] },
];

const ICON: Record<string, typeof Info> = {
  bid_up: ArrowUp,
  placement_up: ArrowUp,
  budget_capped: ArrowUp,
  underused: ArrowUp,
  bid_down: ArrowDown,
  placement_down: ArrowDown,
  kill: Ban,
  no_conversion: Ban,
  healthy: Check,
};

const PLACEMENT_LABEL: Record<string, string> = {
  PLACEMENT_TOP: "Góra wyników",
  PLACEMENT_REST_OF_SEARCH: "Pozostałe wyniki",
  PLACEMENT_PRODUCT_PAGE: "Karta produktu",
  SITE_AMAZON_BUSINESS: "Amazon Business (B2B)",
};

export default function SuggestionsPage() {
  const params = useRangeParams();
  const { currency, profileId, revision, bump } = useFilters();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [levelFilter, setLevelFilter] = useState<"" | "bad" | "warn" | "good">("");
  const [group, setGroup] = useState<string>("");
  const [portfolio, setPortfolio] = useState<string>("");
  // Prog probki. 1.0 to wartosc wyliczona z konwersji konta - tyle klikniec,
  // ile trzeba, zeby brak sprzedazy przestal byc przypadkiem. Nizej wpuszczamy
  // slabsze sygnaly; to zastapilo osobne zakladki w Analizie, ktore robily
  // dokladnie to samo, tylko w innym miejscu.
  const [sensitivity, setSensitivity] = useState(1);
  const [busy, setBusy] = useState(false);
  // Recznie poprawione wartosci propozycji. Klucz = ten sam co wiersza.
  const [edits, setEdits] = useState<Map<string, number>>(new Map());
  // Nadpisania typu dopasowania przy wykluczeniach - osobno od wartosci
  // liczbowych, bo to wybor z listy, a nie liczba do poprawienia.
  const [matchEdits, setMatchEdits] = useState<Map<string, string>>(new Map());
  // `skipped` niesie pozycje, ktorych nie da sie wykonac, razem z powodem.
  // Powod jest tresciwy ("brak grupy keywordowej reklamujacej te same
  // produkty"), wiec nalezy do komunikatu, a nie do konsoli.
  const [message, setMessage] = useState<{
    text: string;
    tone: "good" | "warn" | "bad";
    skipped?: { keyword: string; reason: string }[];
  } | null>(null);
  // Odrzucone sugestie: lista rozwijana pod tabela. Osobne zapytanie, bo to
  // rzadko ogladany zbior - nie ma sensu ciagnac go z kazdym odswiezeniem.
  const [showDismissed, setShowDismissed] = useState(false);
  // Na ile odrzucic: 7 = "przypomnij za tydzien" (okno danych ledwo sie
  // przesunie, sugestia zapewne wroci), 30 = okno danych wymieni sie w
  // calosci, 0 = na stale. Krocej niz tydzien nie ma sensu - Amazon dopisuje
  // konwersje do 3 dni wstecz, wiec dane pod sugestia sa jeszcze niepelne.
  const [dismissMenu, setDismissMenu] = useState(false);
  const dismissed = useData(
    ["suggestions-dismissed", profileId, revision],
    () => api.dismissedSuggestions(profileId),
  );

  const { data, loading, error, reload } = useData(
    ["suggestions", params, revision, sensitivity],
    () => api.suggestions({ ...params, limit: 1000, sensitivity }),
  );

  // Zmiana rynku uniewaznia zaznaczenie - identyfikatory naleza do innego profilu
  const [lastProfile, setLastProfile] = useState(profileId);
  if (lastProfile !== profileId) {
    setLastProfile(profileId);
    setSelected(new Set());
    setEdits(new Map());
    setMatchEdits(new Map());
    setGroup("");
    setPortfolio("");
  }

  const all = useMemo(() => data?.items ?? [], [data]);
  const threshold = data?.threshold ?? 50;

  /**
   * Kazdy licznik uwzglednia pozostale filtry, ale nie siebie.
   *
   * Wczesniej liczniki liczyly z calego zbioru, wiec po wybraniu portfolio
   * zakladki nadal sumowaly sie do 218, choc lista pokazywala 77. Wygladalo
   * to tak, jakby filtr nie zadzialal. Licznik, ktory uwzglednialby tez
   * wlasny filtr, bylby rownie bezuzyteczny - po wybraniu zakladki wszystkie
   * pozostale pokazywalyby zero.
   */
  const pasuje = useCallback(
    (
      r: SuggestionRow,
      pomin: "level" | "portfolio" | "group" | null = null,
    ) => {
      if (pomin !== "level" && levelFilter && r.level !== levelFilter) return false;
      if (pomin !== "portfolio" && portfolio && String(r.portfolio_id) !== portfolio)
        return false;
      if (pomin !== "group") {
        const types = GROUPS.find((g) => g.key === group)?.types;
        if (types && !(r.action && types.includes(r.action.type))) return false;
      }
      return true;
    },
    [levelFilter, portfolio, group],
  );

  const rows = useMemo(() => all.filter((r) => pasuje(r)), [all, pasuje]);

  /**
   * Portfolia posortowane po wydatku, nie alfabetycznie: przy dwudziestu kilku
   * pozycjach na gorze ma byc to, gdzie ida pieniadze.
   *
   * Lista obejmuje **wszystkie** portfolia z wynikow, a liczby przy nich
   * dotycza aktywnych filtrow. Zawezanie samej listy bylo bledem: przy
   * zakladce, w ktorej zostawalo jedno portfolio, selektor znikal razem
   * z mozliwoscia przelaczenia sie na inne.
   */
  const portfolios = useMemo(() => {
    const m = new Map<string, { name: string; n: number; cost: number }>();
    for (const r of all) {
      const k = String(r.portfolio_id);
      const w = m.get(k) ?? { name: r.portfolio_name, n: 0, cost: 0 };
      if (pasuje(r, "portfolio")) {
        w.n += 1;
        w.cost += r.cost ?? 0;
      }
      m.set(k, w);
    }
    return [...m.entries()].sort((a, b) => b[1].cost - a[1].cost);
  }, [all, pasuje]);

  const groupCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    const widoczne = all.filter((r) => pasuje(r, "group"));
    for (const g of GROUPS) {
      counts[g.key] = widoczne.filter(
        (r) => r.action && g.types.includes(r.action.type),
      ).length;
    }
    return counts;
  }, [all, pasuje]);

  const rowKey = (r: SuggestionRow) => `${r.scope}|${r.entity_id}|${r.code}`;

  const counts = useMemo(() => {
    const widoczne = all.filter((r) => pasuje(r, "level"));
    return {
      bad: widoczne.filter((r) => r.level === "bad").length,
      warn: widoczne.filter((r) => r.level === "warn").length,
      good: widoczne.filter((r) => r.level === "good").length,
      cost: widoczne
        .filter((r) => r.level === "bad")
        .reduce((a, r) => a + (r.cost ?? 0), 0),
      critical: widoczne.filter((r) => r.critical).length,
    };
  }, [all, pasuje]);

  const columns = useMemo(
    () => [
      {
        id: "suggestion",
        header: "Sugestia",
        meta: { width: "22%" },
        accessorFn: (r: any) => r.title,
        cell: (c: any) => {
          const r = c.row.original as SuggestionRow;
          const I = ICON[r.code] ?? (r.level === "bad" ? AlertTriangle : Info);
          return (
            // Twardy limit szerokosci, nie tylko `width` na naglowku. Tabela ma
            // uklad automatyczny, w ktorym `width` jest wylacznie sugestia -
            // dluzszy tekst i tak rozpychal kolumne, spychajac liczby poza
            // ekran. Ograniczenie zawartosci wymusza przyciecie tekstu zamiast
            // rozjezdzania sie tabeli.
            <div className="flex max-w-[26rem] min-w-0 items-start gap-1.5">
              <I
                className={clsx(
                  "mt-0.5 size-3.5 shrink-0",
                  r.level === "good" && "text-good",
                  r.level === "warn" && "text-warn",
                  r.level === "bad" && "text-bad",
                  r.level === "info" && "text-muted",
                )}
              />
              <div className="min-w-0">
                <div className="truncate text-sm font-medium">{r.title}</div>
                {/* Uzasadnienie pod tytulem, pelna tresc w dymku - inaczej
                    wiersz rosnie do czterech linii i tabela przestaje sie
                    miescic na ekranie. */}
                <div
                  className="mt-0.5 line-clamp-2 text-[11px] leading-snug text-muted"
                  title={r.detail}
                >
                  {r.detail}
                </div>
                {/* `basis` celowo nie jest tu pokazywany. Zawiera dokladnie
                    wyswietlenia, klikniecia, zamowienia i CTR - czyli cztery
                    kolumny tej samej tabeli. Te same liczby dwa razy w wierszu
                    rozpychaly go tak, ze ACOS i wydatek wypadaly poza ekran. */}
              </div>
            </div>
          );
        },
      },
      {
        id: "entity",
        header: "Czego dotyczy",
        meta: { width: "18%" },
        accessorFn: (r: any) => r.entity_name ?? "",
        cell: (c: any) => {
          const r = c.row.original as SuggestionRow;
          const name =
            r.scope === "PLACEMENT"
              ? (PLACEMENT_LABEL[r.entity_name ?? ""] ?? r.entity_name)
              : r.entity_name;
          return (
            // Ten sam powod co przy kolumnie obok: bez gornego limitu dluga
            // nazwa kampanii rozciaga kolumne i wypycha metryki w prawo.
            <div className="max-w-[19rem] min-w-0">
              <div className="flex items-center gap-1.5">
                <Badge tone="neutral">{SCOPE_LABEL[r.scope] ?? r.scope}</Badge>
                <span className="truncate text-xs font-medium" title={name ?? ""}>
                  {name || "—"}
                </span>
              </div>
              {r.campaign_name && (
                <div className="mt-0.5 flex items-center gap-1.5 text-[11px]">
                  <Link
                    href={`/campaigns/${r.campaign_id}`}
                    className="truncate text-accent hover:underline"
                  >
                    {r.campaign_name}
                  </Link>
                  {/* Portfolio pokazujemy tylko przy widoku zbiorczym - przy
                      wybranym i tak jest w naglowku, a tu zabieraloby miejsce. */}
                  {!portfolio && r.portfolio_id !== 0 && (
                    <span className="shrink-0 text-muted" title="Portfolio">
                      · {r.portfolio_name}
                    </span>
                  )}
                </div>
              )}
            </div>
          );
        },
      },
      {
        id: "change",
        header: "Zmiana",
        meta: { align: "right", width: "12%" },
        enableSorting: false,
        cell: (c: any) => {
          const r = c.row.original as SuggestionRow;
          const a = r.action;
          if (!a) return <span className="text-muted">—</span>;

          const isPct = a.type === "SET_PLACEMENT";
          const fmt = (v: number | null) =>
            v === null || v === undefined
              ? "—"
              : isPct
                ? `+${Math.round(v)}%`
                : money(v, currency);

          // Wykluczenie nie ma wartosci liczbowej, ale ma typ dopasowania -
          // a to decyzja o zasiegu blokady, wiec musi byc widoczna i zmienialna.
          if (a.type === "ADD_NEGATIVE") {
            const key = rowKey(r);
            const wybrane = matchEdits.get(key) ?? a.match_type ?? "NEGATIVE_EXACT";
            return (
              <div className="flex flex-col items-end gap-0.5">
                <select
                  value={wybrane}
                  onClick={(e) => e.stopPropagation()}
                  onChange={(e) =>
                    setMatchEdits((m) => new Map(m).set(key, e.target.value))
                  }
                  className="rounded-lg border border-border bg-bg px-1.5 py-0.5 text-xs outline-none focus:border-accent"
                  title="Ścisłe blokuje dokładnie tę frazę. Wyrażenie blokuje każde zapytanie, które ją zawiera."
                >
                  <option value="NEGATIVE_EXACT">wyklucz ścisłe</option>
                  <option value="NEGATIVE_PHRASE">wyklucz wyrażenie</option>
                </select>
                {/* Podpowiedz pojawia sie tylko wtedy, gdy dane ja uzasadniaja:
                    slowo wraca w kilku frazach i w zadnej nie sprzedaje. */}
                {r.pattern && wybrane === "NEGATIVE_EXACT" && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      setMatchEdits((m) => new Map(m).set(key, "NEGATIVE_PHRASE"));
                    }}
                    className="text-[10px] text-accent hover:underline"
                    title={`Słowo „${r.pattern.token}" wraca w ${r.pattern.terms} niesprzedających frazach i w żadnej sprzedającej. Wyrażenie zablokuje je naraz.`}
                  >
                    {`„${r.pattern.token}”`} w {r.pattern.terms} frazach → wyrażenie
                  </button>
                )}
              </div>
            );
          }

          // Pozostale akcje bez wartosci liczbowej (pauza) - nie ma czego edytowac
          if (a.value === undefined || a.value === null) {
            return <span className="text-xs text-muted">{ACTION_LABEL[a.type]}</span>;
          }

          const key = rowKey(r);
          // Przy nowym keywordzie nie ma poprzedniej wartosci - przekreslona
          // kreska ze strzalka sugerowalaby zmiane, a to jest dodanie.
          const nowy = a.from === undefined || a.from === null;
          // Widelki Amazona pokazujemy tylko przy stawkach - przy budzetach
          // czy wykluczeniach nie maja sensu.
          const w = r.amazon_bid;
          // Widelki bez symbolu waluty. Ta sama kolumna pokazuje juz kwote
          // z waluta obok, wiec powtorzenie jej dwa razy w nawiasie nic nie
          // wnosilo, a dokladalo szerokosci akurat tam, gdzie jej brakuje.
          const widelki =
            !isPct && w && w.low != null && w.high != null ? (
              <span
                className="tnum text-[11px] whitespace-nowrap text-muted"
                title={`Amazon rekomenduje ${money(w.median ?? 0, currency)} w przedziale ${money(w.low, currency)}–${money(w.high, currency)}`}
              >
                ({money(w.low, currency).replace(/\s*[^\d.,\s-]+$/, "")}–
                {money(w.high, currency).replace(/\s*[^\d.,\s-]+$/, "")})
              </span>
            ) : null;
          return (
            <span className="inline-flex items-center justify-end gap-1 text-xs">
              {nowy ? (
                // Sam znak zamiast "Nowy keyword za" - nazwa akcji jest juz
                // w zakladce i w kolumnie Sugestia, a tu zabierala pol kolumny.
                <span className="text-muted" title={ACTION_LABEL[a.type]}>
                  +
                </span>
              ) : (
                <>
                  <span className="tnum text-muted line-through">{fmt(a.from ?? null)}</span>
                  <span className="text-muted">→</span>
                </>
              )}
              <EditableNumber
                value={a.value}
                draft={edits.get(key)}
                onChange={(v) => setEdits((m) => new Map(m).set(key, v))}
                onReset={() =>
                  setEdits((m) => {
                    const next = new Map(m);
                    next.delete(key);
                    return next;
                  })
                }
                format={(v) => fmt(v)}
                min={isPct ? 0 : 0.02}
                max={isPct ? 900 : 1000}
              />
                          {widelki}
            </span>
          );
        },
      },
      // Kolejnosc kolumn nie jest kosmetyczna. Tresc sugestii i nazwa obiektu
      // rozpychaja tabele na tyle, ze dalsze kolumny wypadaja poza ekran
      // i trzeba przewijac w bok. ACOS, zamowienia i wydatek to liczby, na
      // ktorych podejmuje sie decyzje - musza byc widoczne bez przewijania,
      // a wyswietlenia, klikniecia i CTR moga zostac na koncu.
      {
        accessorKey: "acos",
        header: "ACOS",
        meta: { align: "right" },
        cell: (c: any) => (
          <span className={acosTone(c.getValue())}>{pct(c.getValue())}</span>
        ),
      },
      {
        accessorKey: "orders",
        header: "Zam.",
        meta: { align: "right" },
        cell: (c: any) => num(c.getValue()),
      },
      {
        accessorKey: "cost",
        header: "Wydatek",
        meta: { align: "right" },
        cell: (c: any) => {
          const r = c.row.original as SuggestionRow;
          return (
            <span className={r.critical ? "font-medium text-bad" : undefined}>
              {money(c.getValue(), currency)}
            </span>
          );
        },
      },
      {
        accessorKey: "impressions",
        header: "Wyśw.",
        meta: { align: "right" },
        cell: (c: any) => <span className="text-muted">{num(c.getValue())}</span>,
      },
      {
        accessorKey: "clicks",
        header: "Klik.",
        meta: { align: "right" },
        cell: (c: any) => num(c.getValue()),
      },
      {
        accessorKey: "ctr",
        header: "CTR",
        meta: { align: "right" },
        cell: (c: any) => <span className="text-muted">{pct(c.getValue(), 2)}</span>,
      },
    ],
    [currency, edits, matchEdits, portfolio],
  );

  async function dismissSelected(days: number | null) {
    if (!profileId) return;
    const items = all
      .filter((r) => selected.has(rowKey(r)))
      .map((r) => ({
        scope: r.scope,
        entity_id: r.entity_id,
        code: r.code,
        title: r.title,
        entity_name: r.entity_name ?? r.campaign_name ?? null,
      }));
    if (!items.length) return;
    setBusy(true);
    setMessage(null);
    try {
      const res = await api.dismissSuggestions(profileId, items, days);
      setMessage({
        text:
          `Odrzucono ${count(res.dismissed, "sugestię", "sugestie", "sugestii")}` +
          (days ? ` na ${days} dni` : " na stałe") +
          ". Nic nie poszło do Amazona — to tylko Twoja decyzja, żeby tego nie pokazywać.",
        tone: "good",
      });
      setSelected(new Set());
      reload();
      dismissed.reload();
    } catch (err) {
      setMessage({ text: (err as Error).message.slice(0, 200), tone: "bad" });
    } finally {
      setBusy(false);
    }
  }

  async function restoreDismissed(d: DismissedSuggestion) {
    try {
      await api.restoreSuggestion(d.id);
      reload();
      dismissed.reload();
    } catch (err) {
      setMessage({ text: (err as Error).message.slice(0, 200), tone: "bad" });
    }
  }

  async function queueSelected() {
    if (!profileId) return;
    const actions = all
      .filter((r) => selected.has(rowKey(r)) && r.action)
      .map((r) => {
        const key = rowKey(r);
        const edited = edits.get(key);
        const match = matchEdits.get(key);
        // Recznie poprawiona wartosc i typ dopasowania maja pierwszenstwo
        // nad wyliczonymi. Powod dokladamy tutaj, bo w kolejce zostaje sam
        // identyfikator - a z niego nie da sie poznac, o co szlo. Kampanie
        // kolejka rozwiazuje sama, wiec nie ma sensu jej tu powtarzac.
        return {
          ...r.action!,
          reason: r.title,
          ...(edited !== undefined ? { value: edited } : {}),
          ...(match !== undefined ? { match_type: match } : {}),
        };
      });
    if (!actions.length) return;

    setBusy(true);
    setMessage(null);
    try {
      const res = await api.queueSuggestions(profileId, actions, params);
      // Jedno klikniecie potrafi utworzyc kilka planow, bo backend rozbija
      // zaznaczenie po rodzaju zmiany. Bez wypisania ich z nazwy wyglada to
      // tak, jakby panel zrobil cos wiecej, niz sie prosilo.
      const rozbicie = (res.plans ?? [])
        .filter((p) => p.plan_id)
        .map((p) => `${planKindLabel(p.kind)} (${p.item_count})`)
        .join(", ");

      // Pozycje, ktorych nie da sie wykonac, wracaja z powodem zamiast
      // trafiac do kolejki. Bez ich pokazania zostawal sam komunikat
      // "utworzono 0 planow", ktory wyglada jak zepsuty przycisk - a panel
      // przez caly czas wie, czego brakuje.
      const pominiete = (res.plans ?? []).flatMap((p) => p.skipped ?? []);

      if (!res.queued) {
        setMessage({
          text:
            pominiete.length
              ? `Nic nie trafiło do kolejki — ${count(pominiete.length, "pozycja", "pozycje", "pozycji")} ${pominiete.length === 1 ? "odpadła" : "odpadły"}.`
              : "Nic nie trafiło do kolejki.",
          tone: "warn",
          skipped: pominiete,
        });
      } else {
        setMessage({
          text:
            `Utworzono ${count(res.queued, "plan", "plany", "planów")}` +
            (rozbicie ? `: ${rozbicie}` : "") +
            `. Nic nie zostało wysłane — zatwierdź w kolejce.` +
            (pominiete.length
              ? ` ${count(pominiete.length, "pozycja", "pozycje", "pozycji")} odpadły.`
              : ""),
          tone: pominiete.length ? "warn" : "good",
          skipped: pominiete,
        });
      }
      // Zaznaczenie czyscimy tylko z tego, co faktycznie poszlo do kolejki.
      // Pozycja, ktora odpadla, ma zostac zaznaczona - inaczej znika bez
      // sladu i trzeba jej szukac po liscie od nowa.
      if (res.queued) {
        setSelected(new Set());
        setEdits(new Map());
      }
      reload();
      bump();
    } catch (err) {
      setMessage({ text: (err as Error).message.slice(0, 200), tone: "bad" });
    } finally {
      setBusy(false);
    }
  }

  if (error) return <ErrorState message={error} />;

  const blocked = profileId === null;

  // Selektor pokazujemy zawsze, gdy jakiekolwiek portfolio istnieje.
  // Chowanie go przy jednym bylo mylace: na DE (12 portfolii) byl, a po
  // przelaczeniu na ES (jedno) znikal i wygladalo to na usterke. Przy jednym
  // portfolio nadal mowi, czego dotycza sugestie.
  const portfoliosSelect =
    portfolios.length > 0 ? (
            <select
              value={portfolio}
              onChange={(e) => {
                setPortfolio(e.target.value);
                setSelected(new Set());
              }}
              className="max-w-[16rem] rounded-lg border border-border bg-bg px-2 py-1 text-sm outline-none focus:border-accent"
            >
              <option value="">
                Wszystkie portfolia — {all.filter((r) => pasuje(r, "portfolio")).length}{" "}
                sugestii
              </option>
              {portfolios.map(([id, p]) => (
                <option key={id} value={id}>
                  {p.name} — {p.n} / {money(p.cost, currency)}
                </option>
              ))}
            </select>
          ) : undefined;

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-2 rounded-xl border border-border bg-surface px-4 py-3">
        <ShieldCheck className="mt-0.5 size-4 shrink-0 text-accent" />
        <div className="min-w-0 flex-1 text-xs">
          <div className="text-sm font-medium">Wszystko, co warto zmienić — w jednym miejscu</div>
          <p className="mt-0.5 text-muted">
            Sugestie z kampanii, targetowania i placementów, policzone względem średnich
            Twojego konta. Zaznaczone trafiają do kolejki{" "}
            <Link href="/approvals" className="text-accent underline">
              Do zatwierdzenia
            </Link>{" "}
            — do Amazona idą dopiero po Twoim kliknięciu. Reguły w tle kolejkują same
            wyłącznie to, gdzie stawką jest ponad {money(threshold, currency)} wydatku;
            reszta czeka tutaj na Twoją decyzję.
          </p>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        <button
          onClick={() => setLevelFilter(levelFilter === "bad" ? "" : "bad")}
          className={clsx(
            "rounded-xl border px-4 py-3 text-left transition-colors",
            levelFilter === "bad" ? "border-bad bg-bad-soft" : "border-bad/25 bg-bad-soft/60",
          )}
        >
          <div className="text-xs font-medium text-bad uppercase">Wymaga reakcji</div>
          <div className="tnum mt-1 text-xl font-semibold">{counts.bad}</div>
          <div className="mt-0.5 text-xs text-muted">
            {money(counts.cost, currency)} wydatku bez zwrotu
          </div>
        </button>
        <button
          onClick={() => setLevelFilter(levelFilter === "warn" ? "" : "warn")}
          className={clsx(
            "rounded-xl border px-4 py-3 text-left transition-colors",
            levelFilter === "warn" ? "border-warn bg-warn-soft" : "border-warn/25 bg-warn-soft/60",
          )}
        >
          <div className="text-xs font-medium text-warn uppercase">Do poprawy</div>
          <div className="tnum mt-1 text-xl font-semibold">{counts.warn}</div>
          <div className="mt-0.5 text-xs text-muted">stawki oderwane od wyników</div>
        </button>
        <button
          onClick={() => setLevelFilter(levelFilter === "good" ? "" : "good")}
          className={clsx(
            "rounded-xl border px-4 py-3 text-left transition-colors",
            levelFilter === "good" ? "border-good bg-good-soft" : "border-good/25 bg-good-soft/60",
          )}
        >
          <div className="text-xs font-medium text-good uppercase">Okazje do wzrostu</div>
          <div className="tnum mt-1 text-xl font-semibold">{counts.good}</div>
          <div className="mt-0.5 text-xs text-muted">miejsca, gdzie warto dołożyć</div>
        </button>
      </div>

      {message && (
        <div
          className={clsx(
            "rounded-lg px-4 py-2.5 text-sm",
            message.tone === "good"
              ? "bg-good-soft text-good"
              : message.tone === "warn"
                ? "bg-warn-soft text-warn"
                : "bg-bad-soft text-bad",
          )}
        >
          {message.text}
          {!!message.skipped?.length && (
            <ul className="mt-1.5 space-y-1">
              {message.skipped.slice(0, 8).map((s, i) => (
                <li key={`${s.keyword}-${i}`} className="text-xs leading-snug">
                  <span className="font-medium">{s.keyword}</span>
                  {" — "}
                  {s.reason}
                </li>
              ))}
              {message.skipped.length > 8 && (
                <li className="text-xs opacity-80">
                  …i {message.skipped.length - 8} więcej
                </li>
              )}
            </ul>
          )}
        </div>
      )}

      <Card
        title={
          <div className="flex flex-wrap gap-1">
            <button
              onClick={() => setGroup("")}
              className={clsx(
                "rounded-lg px-2.5 py-1 text-sm font-medium transition-colors",
                group === ""
                  ? "bg-accent-soft text-accent"
                  : "text-muted hover:bg-surface-2 hover:text-text",
              )}
            >
              Wszystkie ({all.filter((r) => pasuje(r, "group")).length})
            </button>
            {/* Aktywna zakladka zostaje widoczna nawet przy zerze - inaczej
                po zawezeniu filtra znikalaby razem z jedynym wyjsciem z pustej
                listy. */}
            {GROUPS.filter((g) => groupCounts[g.key] > 0 || group === g.key).map((g) => (
              <button
                key={g.key}
                onClick={() => setGroup(group === g.key ? "" : g.key)}
                className={clsx(
                  "rounded-lg px-2.5 py-1 text-sm font-medium transition-colors",
                  group === g.key
                    ? "bg-accent-soft text-accent"
                    : "text-muted hover:bg-surface-2 hover:text-text",
                )}
              >
                {g.label} ({groupCounts[g.key]})
              </button>
            ))}
          </div>
        }
        subtitle={`${rows.length} pozycji · ${params.start} → ${params.end}${
          levelFilter ? " · filtr wagi aktywny, kliknij kafelek żeby wyłączyć" : ""
        }${portfolio ? " · tylko wybrane portfolio" : ""}`}
        action={
          <div className="flex items-center gap-2">
            <select
              value={sensitivity}
              onChange={(e) => {
                setSensitivity(Number(e.target.value));
                setSelected(new Set());
              }}
              className="rounded-lg border border-border bg-bg px-2 py-1 text-sm outline-none focus:border-accent"
              title="Ile danych musi się zebrać, zanim uznam sygnał za rozstrzygający"
            >
              <option value={1}>Próg pewny</option>
              <option value={0.5}>Próg obniżony</option>
              <option value={0.34}>Wszystkie sygnały</option>
            </select>
            {portfoliosSelect}
          </div>
        }
      >
        {loading && !data ? (
          <Skeleton rows={12} />
        ) : (
          <DataTable
            data={rows}
            columns={columns}
            getRowId={rowKey}
            selected={selected}
            onSelectedChange={setSelected}
            initialSort={[{ id: "cost", desc: true }]}
            emptyLabel="Brak sugestii w tym okresie"
            emptyHint="To dobra wiadomość — albo za mało danych, żeby cokolwiek stwierdzić. Poszerz zakres dat."
          />
        )}
      </Card>

      {(dismissed.data?.length ?? 0) > 0 && (
        <div className="rounded-xl border border-border bg-surface">
          <button
            onClick={() => setShowDismissed((v) => !v)}
            className="flex w-full items-center gap-2 px-4 py-2.5 text-left text-sm hover:bg-surface-2"
          >
            <EyeOff className="size-4 text-muted" />
            <span className="font-medium">Odrzucone</span>
            <span className="tnum text-muted">({dismissed.data!.length})</span>
            <span className="ml-auto text-xs text-muted">
              {showDismissed ? "zwiń" : "pokaż"}
            </span>
          </button>
          {showDismissed && (
            <div className="border-t border-border">
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-xs text-muted">
                    <tr className="border-b border-border">
                      <th className="px-4 py-2 text-left font-medium">Sugestia</th>
                      <th className="px-2 py-2 text-left font-medium">Obiekt</th>
                      <th className="px-2 py-2 text-left font-medium">Do kiedy</th>
                      <th className="px-4 py-2" />
                    </tr>
                  </thead>
                  <tbody>
                    {dismissed.data!.map((d) => (
                      <tr key={d.id} className="border-b border-border/60 last:border-0">
                        <td className="max-w-[28rem] truncate px-4 py-1.5">{d.title ?? d.code}</td>
                        <td className="max-w-[20rem] truncate px-2 py-1.5 text-muted">
                          {d.entity_name ?? d.entity_id}
                        </td>
                        <td className="whitespace-nowrap px-2 py-1.5 text-muted">
                          {d.until ? new Date(d.until).toLocaleDateString("pl-PL") : "na stałe"}
                        </td>
                        <td className="px-4 py-1.5 text-right">
                          <Button size="sm" variant="ghost" onClick={() => restoreDismissed(d)} title="Sugestia wróci na listę">
                            <Undo2 className="size-3.5" />
                            Przywróć
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}

      {selected.size > 0 && (
        <div className="sticky bottom-4 z-30">
          <div className="mx-auto flex max-w-3xl flex-wrap items-center gap-3 rounded-xl border border-border bg-surface p-3 shadow-lg">
            <span className="text-sm font-medium">
              Zaznaczono <span className="tnum">{selected.size}</span>
            </span>
            {blocked ? (
              <span className="text-xs text-warn">
                Wybierz konkretny rynek, żeby wysłać do zatwierdzenia
              </span>
            ) : (
              <span className="text-xs text-muted">
                {edits.size > 0
                  ? `${edits.size} wartości poprawionych ręcznie · trafią do kolejki jako plany`
                  : "Trafią do kolejki jako plany — Amazon zobaczy je dopiero po zatwierdzeniu"}
              </span>
            )}
            <div className="ml-auto flex items-center gap-2">
              <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}>
                Wyczyść
              </Button>
              {/* Odrzucenie to decyzja, nie zmiana w Amazonie - nie przechodzi
                  przez kolejke. 30 dni to tyle, ile trwa, zanim dane pod
                  sugestia zdaza sie zmienic; "na stale" dla rzeczy, o ktorych
                  wiesz, ze zostaja jak sa. */}
              <div className="relative">
                <Button
                  size="sm"
                  onClick={() => setDismissMenu((v) => !v)}
                  disabled={busy || blocked}
                  title="Schowaj zaznaczone sugestie — nic nie idzie do Amazona"
                >
                  <EyeOff className="size-3.5" />
                  Odrzuć ▾
                </Button>
                {dismissMenu && (
                  <div className="absolute bottom-full right-0 z-40 mb-1 w-44 rounded-lg border border-border bg-surface p-1 shadow-lg">
                    {[
                      [7, "na 7 dni"],
                      [14, "na 14 dni"],
                      [30, "na 30 dni"],
                      [0, "na stałe"],
                    ].map(([d, label]) => (
                      <button
                        key={d}
                        onClick={() => {
                          setDismissMenu(false);
                          dismissSelected((d as number) || null);
                        }}
                        className="block w-full rounded px-2 py-1.5 text-left text-sm hover:bg-surface-2"
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              <span className="mx-1 h-5 w-px bg-border" />
              <Button variant="primary" onClick={queueSelected} disabled={busy || blocked}>
                {busy && <Loader2 className="size-3.5 animate-spin" />}
                Wyślij do zatwierdzenia
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
