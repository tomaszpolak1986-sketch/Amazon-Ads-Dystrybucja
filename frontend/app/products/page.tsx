"use client";

/* eslint-disable @typescript-eslint/no-explicit-any */

import { useMemo } from "react";
import { DataTable } from "@/components/DataTable";
import { metricColumns, tacosColumn } from "@/components/metricColumns";
import { Badge, Card, ErrorState, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import { useFilters, useRangeParams } from "@/lib/filters";
import { useData } from "@/lib/useData";

export default function ProductsPage() {
  const params = useRangeParams();
  const { currency, revision } = useFilters();

  const { data, loading, error } = useData(["products", params, revision], () => api.products({ ...params, limit: 2000 }));

  const columns = useMemo(
    () => [
      {
        accessorKey: "sku",
        header: "Produkt",
        meta: { width: "26%" },
        cell: (c: any) => {
          const r = c.row.original;
          return (
            <div className="flex min-w-0 items-center gap-2">
              {/* Zwykly <img>: obrazy Amazona sa zewnetrzne i nie ma sensu
                  przepuszczac ich przez optymalizator Next.
                  loading="lazy" ma tu szczegolne znaczenie: tabela renderuje
                  ponad 1600 wierszy, a widac osiem naraz - bez tego wejscie na
                  zakladke to 1600 zadan zamiast kilkunastu. */}
              {r.catalog?.image && (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={r.catalog.image}
                  alt=""
                  width={40}
                  height={40}
                  loading="lazy"
                  decoding="async"
                  className="size-10 shrink-0 rounded border border-border bg-surface-2 object-contain"
                />
              )}
              <div className="min-w-0">
                {r.catalog?.title && (
                  <div className="truncate text-[13px]" title={r.catalog.title}>
                    {r.catalog.title}
                  </div>
                )}
                <div className="truncate font-medium" title={c.getValue() ?? ""}>
                  {c.getValue() ?? "—"}
                </div>
                {r.asin && (
                  <div className="font-mono text-[11px] text-muted">{r.asin}</div>
                )}
              </div>
            </div>
          );
        },
      },
      {
        accessorKey: "country",
        header: "Rynek",
        cell: (c: any) => <Badge tone="neutral">{c.getValue() ?? "—"}</Badge>,
      },
      ...metricColumns({ currency }),
      tacosColumn(),
    ],
    [currency],
  );

  if (error) return <ErrorState message={error} />;

  return (
    <Card
      title="Produkty"
      subtitle={`Wyniki reklamowe per SKU · ${params.start} → ${params.end}`}
    >
      {loading && !data ? (
        <Skeleton rows={12} />
      ) : (
        <DataTable
          data={data ?? []}
          columns={columns}
          getRowId={(r) => `${r.country}|${r.sku}`}
          initialSort={[{ id: "cost", desc: true }]}
          emptyLabel="Brak danych produktowych"
          emptyHint="Raport advertised product pobiera się razem z pozostałymi przy synchronizacji."
        />
      )}
    </Card>
  );
}
