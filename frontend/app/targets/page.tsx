"use client";

/* eslint-disable @typescript-eslint/no-explicit-any */

import { Pause, Play, TrendingDown, TrendingUp } from "lucide-react";
import { useMemo, useState } from "react";
import { BulkBar, useBulkAction } from "@/components/BulkBar";
import { DraftBar, EditableNumber } from "@/components/EditableCell";
import { DataTable } from "@/components/DataTable";
import { SuggestionCell } from "@/components/SuggestionCell";
import { bidGapColumn, metricColumns, nameColumn, tosShareColumn } from "@/components/metricColumns";
import { Badge, Button, Card, ErrorState, Skeleton } from "@/components/ui";
import { api, type TargetRow } from "@/lib/api";
import { useFilters, useRangeParams } from "@/lib/filters";
import { money } from "@/lib/format";
import { matchLabel, stateLabel } from "@/lib/labels";
import { useData } from "@/lib/useData";

export default function TargetsPage() {
  const params = useRangeParams();
  const { currency, profileId, revision, bump } = useFilters();
  const [minClicks, setMinClicks] = useState(0);
  const [kind, setKind] = useState<"" | "KEYWORD" | "TARGET">("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bidPct, setBidPct] = useState("10");
  // Recznie zmienione stawki, jeszcze niewyslane. Klucz = target_id.
  const [drafts, setDrafts] = useState<Map<string, number>>(new Map());

  function setDraft(id: string, bid: number) {
    setDrafts((m) => new Map(m).set(id, bid));
  }
  function clearDraft(id: string) {
    setDrafts((m) => {
      const next = new Map(m);
      next.delete(id);
      return next;
    });
  }

  const { data, loading, error, reload } = useData(["targets", params, revision, minClicks, kind], () =>
    api.targets({ ...params, min_clicks: minClicks, kind: kind || undefined, limit: 2000 }),
  );

  const bulk = useBulkAction(() => {
    setSelected(new Set());
    setDrafts(new Map());
    reload();
    bump();
  });

  // Zmiana rynku uniewaznia zaznaczenie - ID naleza do poprzedniego profilu.
  // Reset podczas renderu (zalecany wzorzec Reacta) zamiast efektu, ktory
  // wywolalby dodatkowy przebieg renderowania.
  const [lastProfile, setLastProfile] = useState(profileId);
  if (lastProfile !== profileId) {
    setLastProfile(profileId);
    setSelected(new Set());
  }

  const byId = useMemo(() => {
    const map = new Map<string, TargetRow>();
    (data ?? []).forEach((r) => map.set(r.target_id, r));
    return map;
  }, [data]);

  const columns = useMemo(
    () => [
      nameColumn({
        id: "target",
        header: "Keyword / target",
        primary: (r) => r.target_text ?? r.target_id,
        secondary: (r) => [r.campaign_name, r.ad_group_name].filter(Boolean).join(" · "),
        width: "26%",
      }),
      {
        accessorKey: "match_type",
        header: "Dopasowanie",
        cell: (c: any) => {
          const v = c.getValue() as string | null;
          return v ? (
            <Badge tone="accent">
              <span title={v}>{matchLabel(v)}</span>
            </Badge>
          ) : (
            <span className="text-muted">—</span>
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
              {stateLabel(v) ?? "—"}
            </Badge>
          );
        },
      },
      {
        accessorKey: "bid",
        header: "Stawka",
        meta: { align: "right" },
        cell: (c: any) => {
          const r = c.row.original;
          return (
            <EditableNumber
              value={r.bid}
              draft={drafts.get(r.target_id)}
              onChange={(v) => setDraft(r.target_id, v)}
              onReset={() => clearDraft(r.target_id)}
              format={(v) => money(v, currency)}
              min={0.02}
              max={100}
              disabled={profileId === null}
              disabledHint="Wybierz konkretny rynek, żeby edytować"
            />
          );
        },
      },
      ...metricColumns({ currency }),
      bidGapColumn(),
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

  function adjustBids(direction: 1 | -1) {
    const factor = 1 + (direction * Number(bidPct.replace(",", "."))) / 100;
    if (!Number.isFinite(factor) || factor <= 0) return;

    const changes = [...selected]
      .map((id) => {
        const row = byId.get(id);
        if (!row) return null;
        // Brak stawki wlasnej = target dziedziczy default bid grupy; wtedy
        // punktem wyjscia jest realny CPC, nie zero.
        const base = row.bid ?? row.cpc ?? 0;
        if (!base) return null;
        return { id, kind: row.target_kind, bid: Math.max(0.02, Number((base * factor).toFixed(2))) };
      })
      .filter(Boolean) as Array<{ id: string; kind: string; bid: number }>;

    if (changes.length === 0) return;
    bulk.preview(() => api.planBids(profileId!, changes));
  }

  function changeState(state: "ENABLED" | "PAUSED") {
    const keywords = [...selected].filter((id) => byId.get(id)?.target_kind === "KEYWORD");
    const targets = [...selected].filter((id) => byId.get(id)?.target_kind === "TARGET");

    // Keywordy i targety to osobne zasoby w Ads API, a jeden plan obejmuje jeden
    // typ. Zamiast po cichu pominac czesc zaznaczenia, prosimy o zawezenie filtra.
    if (keywords.length && targets.length) {
      bulk.notify(
        "Zaznaczenie miesza keywordy i targety produktowe. Zawęź filtr typu i wykonaj osobno.",
      );
      return;
    }

    const [entityType, ids] = keywords.length
      ? (["KEYWORD", keywords] as const)
      : (["TARGET", targets] as const);
    bulk.preview(() => api.planState(profileId!, entityType, [...ids], state));
  }

  function submitDrafts() {
    const changes = [...drafts.entries()]
      .map(([id, bid]) => {
        const row = byId.get(id);
        return row ? { id, kind: row.target_kind, bid } : null;
      })
      .filter(Boolean) as Array<{ id: string; kind: string; bid: number }>;
    if (!changes.length) return;
    bulk.preview(() => api.planBids(profileId!, changes));
  }

  if (error) return <ErrorState message={error} />;

  return (
    <div className="space-y-4">
      <Card
        title="Targetowanie"
        subtitle={`${data?.length ?? 0} pozycji · ${params.start} → ${params.end}`}
        action={
          <div className="flex items-center gap-2">
            <select
              value={kind}
              onChange={(e) => setKind(e.target.value as typeof kind)}
              className="rounded-lg border border-border bg-bg px-2 py-1 text-sm outline-none focus:border-accent"
            >
              <option value="">Wszystkie typy</option>
              <option value="KEYWORD">Keywordy</option>
              <option value="TARGET">Targety produktowe</option>
            </select>
            <select
              value={minClicks}
              onChange={(e) => setMinClicks(Number(e.target.value))}
              className="rounded-lg border border-border bg-bg px-2 py-1 text-sm outline-none focus:border-accent"
            >
              <option value={0}>Bez progu</option>
              <option value={5}>Min. 5 kliknięć</option>
              <option value={10}>Min. 10 kliknięć</option>
              <option value={25}>Min. 25 kliknięć</option>
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
            getRowId={(r) => r.target_id}
            initialSort={[{ id: "cost", desc: true }]}
            selected={selected}
            onSelectedChange={setSelected}
            emptyLabel="Brak danych targetowania"
          />
        )}
      </Card>

      <DraftBar
        count={drafts.size}
        label={drafts.size === 1 ? "zmieniona stawka" : "zmienionych stawek"}
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
        <div className="flex items-center gap-1">
          <input
            value={bidPct}
            onChange={(e) => setBidPct(e.target.value)}
            inputMode="decimal"
            className="tnum w-14 rounded-lg border border-border bg-bg px-2 py-1 text-xs outline-none focus:border-accent"
          />
          <span className="text-xs text-muted">%</span>
          <Button size="sm" onClick={() => adjustBids(1)}>
            <TrendingUp className="size-3.5" /> Podnieś
          </Button>
          <Button size="sm" onClick={() => adjustBids(-1)}>
            <TrendingDown className="size-3.5" /> Obniż
          </Button>
        </div>
        <div className="mx-1 h-4 w-px bg-border" />
        <Button size="sm" onClick={() => changeState("PAUSED")}>
          <Pause className="size-3.5" /> Wstrzymaj
        </Button>
        <Button size="sm" onClick={() => changeState("ENABLED")}>
          <Play className="size-3.5" /> Wznów
        </Button>
      </BulkBar>
    </div>
  );
}
