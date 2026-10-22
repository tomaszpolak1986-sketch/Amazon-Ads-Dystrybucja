"use client";

import clsx from "clsx";
import { AlertTriangle, ChevronDown, ChevronRight, X } from "lucide-react";
import { useMemo, useState } from "react";
import { Badge, Button, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import type { NgramGroup, RangeParams } from "@/lib/api";
import { count, money, pct } from "@/lib/format";
import { useData } from "@/lib/useData";

/**
 * Gdzie wystepuje n-gram i jak go stad wykluczyc.
 *
 * Panel jest ulozony kampaniami, bo tak zapada decyzja: "ten fragment
 * przepala w tej kampanii". Grupy reklam sa pod spodem, dla przypadkow,
 * w ktorych fragment psuje tylko jedna z nich.
 *
 * Wykluczenie frazowe Amazona blokuje **wszystkie** zapytania zawierajace ten
 * ciag slow, wiec przy n-gramie, ktory cos sprzedaje, zablokuje tez sprzedaz.
 * Dlatego liczby sprzedazowe sa tu pokazane na rowni z kosztem.
 */

type Poziom = "CAMPAIGN" | "AD_GROUP";

interface Kampania {
  campaign_id: string;
  campaign_name: string;
  cost: number;
  sales: number;
  clicks: number;
  orders: number;
  terms: number;
  acos: number | null;
  grupy: NgramGroup[];
}

function scalPoKampaniach(items: NgramGroup[]): Kampania[] {
  const mapa = new Map<string, Kampania>();
  for (const g of items) {
    const k = mapa.get(g.campaign_id) ?? {
      campaign_id: g.campaign_id,
      campaign_name: g.campaign_name ?? g.campaign_id,
      cost: 0, sales: 0, clicks: 0, orders: 0, terms: 0,
      acos: null, grupy: [],
    };
    k.cost += g.cost;
    k.sales += g.sales;
    k.clicks += g.clicks;
    k.orders += g.orders;
    k.terms += g.terms;
    k.grupy.push(g);
    mapa.set(g.campaign_id, k);
  }
  const out = [...mapa.values()];
  for (const k of out) k.acos = k.sales ? (k.cost / k.sales) * 100 : null;
  out.sort((a, b) => b.cost - a.cost);
  return out;
}

export function NgramBreakdown({
  ngram,
  params,
  currency,
  canPlan,
  onClose,
  onNegatives,
}: {
  ngram: string;
  params: RangeParams;
  currency: string;
  canPlan: boolean;
  onClose: () => void;
  onNegatives: (
    items: Array<{
      campaignId: string;
      adGroupId?: string;
      keywordText: string;
      matchType: string;
    }>,
    level: Poziom,
  ) => void;
}) {
  const [poziom, setPoziom] = useState<Poziom>("CAMPAIGN");
  const [dopasowanie, setDopasowanie] = useState("NEGATIVE_PHRASE");
  const [wybrane, setWybrane] = useState<Set<string>>(new Set());
  const [rozwiniete, setRozwiniete] = useState<Set<string>>(new Set());

  const { data, loading, error } = useData(
    ["ngram-breakdown", ngram, params],
    () => api.ngramBreakdown({ ...params, ngram }),
  );

  const kampanie = useMemo(
    () => scalPoKampaniach(data?.items ?? []),
    [data],
  );

  if (loading && !data) {
    return (
      <div className="mt-3 rounded-xl border border-border p-3">
        <Skeleton rows={4} />
      </div>
    );
  }
  if (error) return <p className="mt-3 text-xs text-bad">{error}</p>;
  if (!data?.items.length || !data.totals) {
    return (
      <p className="mt-3 rounded-xl border border-border p-3 text-xs text-muted">
        Nie znalazłem fraz z tym fragmentem w wybranym okresie.
      </p>
    );
  }

  const t = data.totals;
  const sprzedaje = (t.orders ?? 0) > 0;

  function przelacz(klucz: string) {
    const next = new Set(wybrane);
    if (next.has(klucz)) next.delete(klucz);
    else next.add(klucz);
    setWybrane(next);
  }

  function zmienPoziom(nowy: Poziom) {
    // Zaznaczenie z jednego poziomu nie ma sensu na drugim - jeden plan
    // obejmuje jeden poziom, wiec czyscimy je przy przelaczeniu.
    setPoziom(nowy);
    setWybrane(new Set());
  }

  function wyslij() {
    const items =
      poziom === "CAMPAIGN"
        ? kampanie
            .filter((k) => wybrane.has(k.campaign_id))
            .map((k) => ({
              campaignId: k.campaign_id,
              keywordText: ngram,
              matchType: dopasowanie,
            }))
        : data!.items
            .filter((g) => wybrane.has(`${g.campaign_id}|${g.ad_group_id}`))
            .map((g) => ({
              campaignId: g.campaign_id,
              adGroupId: g.ad_group_id ?? undefined,
              keywordText: ngram,
              matchType: dopasowanie,
            }));
    if (items.length) onNegatives(items, poziom);
  }

  return (
    <div className="mt-3 rounded-xl border border-accent/40 bg-surface">
      <div className="flex items-start justify-between gap-2 border-b border-border px-3 py-2.5">
        <div className="min-w-0">
          <div className="text-sm font-medium">
            „{ngram}&rdquo; w {count(kampanie.length, "kampanii", "kampaniach", "kampaniach")}
          </div>
          <div className="tnum mt-0.5 text-xs text-muted">
            {count(t.terms, "fraza", "frazy", "fraz")} · {money(t.cost, currency)} kosztu ·{" "}
            {t.orders} zam. · {money(t.sales, currency)} sprzedaży
            {t.acos !== null && ` · ACOS ${pct(t.acos)}`}
          </div>
        </div>
        <button
          onClick={onClose}
          aria-label="Zamknij rozbicie"
          className="rounded-lg p-1 text-muted hover:bg-surface-2 hover:text-text"
        >
          <X className="size-4" />
        </button>
      </div>

      {sprzedaje && (
        <div className="flex items-start gap-2 border-b border-border bg-warn-soft px-3 py-2 text-xs text-warn">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          <p>
            Ten fragment ma {count(t.orders ?? 0, "zamówienie", "zamówienia", "zamówień")} i{" "}
            {money(t.sales, currency)} sprzedaży. Wykluczenie zablokuje{" "}
            <strong>wszystkie</strong> zapytania z tym ciągiem słów — razem z tymi, które
            sprzedają. Wyklucz tylko tam, gdzie nic nie przyniósł.
          </p>
        </div>
      )}

      {/* Poziom decyduje, gdzie wyladuje wykluczenie. Jeden plan obejmuje
          jeden poziom, wiec to wybor, a nie dwie niezalezne listy. */}
      <div className="flex flex-wrap items-center gap-3 border-b border-border px-3 py-2 text-xs">
        <span className="text-muted">Wyklucz w:</span>
        <div className="flex gap-0.5 rounded-lg border border-border p-0.5">
          {(
            [
              ["CAMPAIGN", "całej kampanii"],
              ["AD_GROUP", "wybranych grupach"],
            ] as Array<[Poziom, string]>
          ).map(([w, etykieta]) => (
            <button
              key={w}
              onClick={() => zmienPoziom(w)}
              className={clsx(
                "rounded-md px-2 py-0.5 font-medium transition-colors",
                poziom === w ? "bg-accent text-white" : "text-muted hover:text-text",
              )}
            >
              {etykieta}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-1.5">
          <span className="text-muted">Dopasowanie</span>
          <select
            value={dopasowanie}
            onChange={(e) => setDopasowanie(e.target.value)}
            className="rounded-lg border border-border bg-bg px-2 py-0.5 outline-none focus:border-accent"
          >
            <option value="NEGATIVE_PHRASE">frazowe</option>
            <option value="NEGATIVE_EXACT">dokładne</option>
          </select>
        </label>
      </div>

      <ul className="divide-y divide-border">
        {kampanie.map((k) => {
          const otwarta = rozwiniete.has(k.campaign_id);
          const bezSprzedazy = !k.orders;
          return (
            <li key={k.campaign_id}>
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1 px-3 py-2">
                <input
                  type="checkbox"
                  checked={wybrane.has(k.campaign_id)}
                  disabled={!canPlan || poziom !== "CAMPAIGN"}
                  onChange={() => przelacz(k.campaign_id)}
                  aria-label={`Zaznacz kampanię ${k.campaign_name}`}
                  className="size-3.5 shrink-0 accent-[var(--accent)] disabled:opacity-30"
                />
                <button
                  onClick={() =>
                    setRozwiniete((b) => {
                      const n = new Set(b);
                      if (n.has(k.campaign_id)) n.delete(k.campaign_id);
                      else n.add(k.campaign_id);
                      return n;
                    })
                  }
                  className="flex min-w-0 flex-1 items-center gap-1 text-left"
                >
                  {otwarta ? (
                    <ChevronDown className="size-3.5 shrink-0 text-muted" />
                  ) : (
                    <ChevronRight className="size-3.5 shrink-0 text-muted" />
                  )}
                  <span className="truncate text-xs font-medium" title={k.campaign_name}>
                    {k.campaign_name}
                  </span>
                  <span className="shrink-0 text-[11px] text-muted">
                    {count(k.grupy.length, "grupa", "grupy", "grup")}
                  </span>
                </button>
                <Badge tone={bezSprzedazy ? "bad" : "neutral"}>
                  {bezSprzedazy ? "0 zamówień" : `${k.orders} zam.`}
                </Badge>
                <span className="tnum flex shrink-0 items-center gap-3 text-xs text-muted">
                  <span>{count(k.terms, "fraza", "frazy", "fraz")}</span>
                  <span>{money(k.cost, currency)}</span>
                  <span className={clsx("w-20 text-right", bezSprzedazy && "text-bad")}>
                    {k.acos === null ? "brak sprzedaży" : pct(k.acos)}
                  </span>
                </span>
              </div>

              {otwarta && (
                <ul className="border-t border-border/60 bg-surface-2">
                  {k.grupy.map((g) => {
                    const klucz = `${g.campaign_id}|${g.ad_group_id}`;
                    const pusta = !g.orders;
                    return (
                      <li
                        key={klucz}
                        className="flex flex-wrap items-center gap-x-2 gap-y-0.5 py-1.5 pl-9 pr-3"
                      >
                        <input
                          type="checkbox"
                          checked={wybrane.has(klucz)}
                          disabled={!canPlan || poziom !== "AD_GROUP"}
                          onChange={() => przelacz(klucz)}
                          aria-label={`Zaznacz grupę ${g.ad_group_name ?? g.ad_group_id}`}
                          className="size-3.5 shrink-0 accent-[var(--accent)] disabled:opacity-30"
                        />
                        <span
                          className="min-w-0 flex-1 truncate text-xs"
                          title={g.ad_group_name ?? ""}
                        >
                          {g.ad_group_name ?? g.ad_group_id}
                        </span>
                        <span className="tnum flex shrink-0 items-center gap-3 text-[11px] text-muted">
                          <span>{g.orders} zam.</span>
                          <span>{money(g.cost, currency)}</span>
                          <span className={clsx("w-16 text-right", pusta && "text-bad")}>
                            {g.acos === null ? "—" : pct(g.acos)}
                          </span>
                        </span>
                        <div className="w-full truncate pl-5 text-[11px] text-muted/80">
                          np. {g.examples.map((e) => e.search_term).join(" · ")}
                        </div>
                      </li>
                    );
                  })}
                </ul>
              )}
            </li>
          );
        })}
      </ul>

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border px-3 py-2.5">
        <p className="text-[11px] text-muted">
          {!canPlan
            ? "Wybierz konkretny rynek, żeby wykluczać"
            : wybrane.size
              ? `Wykluczenie ${dopasowanie === "NEGATIVE_PHRASE" ? "frazowe" : "dokładne"} „${ngram}” w ${
                  poziom === "CAMPAIGN"
                    ? count(wybrane.size, "kampanii", "kampaniach", "kampaniach")
                    : count(wybrane.size, "grupie", "grupach", "grupach")
                } — trafi do zatwierdzenia.`
              : poziom === "CAMPAIGN"
                ? "Zaznacz kampanie, w których chcesz wykluczyć ten fragment."
                : "Rozwiń kampanię i zaznacz grupy reklam."}
        </p>
        <Button
          size="sm"
          variant="danger"
          disabled={!canPlan || !wybrane.size}
          onClick={wyslij}
        >
          Wyklucz
        </Button>
      </div>
    </div>
  );
}
