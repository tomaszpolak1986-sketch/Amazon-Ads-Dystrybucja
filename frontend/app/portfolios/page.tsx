"use client";

/* eslint-disable @typescript-eslint/no-explicit-any */

import { useRouter } from "next/navigation";
import { useMemo } from "react";
import { DataTable } from "@/components/DataTable";
import { metricColumns } from "@/components/metricColumns";
import { Badge, Card, ErrorState, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import { useFilters, useRangeParams } from "@/lib/filters";
import { acosTone, money, num, pct } from "@/lib/format";
import { Sparkline, seriesOf } from "@/components/Sparkline";
import { useData } from "@/lib/useData";

export default function PortfoliosPage() {
  const router = useRouter();
  const params = useRangeParams();
  const { currency, revision } = useFilters();

  const { data, loading, error } = useData(["portfolios", params, revision], () =>
    api.portfolios(params),
  );
  // Przebiegi ida osobnym zapytaniem: tabela ma sie pokazac od razu, a wykresy
  // doplynac chwile pozniej, zamiast wstrzymywac caly ekran.
  const trends = useData(["portfolio-trends", params, revision], () =>
    api.portfolioTrends(params),
  );

  const rows = data ?? [];
  const totalCost = rows.reduce((a, r) => a + r.cost, 0);

  const columns = useMemo(
    () => [
      {
        id: "name",
        header: "Portfolio",
        accessorFn: (r: any) => r.name ?? "",
        meta: { width: "26%" },
        cell: (c: any) => {
          const r = c.row.original;
          const share = totalCost ? (r.cost / totalCost) * 100 : 0;
          return (
            <div className="min-w-0">
              <div className="truncate font-medium" title={r.name ?? ""}>
                {r.name}
              </div>
              <div className="mt-0.5 flex items-center gap-1.5">
                {/* Pasek udzialu w wydatku - od razu widac, gdzie ida pieniadze */}
                <div className="h-1 w-16 overflow-hidden rounded-full bg-surface-2">
                  <div
                    className="h-full rounded-full bg-accent"
                    style={{ width: `${Math.min(100, share)}%` }}
                  />
                </div>
                <span className="tnum text-[11px] text-muted">{pct(share, 0)} budżetu</span>
              </div>
            </div>
          );
        },
      },
      {
        accessorKey: "campaigns",
        header: "Kampanii",
        meta: { align: "right" },
        cell: (c: any) => <span className="text-muted">{num(c.getValue())}</span>,
      },
      {
        accessorKey: "budget",
        header: "Budżet",
        meta: { align: "right" },
        cell: (c: any) => {
          const r = c.row.original;
          if (r.budget_policy === "NO_CAP" || !c.getValue()) {
            return <span className="text-muted">bez limitu</span>;
          }
          return money(c.getValue(), r.currency ?? currency);
        },
      },
      ...metricColumns({ currency }),
      {
        id: "trend",
        header: "Przebieg wydatku",
        enableSorting: false,
        meta: { width: "8rem" },
        cell: (c: any) => {
          const seria = seriesOf(trends.data?.[String(c.row.original.portfolio_id)], "cost");
          const suma = seria.reduce((a, v) => a + v, 0);
          if (!suma) return <span className="text-[10px] text-muted/60">brak wydatku</span>;
          return (
            <Sparkline
              points={seria}
              label={`Przebieg wydatku portfolio ${c.row.original.name ?? ""}`}
            />
          );
        },
      },
    ],
    [currency, totalCost, trends.data],
  );

  if (error) return <ErrorState message={error} />;

  // Portfolia, ktore wydaja i nie sprzedaja - warto zobaczyc od razu
  const wasteful = rows.filter((r) => r.cost > 5 && !r.sales);
  const best = rows
    .filter((r) => r.acos !== null && r.orders >= 5)
    .sort((a, b) => (a.acos ?? 999) - (b.acos ?? 999))[0];

  return (
    <div className="space-y-4">
      {(best || wasteful.length > 0) && (
        <div className="grid gap-3 sm:grid-cols-2">
          {best && (
            <div className="rounded-xl border border-good/25 bg-good-soft px-4 py-3">
              <div className="text-xs font-medium text-good uppercase">
                Najtańsza sprzedaż
              </div>
              <div className="mt-1 truncate text-lg font-semibold">{best.name}</div>
              <div className="mt-0.5 text-xs text-muted">
                ACOS <span className={acosTone(best.acos)}>{pct(best.acos)}</span> przy{" "}
                {num(best.orders)} zamówieniach · wydatek {money(best.cost, currency)}
              </div>
            </div>
          )}
          {wasteful.length > 0 && (
            <div className="rounded-xl border border-bad/25 bg-bad-soft px-4 py-3">
              <div className="text-xs font-medium text-bad uppercase">Wydatek bez sprzedaży</div>
              <div className="tnum mt-1 text-lg font-semibold">
                {money(
                  wasteful.reduce((a, r) => a + r.cost, 0),
                  currency,
                )}
              </div>
              <div className="mt-0.5 truncate text-xs text-muted">
                {wasteful.length}{" "}
                {wasteful.length === 1 ? "portfolio" : "portfolii"}:{" "}
                {wasteful
                  .slice(0, 3)
                  .map((r) => r.name)
                  .join(", ")}
              </div>
            </div>
          )}
        </div>
      )}

      <Card
        title="Portfolia"
        subtitle={`${rows.length} portfolii · ${params.start} → ${params.end}`}
      >
        {loading && !data ? (
          <Skeleton rows={10} />
        ) : (
          <DataTable
            data={rows}
            columns={columns}
            getRowId={(r) => String(r.portfolio_id)}
            onRowClick={(r) => router.push(`/campaigns?portfolio=${r.portfolio_id}`)}
            initialSort={[{ id: "cost", desc: true }]}
            emptyLabel="Brak danych o portfoliach"
            emptyHint="Portfolia pobierają się razem z resztą struktury konta przy synchronizacji."
          />
        )}
      </Card>

      <p className="px-1 text-xs text-muted">
        Kliknij wiersz, żeby zobaczyć kampanie danego portfolio. Kampanie bez przypisania
        pokazane są jako <Badge tone="neutral">Bez portfolio</Badge> — dzięki temu sumy
        zgadzają się z przeglądem i widać, czego brakuje.
      </p>
    </div>
  );
}
