"use client";

/* eslint-disable @typescript-eslint/no-explicit-any */

import clsx from "clsx";
import { useMemo, useState } from "react";
import { DataTable } from "@/components/DataTable";
import { Badge, Card, ErrorState, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import { useFilters } from "@/lib/filters";
import { matchLabel } from "@/lib/labels";
import { useData } from "@/lib/useData";

type Tab = "keywords" | "targets";

export default function NegativesPage() {
  const { profileId, revision } = useFilters();
  const [tab, setTab] = useState<Tab>("keywords");
  const [search, setSearch] = useState("");

  const keywords = useData(["neg-keywords", profileId, revision], () =>
    api.negativeKeywords({ profile_id: profileId ?? undefined, limit: 20000 }),
  );
  const targets = useData(["neg-targets", profileId, revision], () =>
    api.negativeTargets({ profile_id: profileId ?? undefined, limit: 20000 }),
  );

  const active = tab === "keywords" ? keywords : targets;

  const rows = useMemo(() => {
    const data: any[] = active.data ?? [];
    if (!search.trim()) return data;
    const q = search.toLowerCase();
    return data.filter((r) =>
      ((r.keyword_text ?? r.value ?? "") as string).toLowerCase().includes(q),
    );
  }, [active.data, search]);

  const keywordColumns = useMemo(
    () => [
      {
        accessorKey: "keyword_text",
        header: "Wykluczona fraza",
        meta: { width: "28%" },
        cell: (c: any) => <span className="font-medium">{c.getValue() || "—"}</span>,
      },
      {
        accessorKey: "match_type",
        header: "Dopasowanie",
        cell: (c: any) => (
          <Badge tone="neutral">{matchLabel(c.getValue())}</Badge>
        ),
      },
      {
        accessorKey: "level",
        header: "Poziom",
        cell: (c: any) => (
          <Badge tone={c.getValue() === "CAMPAIGN" ? "accent" : "neutral"}>
            {c.getValue() === "CAMPAIGN" ? "kampania" : "grupa reklam"}
          </Badge>
        ),
      },
      {
        id: "where",
        header: "Gdzie",
        accessorFn: (r: any) => r.campaign_name ?? "",
        meta: { width: "30%" },
        cell: (c: any) => (
          <div className="min-w-0 text-xs">
            <div className="truncate">{c.row.original.campaign_name || "—"}</div>
            {c.row.original.ad_group_name && (
              <div className="truncate text-muted">{c.row.original.ad_group_name}</div>
            )}
          </div>
        ),
      },
      {
        accessorKey: "country",
        header: "Rynek",
        cell: (c: any) => <span className="text-muted">{c.getValue() ?? "—"}</span>,
      },
    ],
    [],
  );

  const targetColumns = useMemo(
    () => [
      {
        accessorKey: "value",
        header: "Wykluczenie",
        meta: { width: "28%" },
        cell: (c: any) => <span className="font-mono text-xs">{c.getValue() || "—"}</span>,
      },
      {
        accessorKey: "expression_type",
        header: "Typ",
        cell: (c: any) => <Badge tone="neutral">{c.getValue() ?? "—"}</Badge>,
      },
      {
        accessorKey: "level",
        header: "Poziom",
        cell: (c: any) => (
          <Badge tone={c.getValue() === "CAMPAIGN" ? "accent" : "neutral"}>
            {c.getValue() === "CAMPAIGN" ? "kampania" : "grupa reklam"}
          </Badge>
        ),
      },
      {
        id: "where",
        header: "Gdzie",
        accessorFn: (r: any) => r.campaign_name ?? "",
        meta: { width: "30%" },
        cell: (c: any) => (
          <div className="min-w-0 text-xs">
            <div className="truncate">{c.row.original.campaign_name || "—"}</div>
            {c.row.original.ad_group_name && (
              <div className="truncate text-muted">{c.row.original.ad_group_name}</div>
            )}
          </div>
        ),
      },
      {
        accessorKey: "country",
        header: "Rynek",
        cell: (c: any) => <span className="text-muted">{c.getValue() ?? "—"}</span>,
      },
    ],
    [],
  );

  if (active.error) return <ErrorState message={active.error} />;

  return (
    <div className="space-y-4">
      <Card
        title={
          <div className="flex gap-1">
            <button
              onClick={() => setTab("keywords")}
              className={clsx(
                "rounded-lg px-2.5 py-1 text-sm font-medium transition-colors",
                tab === "keywords"
                  ? "bg-accent-soft text-accent"
                  : "text-muted hover:bg-surface-2 hover:text-text",
              )}
            >
              Wykluczone frazy ({keywords.data?.length ?? 0})
            </button>
            <button
              onClick={() => setTab("targets")}
              className={clsx(
                "rounded-lg px-2.5 py-1 text-sm font-medium transition-colors",
                tab === "targets"
                  ? "bg-accent-soft text-accent"
                  : "text-muted hover:bg-surface-2 hover:text-text",
              )}
            >
              Wykluczone ASIN-y ({targets.data?.length ?? 0})
            </button>
          </div>
        }
        subtitle={
          tab === "keywords"
            ? "Frazy, na które reklamy się nie wyświetlają. Odpowiednik negative keywords w panelu Amazona."
            : "Produkty i marki wykluczone z targetowania produktowego. Odpowiednik negative targeting."
        }
        action={
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Szukaj…"
            className="w-48 rounded-lg border border-border bg-bg px-2.5 py-1 text-sm outline-none focus:border-accent"
          />
        }
      >
        {active.loading && !active.data ? (
          <Skeleton rows={12} />
        ) : (
          <DataTable
            data={rows}
            columns={tab === "keywords" ? keywordColumns : targetColumns}
            getRowId={(r: any) => String(r.keyword_id ?? r.target_id)}
            emptyLabel={
              tab === "keywords" ? "Brak wykluczonych fraz" : "Brak wykluczonych ASIN-ów"
            }
            emptyHint="Wykluczenia pobierają się razem ze strukturą konta przy synchronizacji."
          />
        )}
      </Card>

      <p className="px-1 text-xs text-muted">
        Nowe wykluczenia dodasz na ekranie <span className="font-medium">Okazje</span> — tam
        panel wskazuje frazy, które klikają i nie sprzedają. Każde dodanie przechodzi przez
        kolejkę zatwierdzeń.
      </p>
    </div>
  );
}
