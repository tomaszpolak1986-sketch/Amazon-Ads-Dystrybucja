"use client";

import clsx from "clsx";
import { Lightbulb } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { BudgetCapped } from "@/components/BudgetCapped";
import { BulkBar, useBulkAction } from "@/components/BulkBar";
import { DataTable } from "@/components/DataTable";
import { NgramBreakdown } from "@/components/NgramBreakdown";
import { OverlapList } from "@/components/OverlapList";
import { metricColumns } from "@/components/metricColumns";
import { Card, ErrorState, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import type { NgramRow } from "@/lib/api";
import { useFilters, useRangeParams } from "@/lib/filters";
import { money, num, pct } from "@/lib/format";
import { useData } from "@/lib/useData";

/**
 * Analiza pokazuje wylacznie to, czego nie widac wiersz po wierszu.
 *
 * Pojedyncze frazy do wykluczenia albo przejecia byly tu kiedys osobnymi
 * zakladkami - dublowaly Sugestie, roznily sie tylko progiem. Prog trafil
 * do Sugestii jako regulacja czulosci, a tutaj zostalo to, czego nie da sie
 * zobaczyc, patrzac na jedna pozycje: rodziny slow i konkurencja miedzy
 * wlasnymi kampaniami.
 */
type Tab = "overlap" | "sku" | "ngrams" | "budget";

const TABS: Array<{ key: Tab; label: string; hint: string }> = [
  {
    key: "overlap",
    label: "Fraza w kilku kampaniach",
    hint:
      "Frazy, o które stara się kilka Twoich kampanii naraz. Amazon i tak wpuszcza " +
      "do aukcji jedną reklamę z konta, więc nie licytujesz przeciw sobie — ale " +
      "historia i budżet frazy rozjeżdżają się na kilka kampanii, przez co każda " +
      "uczy się wolniej. Patrz na rozrzut ACOS: jeśli jest duży, jedna robi to lepiej.",
  },
  {
    key: "sku",
    label: "Produkt w kilku kampaniach",
    hint:
      "Ten sam produkt reklamowany z kilku kampanii. Samo w sobie nie jest błędem — " +
      "auto i ręczna obok siebie to normalna praktyka. Problem zaczyna się, gdy ACOS " +
      "między nimi różni się o kilkadziesiąt punktów: ta sama cena, ten sam produkt, " +
      "a budżet płynie gorszym kanałem.",
  },
  {
    key: "budget",
    label: "Budżet na wyczerpaniu",
    hint:
      "Kampanie, które regularnie domykają dzienny budżet i wyłączają się przed " +
      "końcem dnia. W tabeli kampanii wyglądają wzorowo — niski ACOS, wysoki ROAS — " +
      "bo liczy się tylko to, co zdążyły wydać. Dopiero rozkład po dniach pokazuje, " +
      "ile godzin nie były w aukcji.",
  },
  {
    key: "ngrams",
    label: "N-gramy",
    hint:
      "Wyniki zsumowane po fragmentach fraz. Pokazuje całe rodziny słów przepalających " +
      "budżet — pojedyncze frazy zobaczysz w Sugestiach, wzorce tylko tutaj.",
  },
];

export default function OpportunitiesPage() {
  const params = useRangeParams();
  const { currency, revision, profileId, bump } = useFilters();
  const bulk = useBulkAction(bump);
  const [tab, setTab] = useState<Tab>("overlap");
  const [minClicks, setMinClicks] = useState(10);
  // Rozwiniety n-gram: pokazuje, ktore grupy go lapia i pozwala wykluczyc
  const [ngramOtwarty, setNgramOtwarty] = useState<string | null>(null);

  const overlap = useData(["overlap", params, revision, minClicks], () =>
    api.overlap({ ...params, min_clicks: minClicks, limit: 300 }),
  );
  const skuOverlap = useData(["sku-overlap", params, revision, minClicks], () =>
    api.skuOverlap({ ...params, min_clicks: minClicks, limit: 300 }),
  );
  const ngrams = useData(["ngrams", params, revision], () =>
    api.ngrams({ ...params, limit: 300 }),
  );
  const budget = useData(["budget-capped", params, revision], () =>
    api.budgetCapped(params),
  );

  // Tabela jak wczesniej - sortowalna, z pelnym zestawem metryk. Doszly dwie
  // kolumny: ile z tego wydatku jest zmarnowane i w ktorych kampaniach
  // fragment wystepuje, zeby nie trzeba bylo klikac w kazdy z osobna.
  const ngramColumns = useMemo(
    () => [
      {
        accessorKey: "ngram",
        header: "Fragment frazy",
        meta: { width: "22%" },
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        cell: (c: any) => <span className="font-medium">{c.getValue()}</span>,
      },
      {
        id: "campaigns",
        header: "Kampanie",
        accessorFn: (r: NgramRow) => r.campaign_count ?? 0,
        meta: { width: "20%" },
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        cell: (c: any) => {
          const r = c.row.original as NgramRow;
          const lista = r.campaigns ?? [];
          if (!lista.length) return <span className="text-muted">—</span>;
          const pelna = lista
            .map(
              (k) =>
                `${k.campaign_name} — ${money(k.cost, currency)}, ${k.orders} zam.`,
            )
            .join(String.fromCharCode(10));
          const puste = r.campaigns_no_sales ?? 0;
          return (
            <div className="min-w-0" title={pelna}>
              <div className="truncate text-xs">{lista[0].campaign_name}</div>
              <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
                {lista.length > 1 && (
                  <span className="text-muted">
                    i {lista.length - 1} {lista.length === 2 ? "inna" : "innych"}
                  </span>
                )}
                {/* To jest odpowiedz na pytanie "gdzie to slowo nie rotuje" -
                    tam wykluczenie nie kosztuje zadnej sprzedazy. */}
                {puste > 0 && (
                  <span className="rounded bg-bad-soft px-1 font-medium text-bad">
                    {puste} bez sprzedaży
                  </span>
                )}
              </div>
            </div>
          );
        },
      },
      {
        accessorKey: "waste",
        header: "Marnuje",
        meta: { align: "right" },
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        cell: (c: any) => (
          <span className="tnum font-medium text-bad">{money(c.getValue(), currency)}</span>
        ),
      },
      {
        accessorKey: "terms",
        header: "Fraz",
        meta: { align: "right" },
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        cell: (c: any) => <span className="text-muted">{num(c.getValue())}</span>,
      },
      ...metricColumns({ currency, omit: ["impressions", "ctr"] }),
    ],
    [currency],
  );

  // Tabela sortuje plaska liste, wiec warianty wchodza jako osobne wiersze.
  const ngramRows = useMemo(
    () =>
      (ngrams.data?.items ?? []).flatMap((r) => [r, ...(r.children ?? [])]),
    [ngrams.data],
  );

  const active = TABS.find((t) => t.key === tab)!;
  const state =
    tab === "overlap"
      ? overlap
      : tab === "sku"
        ? skuOverlap
        : tab === "budget"
          ? budget
          : ngrams;

  if (state.error) return <ErrorState message={state.error} />;

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-2 rounded-xl border border-border bg-surface px-4 py-3">
        <Lightbulb className="mt-0.5 size-4 shrink-0 text-accent" />
        <div className="min-w-0 text-xs">
          <div className="text-sm font-medium">Wzorce, nie pojedyncze pozycje</div>
          <p className="mt-0.5 text-muted">
            Ten ekran pokazuje to, czego nie widać wiersz po wierszu: rodziny słów
            przepalających budżet i pozycje, o które konkurują Twoje własne kampanie.
            Pojedyncze frazy do wykluczenia albo przejęcia znajdziesz w{" "}
            <Link href="/suggestions" className="text-accent underline">
              Sugestiach
            </Link>
            , razem z regulacją progu.
          </p>
        </div>
      </div>

      <Card
        title={
          <div className="flex flex-wrap gap-1">
            {TABS.map((t) => (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                className={clsx(
                  "rounded-lg px-2.5 py-1 text-sm font-medium transition-colors",
                  tab === t.key
                    ? "bg-accent-soft text-accent"
                    : "text-muted hover:bg-surface-2 hover:text-text",
                )}
              >
                {t.label}
              </button>
            ))}
          </div>
        }
        subtitle={active.hint}
        action={
          <div className="flex items-center gap-2 text-sm">
            {tab === "budget" || tab === "ngrams" ? null : (
              <select
                value={minClicks}
                onChange={(e) => setMinClicks(Number(e.target.value))}
                className="rounded-lg border border-border bg-bg px-2 py-1 outline-none focus:border-accent"
              >
                {[5, 10, 25, 50, 100].map((v) => (
                  <option key={v} value={v}>
                    Min. {v} kliknięć
                  </option>
                ))}
              </select>
            )}
          </div>
        }
      >
        {state.loading && !state.data ? (
          <Skeleton rows={10} />
        ) : tab === "budget" ? (
          budget.data ? (
            <BudgetCapped
              data={budget.data}
              currency={currency}
              disabled={
                profileId === null
                  ? "Wybierz konkretny rynek, żeby zmieniać budżety"
                  : null
              }
              onPlan={(changes) =>
                bulk.preview(() => api.planBudgets(profileId!, changes))
              }
            />
          ) : null
        ) : tab === "ngrams" ? (
          <>
            {ngrams.data && (
              <p className="mb-2 text-xs text-muted">
                Ranking po zmarnowanym wydatku — ile kosztował ruch ponad to, co
                usprawiedliwia jego sprzedaż przy średniej konta (
                {pct(ngrams.data.account.acos)}). Na liście fragmenty co najmniej
                półtora raza gorsze, od {pct(ngrams.data.threshold_acos)}, mające co
                najmniej {ngrams.data.account.min_clicks} kliknięć. Kliknij wiersz, żeby
                wybrać kampanię i wykluczyć.
              </p>
            )}
            <DataTable
              data={ngramRows}
              columns={ngramColumns}
              getRowId={(r) => r.ngram}
              initialSort={[{ id: "waste", desc: true }]}
              onRowClick={(r) =>
                setNgramOtwarty((b) => (b === r.ngram ? null : r.ngram))
              }
              emptyLabel="Żaden fragment nie odstaje na tyle, żeby go ruszać"
              emptyHint="Kliknij fragment, żeby zobaczyć, które grupy go łapią."
            />
            {ngramOtwarty && (
              <NgramBreakdown
                ngram={ngramOtwarty}
                params={params}
                currency={currency}
                canPlan={profileId !== null}
                onClose={() => setNgramOtwarty(null)}
                onNegatives={(items, level) =>
                  bulk.preview(() => api.planNegatives(profileId!, items, level))
                }
              />
            )}
          </>
        ) : tab === "overlap" ? (
          <OverlapList
            currency={currency}
            emptyLabel="Żadna fraza nie rozkłada się realnie na kilka kampanii"
            items={(overlap.data ?? []).map((r) => ({
              key: r.search_term,
              title: r.search_term,
              campaign_count: r.campaign_count,
              campaign_count_all: r.campaign_count_all,
              clicks: r.clicks,
              cost: r.cost,
              orders: r.orders,
              sales: r.sales,
              acos: r.acos,
              spreadLabel: "Największa ma",
              spreadValue: pct(r.top_share, 0),
              spreadTone:
                r.top_share !== null && r.top_share < 60 ? "text-warn" : "text-muted",
              breakdown: r.breakdown,
            }))}
          />
        ) : (
          <OverlapList
            currency={currency}
            emptyLabel="Żaden produkt nie jest realnie dzielony między kampanie"
            items={(skuOverlap.data ?? []).map((r) => ({
              key: r.sku,
              title: r.sku,
              subtitle: r.asin,
              image: r.catalog?.image ?? null,
              productName: r.catalog?.title ?? null,
              campaign_count: r.campaign_count,
              campaign_count_all: r.campaign_count_all,
              clicks: r.clicks,
              cost: r.cost,
              orders: r.orders,
              sales: r.sales,
              acos: r.acos,
              spreadLabel: "Rozrzut ACOS",
              spreadValue:
                r.acos_spread === null ? "—" : `${r.acos_spread.toFixed(0)} pkt`,
              spreadTone:
                r.acos_spread !== null && r.acos_spread >= 15 ? "text-warn" : "text-muted",
              breakdown: r.breakdown,
            }))}
          />
        )}
      </Card>

      {/* Zmiana budzetu i tak przechodzi przez plan - do Amazona nie idzie nic,
          dopoki nie zostanie zatwierdzona tutaj. */}
      <BulkBar
        count={0}
        onClear={() => {}}
        pending={bulk.pending}
        busy={bulk.busy}
        result={bulk.result}
        onConfirm={bulk.confirm}
        onCancel={bulk.cancel}
      >
        {null}
      </BulkBar>
    </div>
  );
}
