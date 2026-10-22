"use client";

/* eslint-disable @typescript-eslint/no-explicit-any */

import { Layers, Pause, Play, Plus, Wallet, X } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";
import { BulkBar, useBulkAction } from "@/components/BulkBar";
import { DraftBar, EditableNumber } from "@/components/EditableCell";
import { DataTable } from "@/components/DataTable";
import { SuggestionCell } from "@/components/SuggestionCell";
import { metricColumns, tosShareColumn } from "@/components/metricColumns";
import { Badge, Button, Card, ErrorState, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import type { CampaignRow } from "@/lib/api";
import { useFilters, useRangeParams } from "@/lib/filters";
import { money } from "@/lib/format";
import { useData } from "@/lib/useData";

// Taktyki Sponsored Display. Kod `T00020` nie mowi nic, a to dwa rozne
// mechanizmy zakupu - warto je rozroznic juz na liscie.
const TAKTYKI_SD: Record<string, string> = {
  T00020: "remarketing wyświetleń",
  T00030: "targetowanie odbiorców",
};

export default function CampaignsPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  // Filtr po portfolio przychodzi z adresu, nie ze stanu - dzieki temu link
  // z ekranu Portfolia dziala takze po odswiezeniu i da sie go zapisac
  const portfolioParam = searchParams.get("portfolio");
  const portfolioId = portfolioParam === null ? null : Number(portfolioParam);

  const params = useRangeParams();
  const { currency, profileId, revision, bump } = useFilters();
  const [search, setSearch] = useState("");
  const [stateFilter, setStateFilter] = useState<"" | "ENABLED" | "PAUSED">("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [budgetInput, setBudgetInput] = useState("");
  // Recznie zmienione budzety, jeszcze niewyslane
  const [drafts, setDrafts] = useState<Map<string, number>>(new Map());

  const { data, loading, error, reload } = useData(
    ["campaigns", params, revision, portfolioId, stateFilter],
    () =>
      api.campaigns({
        ...params,
        limit: 1000,
        ...(stateFilter ? { state: stateFilter } : {}),
        ...(portfolioId !== null ? { portfolio_id: portfolioId } : {}),
      }),
  );

  const portfolioName =
    portfolioId === null
      ? null
      : portfolioId === 0
        ? "Bez portfolio"
        : ((data ?? []).find((r) => r.portfolio_id === portfolioId)?.portfolio_name ??
          `#${portfolioId}`);

  const bulk = useBulkAction(() => {
    setSelected(new Set());
    setDrafts(new Map());
    reload();
    bump();
  });

  const rows = useMemo(() => {
    if (!data) return [];
    if (!search.trim()) return data;
    const q = search.toLowerCase();
    return data.filter((r) => (r.name ?? "").toLowerCase().includes(q));
  }, [data, search]);

  // Zmiana rynku uniewaznia zaznaczenie - ID naleza do poprzedniego profilu.
  // Reset podczas renderu (zalecany wzorzec Reacta) zamiast efektu, ktory
  // wywolalby dodatkowy przebieg renderowania.
  const [lastProfile, setLastProfile] = useState(profileId);
  if (lastProfile !== profileId) {
    setLastProfile(profileId);
    setSelected(new Set());
  }

  const columns = useMemo(
    () => [
      {
        id: "name",
        header: "Kampania",
        accessorFn: (r: CampaignRow) => r.name ?? r.campaign_id,
        meta: { width: "30%" },
        cell: (c: { row: { original: CampaignRow } }) => {
          const r = c.row.original;
          const opis = [
            r.country,
            // Przy SD `targeting_type` to taktyka - kod Amazona nic nie mowi,
            // wiec tlumaczymy go na to, czym naprawde jest.
            TAKTYKI_SD[r.targeting_type ?? ""] ?? r.targeting_type,
            r.bidding_strategy?.replace("LEGACY_FOR_", ""),
          ]
            .filter(Boolean)
            .join(" · ");
          return (
            <div className="min-w-0">
              <div className="flex items-center gap-1.5">
                {/* Typ produktu reklamowego - bez tego SP, SB i SD wygladaja
                    identycznie, a rzadzi nimi zupelnie inna logika. */}
                {r.ad_product && r.ad_product !== "SP" && (
                  <Badge tone="accent">{r.ad_product}</Badge>
                )}
                <span className="truncate font-medium" title={r.name ?? ""}>
                  {r.name ?? r.campaign_id}
                </span>
              </div>
              {opis && (
                <div className="truncate text-[11px] text-muted" title={opis}>
                  {opis}
                </div>
              )}
            </div>
          );
        },
      },
      {
        accessorKey: "state",
        header: "Status",
        cell: (c: any) => {
          const v = c.getValue() as string | null;
          return (
            <Badge tone={v === "ENABLED" ? "good" : v === "PAUSED" ? "warn" : "neutral"}>
              {v ?? "—"}
            </Badge>
          );
        },
      },
      {
        id: "portfolio",
        header: "Portfolio",
        accessorFn: (r: any) => r.portfolio_name ?? "",
        cell: (c: any) => {
          const r = c.row.original;
          if (!r.portfolio_name) return <span className="text-muted">—</span>;
          return (
            <Link
              href={`/campaigns?portfolio=${r.portfolio_id}`}
              className="truncate text-xs text-accent hover:underline"
            >
              {r.portfolio_name}
            </Link>
          );
        },
      },
      {
        accessorKey: "budget",
        header: "Budżet",
        meta: { align: "right" },
        cell: (c: any) => {
          const r = c.row.original;
          return (
            <EditableNumber
              value={r.budget}
              draft={drafts.get(r.campaign_id)}
              onChange={(v) =>
                setDrafts((m) => new Map(m).set(r.campaign_id, v))
              }
              onReset={() =>
                setDrafts((m) => {
                  const next = new Map(m);
                  next.delete(r.campaign_id);
                  return next;
                })
              }
              format={(v) => money(v, r.currency ?? currency)}
              min={1}
              max={100000}
              disabled={profileId === null}
              disabledHint="Wybierz konkretny rynek, żeby edytować"
            />
          );
        },
      },
      ...metricColumns({ currency }),
      tosShareColumn(),
      {
        id: "suggestion",
        header: "Sugestia",
        meta: { width: "16%" },
        enableSorting: false,
        cell: (c: any) => <SuggestionCell suggestions={c.row.original.suggestions} />,
      },
    ],
    [currency, drafts, profileId],
  );

  const blockedReason =
    profileId === null ? "Wybierz konkretny rynek, żeby wprowadzać zmiany" : null;

  function changeState(state: "ENABLED" | "PAUSED") {
    const ids = [...selected];
    bulk.preview(() => api.planState(profileId!, "CAMPAIGN", ids, state));
  }

  function changeBudget() {
    const value = Number(budgetInput.replace(",", "."));
    if (!value || value <= 0) return;
    const changes = [...selected].map((campaign_id) => ({ campaign_id, budget: value }));
    bulk.preview(() => api.planBudgets(profileId!, changes));
  }

  const counts = useMemo(() => {
    const enabled = rows.filter((r) => r.state === "ENABLED").length;
    return { enabled, paused: rows.length - enabled };
  }, [rows]);

  function submitDrafts() {
    const changes = [...drafts.entries()].map(([campaign_id, budget]) => ({
      campaign_id,
      budget,
    }));
    if (!changes.length) return;
    bulk.preview(() => api.planBudgets(profileId!, changes));
  }

  if (error) return <ErrorState message={error} />;

  return (
    <div className="space-y-4">
      {portfolioId !== null && (
        <div className="flex items-center gap-2 rounded-lg border border-accent/30 bg-accent-soft px-3 py-2 text-sm">
          <Layers className="size-4 shrink-0 text-accent" />
          <span>
            Portfolio: <span className="font-medium">{portfolioName}</span>
          </span>
          <button
            onClick={() => router.push("/campaigns")}
            className="ml-auto flex items-center gap-1 text-xs text-muted hover:text-text"
          >
            <X className="size-3.5" /> Pokaż wszystkie
          </button>
        </div>
      )}

      <Card
        title="Kampanie"
        subtitle={`${rows.length} kampanii — ${counts.enabled} aktywnych, ${counts.paused} wstrzymanych · ${params.start} → ${params.end}`}
        action={
          <div className="flex items-center gap-2">
            <select
              value={stateFilter}
              onChange={(e) => setStateFilter(e.target.value as typeof stateFilter)}
              className="rounded-lg border border-border bg-bg px-2 py-1 text-sm outline-none focus:border-accent"
            >
              <option value="">Aktywne i wstrzymane</option>
              <option value="ENABLED">Tylko aktywne</option>
              <option value="PAUSED">Tylko wstrzymane</option>
            </select>
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Szukaj po nazwie…"
              className="w-48 rounded-lg border border-border bg-bg px-2.5 py-1 text-sm outline-none focus:border-accent"
            />
            <Link href="/campaigns/new">
              <Button size="sm" variant="primary">
                <Plus className="size-3.5" /> Nowa kampania
              </Button>
            </Link>
          </div>
        }
      >
        {loading && !data ? (
          <Skeleton rows={10} />
        ) : (
          <DataTable
            data={rows}
            columns={columns}
            getRowId={(r) => r.campaign_id}
            onRowClick={(r) => router.push(`/campaigns/${r.campaign_id}`)}
            initialSort={[{ id: "cost", desc: true }]}
            selected={selected}
            onSelectedChange={setSelected}
            emptyLabel="Brak kampanii w tym okresie"
            emptyHint="Uruchom sync, żeby pobrać dane z Amazona."
          />
        )}
      </Card>

      <DraftBar
        count={drafts.size}
        label={drafts.size === 1 ? "zmieniony budżet" : "zmienionych budżetów"}
        onDiscard={() => setDrafts(new Map())}
        onSubmit={submitDrafts}
        busy={bulk.busy}
      />

      <BulkBar
        count={selected.size}
        onClear={() => setSelected(new Set())}
        blockedReason={blockedReason}
        pending={bulk.pending}
        busy={bulk.busy}
        result={bulk.result}
        onConfirm={bulk.confirm}
        onCancel={bulk.cancel}
      >
        <Button size="sm" onClick={() => changeState("PAUSED")}>
          <Pause className="size-3.5" /> Wstrzymaj
        </Button>
        <Button size="sm" onClick={() => changeState("ENABLED")}>
          <Play className="size-3.5" /> Wznów
        </Button>
        <div className="flex items-center gap-1">
          <input
            value={budgetInput}
            onChange={(e) => setBudgetInput(e.target.value)}
            placeholder="budżet"
            inputMode="decimal"
            className="tnum w-20 rounded-lg border border-border bg-bg px-2 py-1 text-xs outline-none focus:border-accent"
          />
          <Button size="sm" onClick={changeBudget} disabled={!budgetInput}>
            <Wallet className="size-3.5" /> Ustaw
          </Button>
        </div>
      </BulkBar>
    </div>
  );
}
