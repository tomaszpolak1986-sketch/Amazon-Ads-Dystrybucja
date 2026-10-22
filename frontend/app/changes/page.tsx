"use client";

/* eslint-disable @typescript-eslint/no-explicit-any */

import { useMemo } from "react";
import { DataTable } from "@/components/DataTable";
import { Effectiveness } from "@/components/Effectiveness";
import { Badge, Card, ErrorState, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import { useFilters } from "@/lib/filters";
import { dateTime } from "@/lib/format";
import { useData } from "@/lib/useData";

export default function ChangesPage() {
  const { profileId, revision, currency } = useFilters();

  const changes = useData(["changes", profileId, revision], () =>
    api.changes({ profile_id: profileId ?? undefined, limit: 500 }),
  );
  const runs = useData(["sync-runs", revision], () => api.syncRuns());
  // Wlasne okno, niezalezne od filtra dat: liczy sie data zmiany, nie zakres
  // wybrany u gory.
  const eff = useData(["effectiveness", profileId, revision], () =>
    api.effectiveness(profileId),
  );

  const columns = useMemo(
    () => [
      {
        accessorKey: "ts",
        header: "Kiedy",
        meta: { width: "12%" },
        cell: (c: any) => <span className="tnum text-xs">{dateTime(c.getValue())}</span>,
      },
      {
        accessorKey: "entity_type",
        header: "Obiekt",
        cell: (c: any) => (
          <div>
            <Badge tone="neutral">{c.getValue()}</Badge>
            <div className="mt-0.5 font-mono text-[11px] text-muted">
              {c.row.original.entity_id}
            </div>
          </div>
        ),
      },
      { accessorKey: "field", header: "Pole" },
      {
        id: "change",
        header: "Zmiana",
        accessorFn: (r: any) => `${r.old_value ?? ""}→${r.new_value ?? ""}`,
        cell: (c: any) => (
          <span className="tnum text-xs">
            <span className="text-muted line-through">{c.row.original.old_value ?? "—"}</span>
            <span className="mx-1.5 text-muted">→</span>
            <span className="font-medium">{c.row.original.new_value ?? "—"}</span>
          </span>
        ),
      },
      {
        accessorKey: "source",
        header: "Źródło",
        cell: (c: any) => (
          <Badge tone={c.getValue() === "RULE" ? "accent" : "neutral"}>{c.getValue()}</Badge>
        ),
      },
      {
        accessorKey: "status",
        header: "Status",
        cell: (c: any) => {
          const v = c.getValue() as string;
          const tone = v === "APPLIED" ? "good" : v === "DRY_RUN" ? "neutral" : "bad";
          return (
            <div>
              <Badge tone={tone}>{v === "DRY_RUN" ? "SYMULACJA" : v}</Badge>
              {c.row.original.error && (
                <div className="mt-0.5 max-w-xs truncate text-[11px] text-bad" title={c.row.original.error}>
                  {c.row.original.error}
                </div>
              )}
            </div>
          );
        },
      },
    ],
    [],
  );

  if (changes.error) return <ErrorState message={changes.error} />;

  return (
    <div className="space-y-4">
      <Effectiveness data={eff.data} loading={eff.loading} currency={currency} />

      <Card
        title="Historia zmian"
        subtitle="Każda zmiana wysłana do Amazona — ręczna i automatyczna. Symulacje też są zapisywane."
      >
        {changes.loading && !changes.data ? (
          <Skeleton rows={10} />
        ) : (
          <DataTable
            data={changes.data ?? []}
            columns={columns}
            getRowId={(r) => String(r.id)}
            emptyLabel="Nie wprowadzono jeszcze żadnych zmian"
            emptyHint="Zaznacz wiersze na ekranie kampanii, targetowania lub okazji i użyj akcji masowej."
            maxHeight="calc(100vh - 28rem)"
          />
        )}
      </Card>

      <Card title="Synchronizacje" subtitle="Ostatnie pobrania danych z Amazon Ads API">
        {runs.loading && !runs.data ? (
          <Skeleton rows={4} />
        ) : (runs.data ?? []).length === 0 ? (
          <p className="px-4 py-6 text-sm text-muted">
            Nie uruchomiono jeszcze synchronizacji. Użyj przycisku „Sync” u góry.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-xs text-muted">
                  <th className="px-4 py-2 text-left font-semibold">Typ</th>
                  <th className="px-4 py-2 text-left font-semibold">Profil</th>
                  <th className="px-4 py-2 text-left font-semibold">Start</th>
                  <th className="px-4 py-2 text-left font-semibold">Koniec</th>
                  <th className="px-4 py-2 text-right font-semibold">Wierszy</th>
                  <th className="px-4 py-2 text-left font-semibold">Status</th>
                </tr>
              </thead>
              <tbody>
                {(runs.data ?? []).slice(0, 25).map((r) => (
                  <tr key={r.id} className="border-b border-border/60 last:border-0">
                    <td className="px-4 py-1.5 font-medium">{r.kind}</td>
                    <td className="tnum px-4 py-1.5 text-muted">{r.profile_id ?? "—"}</td>
                    <td className="tnum px-4 py-1.5 text-xs">{dateTime(r.started_at)}</td>
                    <td className="tnum px-4 py-1.5 text-xs">{dateTime(r.finished_at)}</td>
                    <td className="tnum px-4 py-1.5 text-right">{r.rows}</td>
                    <td className="px-4 py-1.5">
                      <Badge
                        tone={r.status === "OK" ? "good" : r.status === "RUNNING" ? "accent" : "bad"}
                      >
                        {r.status}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
