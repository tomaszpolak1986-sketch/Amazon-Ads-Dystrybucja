"use client";

/* eslint-disable @typescript-eslint/no-explicit-any */

import { useMemo } from "react";
import { DataTable } from "@/components/DataTable";
import { metricColumns } from "@/components/metricColumns";
import { Badge, Card, ErrorState, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import { useFilters, useRangeParams } from "@/lib/filters";
import { acosTone, money, pct } from "@/lib/format";
import { useData } from "@/lib/useData";

const LABEL: Record<string, string> = {
  PLACEMENT_TOP: "Góra wyników",
  PLACEMENT_REST_OF_SEARCH: "Pozostałe wyniki",
  PLACEMENT_PRODUCT_PAGE: "Karta produktu",
  // Amazon pozwala ustawic ten modyfikator, ale nie raportuje go osobno -
  // dlatego wiersz ma mnoznik, a metryki puste.
  SITE_AMAZON_BUSINESS: "Amazon Business (B2B)",
};

export default function PlacementsPage() {
  const params = useRangeParams();
  const { currency, revision } = useFilters();

  const summary = useData(["placements-summary", params, revision], () =>
    api.placements(params),
  );
  const detail = useData(["placements-detail", params, revision], () =>
    api.placementsByCampaign({ ...params, limit: 5000 }),
  );

  const rows = detail.data ?? [];
  const totals = summary.data ?? [];
  const grandCost = totals.reduce((a, r) => a + r.cost, 0);

  const columns = useMemo(
    () => [
      {
        id: "campaign",
        header: "Kampania",
        accessorFn: (r: any) => r.campaign_name ?? "",
        meta: { width: "24%" },
        cell: (c: any) => (
          <div className="min-w-0">
            <div className="truncate font-medium" title={c.getValue()}>
              {c.getValue() || "—"}
            </div>
            <div className="text-[11px] text-muted">{c.row.original.country}</div>
          </div>
        ),
      },
      {
        accessorKey: "placement",
        header: "Placement",
        cell: (c: any) => {
          const r = c.row.original as { no_report?: boolean };
          return (
            <div className="flex items-center gap-1.5">
              <Badge tone="neutral">{LABEL[c.getValue()] ?? c.getValue()}</Badge>
              {/* Bez tego wiersz Amazon Business wyglada na placement o zerowych
                  wynikach, a to co innego niz brak danych. Amazon nie raportuje
                  go osobno - metryki sa puste, bo ich nie ma, nie dlatego, ze
                  nic sie nie sprzedalo. Modyfikator dziala mimo to. */}
              {r.no_report && (
                <span
                  className="cursor-help text-[10px] text-muted"
                  title="Amazon nie raportuje tego placementu osobno — jego wyniki są wliczone w pozostałe wiersze. Modyfikator stawki działa normalnie i można go tu zmienić."
                >
                  bez raportu
                </span>
              )}
            </div>
          );
        },
      },
      {
        accessorKey: "modifier",
        header: "Modyfikator",
        meta: { align: "right" },
        cell: (c: any) => {
          const v = c.getValue() as number;
          if (!v) return <span className="text-muted">—</span>;
          return <span className="font-medium text-accent">+{v}%</span>;
        },
      },
      ...metricColumns({ currency }),
    ],
    [currency],
  );

  if (detail.error) return <ErrorState message={detail.error} />;

  // Najtanszy placement w calym koncie - kandydat do podniesienia modyfikatora
  const best = [...totals]
    .filter((p) => p.acos !== null && p.orders >= 3)
    .sort((a, b) => (a.acos ?? 999) - (b.acos ?? 999))[0];

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-3">
        {totals.map((p) => {
          const share = grandCost ? (p.cost / grandCost) * 100 : 0;
          const isBest = best && p.placement === best.placement;
          return (
            <div
              key={p.placement}
              className={`rounded-xl border px-4 py-3 ${
                isBest ? "border-good/30 bg-good-soft" : "border-border bg-surface"
              }`}
            >
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-xs font-medium text-muted uppercase">
                  {LABEL[p.placement] ?? p.placement}
                </span>
                {isBest && <span className="text-[10px] font-semibold text-good">NAJTAŃSZY</span>}
              </div>
              <div className="tnum mt-1 text-xl font-semibold">{money(p.cost, currency)}</div>
              <div className="mt-1 flex items-center gap-2 text-xs">
                <span className={acosTone(p.acos)}>ACOS {pct(p.acos)}</span>
                <span className="text-muted">·</span>
                <span className="text-muted">{pct(share, 0)} budżetu</span>
              </div>
              <div className="mt-2 h-1 overflow-hidden rounded-full bg-surface-2">
                <div
                  className={`h-full rounded-full ${isBest ? "bg-good" : "bg-accent"}`}
                  style={{ width: `${Math.min(100, share)}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>

      <Card
        title="Placementy per kampania"
        subtitle={`${rows.length} pozycji · ${params.start} → ${params.end} · modyfikator to mnożnik stawki ustawiony w kampanii`}
      >
        {detail.loading && !detail.data ? (
          <Skeleton rows={12} />
        ) : (
          <DataTable
            data={rows}
            columns={columns}
            getRowId={(r) => `${r.campaign_id}|${r.placement}`}
            initialSort={[{ id: "cost", desc: true }]}
            emptyLabel="Brak danych o placementach"
            emptyHint="Raport placementów pobiera się razem z pozostałymi przy synchronizacji."
          />
        )}
      </Card>

      <p className="px-1 text-xs text-muted">
        Modyfikator podnosi stawkę na danym placemencie. Reguła „Przenieś budżet na lepiej
        konwertujący placement” proponuje jego zmianę — jak każda zmiana, po zatwierdzeniu.
      </p>
    </div>
  );
}
