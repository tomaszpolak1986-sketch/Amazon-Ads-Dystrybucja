"use client";

/* eslint-disable @typescript-eslint/no-explicit-any */

import { useMemo, useState } from "react";
import { DataTable } from "@/components/DataTable";
import { metricColumns, nameColumnWithThumb } from "@/components/metricColumns";
import { Badge, Card, ErrorState, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import { useFilters, useRangeParams } from "@/lib/filters";
import { useData } from "@/lib/useData";

export default function SearchTermsPage() {
  const params = useRangeParams();
  const { currency, revision } = useFilters();
  const [search, setSearch] = useState("");
  const [minClicks, setMinClicks] = useState(1);

  const { data, loading, error } = useData(["search-terms", params, revision, minClicks, search], () =>
    api.searchTerms({ ...params, min_clicks: minClicks, search: search || undefined, limit: 3000 }),
  );

  const columns = useMemo(
    () => [
      nameColumnWithThumb({
        id: "search_term",
        header: "Fraza wyszukiwania",
        primary: (r) => r.search_term,
        secondary: (r) => [r.campaign_name, r.ad_group_name].filter(Boolean).join(" · "),
        width: "28%",
      }),
      {
        id: "target_text",
        header: "Z keyworda",
        accessorFn: (r: any) => r.target_text ?? "",
        cell: (c: any) => (
          <div className="min-w-0">
            <div className="truncate text-xs">{c.getValue() || "—"}</div>
            {c.row.original.match_type && (
              <Badge tone="neutral">{c.row.original.match_type}</Badge>
            )}
          </div>
        ),
      },
      ...metricColumns({ currency }),
    ],
    [currency],
  );

  if (error) return <ErrorState message={error} />;

  return (
    <Card
      title="Frazy wyszukiwania"
      subtitle={`${data?.length ?? 0} fraz · ${params.start} → ${params.end}`}
      action={
        <div className="flex items-center gap-2">
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Szukaj frazy…"
            className="w-48 rounded-lg border border-border bg-bg px-2.5 py-1 text-sm outline-none focus:border-accent"
          />
          <select
            value={minClicks}
            onChange={(e) => setMinClicks(Number(e.target.value))}
            className="rounded-lg border border-border bg-bg px-2 py-1 text-sm outline-none focus:border-accent"
          >
            <option value={0}>Wszystkie</option>
            <option value={1}>Min. 1 kliknięcie</option>
            <option value={5}>Min. 5 kliknięć</option>
            <option value={10}>Min. 10 kliknięć</option>
          </select>
        </div>
      }
    >
      {loading && !data ? (
        <Skeleton rows={12} />
      ) : (
        <DataTable
          data={data ?? []}
          columns={columns}
          getRowId={(r) => `${r.campaign_id}|${r.target_id}|${r.search_term}`}
          initialSort={[{ id: "cost", desc: true }]}
          emptyLabel="Brak fraz w tym okresie"
          emptyHint="Raport search term pojawia się po pierwszym synchronizowaniu raportów."
        />
      )}
    </Card>
  );
}
