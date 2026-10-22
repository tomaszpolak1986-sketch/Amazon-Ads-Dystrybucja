"use client";

/* eslint-disable @typescript-eslint/no-explicit-any */

import clsx from "clsx";
import {
  ArrowLeft,
  Ban,
  Layers,
  MapPin,
  Package,
  Search,
  Target as TargetIcon,
  Users,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";
import { ProductsTab } from "@/components/ProductsTab";
import { AddNegatives } from "@/components/AddNegatives";
import { BulkBar, useBulkAction } from "@/components/BulkBar";
import { DataTable } from "@/components/DataTable";
import { SuggestionCell } from "@/components/SuggestionCell";
import { DraftBar, EditableNumber } from "@/components/EditableCell";
import { metricColumns, nameColumn, nameColumnWithThumb } from "@/components/metricColumns";
import { Badge, Button, Card, ErrorState, KpiCard, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import { useFilters, useRangeParams } from "@/lib/filters";
import { acosTone, money, num, pct, ratio } from "@/lib/format";
import { useData } from "@/lib/useData";

type Tab = "adgroups" | "products" | "targeting" | "terms" | "placements" | "negatives";

const TABS: Array<{ key: Tab; label: string; icon: typeof Users }> = [
  { key: "adgroups", label: "Grupy reklam", icon: Users },
  { key: "products", label: "Produkty", icon: Package },
  { key: "targeting", label: "Targetowanie", icon: TargetIcon },
  { key: "terms", label: "Frazy wyszukiwania", icon: Search },
  { key: "placements", label: "Placementy", icon: MapPin },
  { key: "negatives", label: "Wykluczenia", icon: Ban },
];

// Rodzaje wykluczen po polsku - Amazon zwraca stale, ktore nic nie mowia
const NEG_KIND: Record<string, string> = {
  NEGATIVE_EXACT: "dokładne",
  NEGATIVE_PHRASE: "frazowe",
  ASIN_SAME_AS: "produkt",
  ASIN_BRAND_SAME_AS: "marka",
  ASIN_EXPANDED_FROM: "produkt (rozszerzony)",
};

const PLACEMENT_LABEL: Record<string, string> = {
  PLACEMENT_TOP: "Góra wyników",
  PLACEMENT_REST_OF_SEARCH: "Pozostałe wyniki",
  PLACEMENT_PRODUCT_PAGE: "Karta produktu",
  // Amazon pozwala ustawic ten modyfikator, ale nie raportuje go osobno -
  // dlatego wiersz ma mnoznik, a metryki puste.
  SITE_AMAZON_BUSINESS: "Amazon Business (B2B)",
};

export default function CampaignDetailPage() {
  const { id } = useParams<{ id: string }>();
  const params = useRangeParams();
  const { currency, revision, profileId, bump } = useFilters();
  const [tab, setTab] = useState<Tab>("adgroups");
  // Modyfikatory placementow zmienione recznie, jeszcze niewyslane
  const [modDrafts, setModDrafts] = useState<Map<string, number>>(new Map());
  // Recznie zmienione stawki keywordow i targetow tej kampanii
  const [bidDrafts, setBidDrafts] = useState<Map<string, number>>(new Map());
  // Zaznaczone grupy reklam - do wstrzymania albo wznowienia
  const [selectedGroups, setSelectedGroups] = useState<Set<string>>(new Set());
  // Zaznaczone reklamy produktowe - do wstrzymania albo wznowienia
  const [selectedAds, setSelectedAds] = useState<Set<string>>(new Set());
  // Zaznaczone keywordy i targety produktowe
  const [selectedTargets, setSelectedTargets] = useState<Set<string>>(new Set());
  // Zaznaczone wykluczenia - keywordy i wykluczone ASIN-y razem
  const [selectedNegs, setSelectedNegs] = useState<Set<string>>(new Set());
  // Zaznaczone frazy wyszukiwania - do wykluczenia w ich wlasnej grupie
  const [selectedTerms, setSelectedTerms] = useState<Set<string>>(new Set());
  // Do ktorej grupy dokladamy produkty i co czeka na wyslanie
  const [grupaDocelowa, setGrupaDocelowa] = useState<string>("");
  const [doDodania, setDoDodania] = useState<string[]>([]);

  const detail = useData(["campaign-detail", id, params, revision], () =>
    api.campaignDetail(id, { start: params.start, end: params.end }),
  );

  // Zakladki ladujemy dopiero po wejsciu - kampania z 5 tys. fraz nie ma powodu
  // pobierac ich, jesli patrzysz na grupy reklam
  const adGroups = useData(["cd-adgroups", id, params, revision, tab], () =>
    tab === "adgroups" || tab === "products" || tab === "negatives"
      ? api.adGroups({ ...params, campaign_id: id })
      : Promise.resolve([]),
  );
  const productAds = useData(["cd-products", id, params, revision, tab], () =>
    tab === "products"
      ? api.productAds({ ...params, campaign_id: id, limit: 2000 })
      : Promise.resolve([]),
  );
  const targets = useData(["cd-targets", id, params, revision, tab], () =>
    tab === "targeting"
      ? api.targets({ ...params, campaign_id: id, limit: 5000 })
      : Promise.resolve([]),
  );
  const terms = useData(["cd-terms", id, params, revision, tab], () =>
    tab === "terms"
      ? api.searchTerms({ ...params, campaign_id: id, limit: 5000 })
      : Promise.resolve([]),
  );
  const placements = useData(["cd-placements", id, params, revision, tab], () =>
    tab === "placements"
      ? api.placementsByCampaign({ ...params, campaign_id: id })
      : Promise.resolve([]),
  );
  const negKeywords = useData(["cd-negkw", id, revision, tab], () =>
    tab === "negatives" ? api.negativeKeywords({ campaign_id: id }) : Promise.resolve([]),
  );
  const negTargets = useData(["cd-negtg", id, revision, tab], () =>
    tab === "negatives" ? api.negativeTargets({ campaign_id: id }) : Promise.resolve([]),
  );

  // Deklarowane po zapytaniach, bo callback siega do ich metod reload
  const bulk = useBulkAction(() => {
    setModDrafts(new Map());
    setBidDrafts(new Map());
    setSelectedGroups(new Set());
    setSelectedAds(new Set());
    setSelectedTargets(new Set());
    setSelectedNegs(new Set());
    setSelectedTerms(new Set());
    setDoDodania([]);
    negKeywords.reload();
    negTargets.reload();
    adGroups.reload();
    productAds.reload();
    targets.reload();
    detail.reload();
    placements.reload();
    bump();
  });

  function changeGroupState(state: "ENABLED" | "PAUSED") {
    if (profileId === null || !selectedGroups.size) return;
    const ids = [...selectedGroups];
    bulk.preview(() => api.planState(profileId, "AD_GROUP", ids, state));
  }

  function changeTargetState(state: "ENABLED" | "PAUSED") {
    if (profileId === null || !selectedTargets.size) return;
    const wiersze = targets.data ?? [];
    const byId = new Map(wiersze.map((t) => [t.target_id, t]));
    const keywords = [...selectedTargets].filter(
      (id) => byId.get(id)?.target_kind === "KEYWORD",
    );
    const produkty = [...selectedTargets].filter(
      (id) => byId.get(id)?.target_kind === "TARGET",
    );

    // Keywordy i targety produktowe to osobne zasoby w Ads API, a jeden plan
    // obejmuje jeden typ. Zamiast po cichu pominac czesc zaznaczenia,
    // mowimy o tym wprost.
    if (keywords.length && produkty.length) {
      bulk.notify(
        "Zaznaczenie miesza keywordy i targety produktowe — wykonaj osobno dla każdego typu.",
      );
      return;
    }

    const [entityType, ids] = keywords.length
      ? (["KEYWORD", keywords] as const)
      : (["TARGET", produkty] as const);
    bulk.preview(() => api.planState(profileId, entityType, [...ids], state));
  }

  function excludeTerms(matchType: string) {
    if (profileId === null || !selectedTerms.size) return;
    // Klucz musi byc ten sam, ktorym tabela identyfikuje wiersz - ta sama
    // fraza potrafi paść z kilku targetow i sa to osobne wiersze.
    const wybrane = (terms.data ?? []).filter((t) =>
      selectedTerms.has(`${t.target_id}|${t.search_term}`),
    );

    // W kampaniach produktowych "fraza" bywa ASIN-em konkurencji. Takiego
    // nie wyklucza sie negatywnym keywordem - Amazon ma na to osobny zasob
    // (negative targeting). Negatyw z ASIN-em po prostu by nie zadzialal.
    const asiny = wybrane.filter((t) => t.asin);
    const frazy = wybrane.filter((t) => !t.asin);

    if (frazy.length) {
      const items = frazy.map((t) => ({
        campaignId: t.campaign_id,
        adGroupId: t.ad_group_id ?? undefined,
        keywordText: t.search_term,
        matchType,
      }));
      bulk.preview(() => api.planNegatives(profileId, items, "AD_GROUP"));
    }
    if (asiny.length) {
      const items = asiny
        .filter((t) => t.ad_group_id)
        .map((t) => ({
          campaignId: t.campaign_id,
          adGroupId: t.ad_group_id as string,
          asin: t.asin as string,
        }));
      if (items.length) {
        bulk.preview(() => api.planNegativeAsins(profileId, items));
      }
    }
  }

  function removeNegatives() {
    if (profileId === null || !selectedNegs.size) return;
    // Wykluczenia keywordowe i produktowe to osobne zasoby, ale jeden plan
    // moze objac oba - rozdzielamy je dopiero przy wysylce do Amazona.
    const kw = (negKeywords.data ?? [])
      .filter((n) => selectedNegs.has(String(n.keyword_id)))
      .map((n) => String(n.keyword_id));
    const tg = (negTargets.data ?? [])
      .filter((n) => selectedNegs.has(String(n.target_id)))
      .map((n) => String(n.target_id));
    bulk.preview(() => api.planRemoveNegatives(profileId, kw, tg));
  }

  function changeAdState(state: "ENABLED" | "PAUSED") {
    if (profileId === null || !selectedAds.size) return;
    const ids = [...selectedAds];
    // Reklamy produktowe maja wlasny endpoint - `state` obsluguje tylko te
    // typy, ktore Ads API aktualizuje przez zasob o tej samej nazwie.
    bulk.preview(() => api.planProductAds(profileId, ids, state));
  }

  const c = detail.data;

  const adGroupColumns = useMemo(
    () => [
      nameColumn({
        id: "name",
        header: "Grupa reklam",
        primary: (r) => r.name ?? r.ad_group_id,
        secondary: (r) => (r.state === "PAUSED" ? "wstrzymana" : null),
        width: "26%",
      }),
      {
        accessorKey: "default_bid",
        header: "Stawka domyślna",
        meta: { align: "right" },
        cell: (x: any) => money(x.getValue(), currency),
      },
      ...metricColumns({ currency }),
    ],
    [currency],
  );

  const productColumns = useMemo(
    () => [
      {
        id: "produkt",
        header: "Produkt",
        accessorFn: (r: any) => r.catalog?.title ?? r.sku ?? r.asin ?? "",
        meta: { width: "30%" },
        cell: (c: any) => {
          const r = c.row.original;
          return (
            <div className="flex min-w-0 items-center gap-2">
              {r.catalog?.image ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={r.catalog.image}
                  alt=""
                  width={32}
                  height={32}
                  loading="lazy"
                  decoding="async"
                  className="size-8 shrink-0 rounded border border-border bg-surface-2 object-contain"
                />
              ) : (
                <span className="size-8 shrink-0 rounded border border-border bg-surface-2" />
              )}
              <div className="min-w-0">
                <div className="truncate text-xs" title={r.catalog?.title ?? ""}>
                  {r.catalog?.title ?? r.sku ?? r.asin}
                </div>
                <div className="truncate font-mono text-[11px] text-muted">
                  {r.sku}
                  {r.asin && ` · ${r.asin}`}
                </div>
              </div>
            </div>
          );
        },
      },
      {
        accessorKey: "ad_group_name",
        header: "Grupa reklam",
        meta: { width: "16%" },
        cell: (c: any) => (
          <span className="truncate text-xs text-muted" title={c.getValue() ?? ""}>
            {c.getValue() ?? "—"}
          </span>
        ),
      },
      {
        accessorKey: "state",
        header: "Status",
        cell: (c: any) =>
          c.getValue() === "PAUSED" ? (
            <Badge tone="warn">wstrzymana</Badge>
          ) : (
            <Badge tone="good">aktywna</Badge>
          ),
      },
      ...metricColumns({ currency }),
    ],
    [currency],
  );

  const targetColumns = useMemo(
    () => [
      nameColumn({
        id: "target",
        header: "Keyword / target",
        primary: (r) => r.target_text ?? r.target_id,
        secondary: (r) => r.ad_group_name,
        width: "26%",
      }),
      {
        id: "match",
        header: "Dopasowanie",
        accessorFn: (r: any) => r.match_label ?? r.match_type ?? "",
        cell: (x: any) =>
          x.getValue() ? (
            <Badge tone="accent">{x.getValue()}</Badge>
          ) : (
            <span className="text-muted">—</span>
          ),
      },
      {
        accessorKey: "bid",
        header: "Stawka",
        meta: { align: "right" },
        cell: (x: any) => {
          const r = x.row.original;
          return (
            <EditableNumber
              value={r.bid}
              draft={bidDrafts.get(r.target_id)}
              onChange={(v) => setBidDrafts((m) => new Map(m).set(r.target_id, v))}
              onReset={() =>
                setBidDrafts((m) => {
                  const next = new Map(m);
                  next.delete(r.target_id);
                  return next;
                })
              }
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
      {
        id: "suggestion",
        header: "Sugestia",
        meta: { width: "18%" },
        enableSorting: false,
        cell: (x: any) => <SuggestionCell suggestions={x.row.original.suggestions} />,
      },
    ],
    [currency, bidDrafts, profileId],
  );

  const termColumns = useMemo(
    () => [
      nameColumnWithThumb({
        id: "search_term",
        header: "Fraza wyszukiwania",
        primary: (r) => r.search_term,
        secondary: (r) => r.target_text,
        width: "28%",
      }),
      ...metricColumns({ currency }),
    ],
    [currency],
  );

  const placementColumns = useMemo(
    () => [
      {
        accessorKey: "placement",
        header: "Placement",
        meta: { width: "22%" },
        cell: (x: any) => (
          <div className="min-w-0">
            <span className="font-medium">
              {PLACEMENT_LABEL[x.getValue()] ?? x.getValue()}
            </span>
            {x.row.original.no_report && (
              <div className="text-[11px] text-muted">
                modyfikator działa, Amazon nie raportuje wyników osobno
              </div>
            )}
          </div>
        ),
      },
      {
        accessorKey: "modifier",
        header: "Modyfikator",
        meta: { align: "right" },
        cell: (x: any) => {
          const r = x.row.original;
          return (
            <EditableNumber
              value={r.modifier ?? 0}
              draft={modDrafts.get(r.placement)}
              onChange={(v) => setModDrafts((m) => new Map(m).set(r.placement, v))}
              onReset={() =>
                setModDrafts((m) => {
                  const next = new Map(m);
                  next.delete(r.placement);
                  return next;
                })
              }
              format={(v) => (v ? `+${Math.round(v)}%` : "—")}
              min={0}
              max={900}
              disabled={profileId === null}
              disabledHint="Wybierz konkretny rynek, żeby edytować"
            />
          );
        },
      },
      ...metricColumns({ currency }),
      {
        id: "suggestion",
        header: "Sugestia",
        meta: { width: "18%" },
        enableSorting: false,
        cell: (x: any) => <SuggestionCell suggestions={x.row.original.suggestions} />,
      },
    ],
    [currency, modDrafts, profileId],
  );

  const negColumns = useMemo(
    () => [
      nameColumnWithThumb({
        id: "value",
        header: "Wykluczenie",
        // Wykluczenia frazowe maja tekst, produktowe - wyrazenie z ASIN-em.
        primary: (r) => r.keyword_text ?? r.label ?? r.value ?? "",
        width: "34%",
      }),
      {
        id: "kind",
        header: "Rodzaj",
        accessorFn: (r: any) => NEG_KIND[r.match_type ?? r.expression_type ?? ""] ?? r.match_type ?? r.expression_type ?? "",
        cell: (x: any) => <Badge tone="neutral">{x.getValue() || "—"}</Badge>,
      },
      {
        accessorKey: "level",
        header: "Poziom",
        cell: (x: any) => (
          <span className="text-xs text-muted">
            {x.getValue() === "CAMPAIGN" ? "kampania" : "grupa reklam"}
          </span>
        ),
      },
      {
        accessorKey: "ad_group_name",
        header: "Grupa reklam",
        cell: (x: any) => <span className="text-xs text-muted">{x.getValue() ?? "—"}</span>,
      },
    ],
    [],
  );

  function submitBids() {
    const byId = new Map((targets.data ?? []).map((r) => [r.target_id, r]));
    const changes = [...bidDrafts.entries()]
      .map(([id, bid]) => {
        const row = byId.get(id);
        return row ? { id, kind: row.target_kind, bid } : null;
      })
      .filter(Boolean) as Array<{ id: string; kind: string; bid: number }>;
    if (!changes.length) return;
    bulk.preview(() => api.planBids(profileId!, changes));
  }

  function submitModifiers() {
    const rows = placements.data ?? [];
    if (!rows.length || !c) return;
    // Amazon nadpisuje caly zestaw placementBidding, wiec wysylamy komplet -
    // takze te niezmienione, inaczej skasowalyby sie do zera
    const slots = rows.map((r) => ({
      placement: r.placement,
      old: r.modifier ?? 0,
      new: modDrafts.get(r.placement) ?? r.modifier ?? 0,
    }));
    bulk.preview(() =>
      api.planPlacements(profileId!, [
        { campaign_id: id, strategy: c.bidding_strategy, placements: slots },
      ]),
    );
  }

  if (detail.error) return <ErrorState message={detail.error} />;

  const negRows = [...(negKeywords.data ?? []), ...(negTargets.data ?? [])];
  const activeState =
    tab === "adgroups" ? adGroups
    : tab === "products" ? productAds
    : tab === "targeting" ? targets
    : tab === "terms" ? terms
    : tab === "placements" ? placements
    : negKeywords;

  return (
    <div className="space-y-4">
      <Link
        href="/campaigns"
        className="inline-flex items-center gap-1.5 text-xs text-muted hover:text-text"
      >
        <ArrowLeft className="size-3.5" /> Wszystkie kampanie
      </Link>

      {detail.loading && !c ? (
        <Card>
          <Skeleton rows={3} />
        </Card>
      ) : c ? (
        <>
          <div className="rounded-xl border border-border bg-surface px-4 py-3">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <h1 className="text-lg font-semibold">{c.name}</h1>
                <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs">
                  <Badge tone={c.state === "ENABLED" ? "good" : "warn"}>{c.state}</Badge>
                  <Badge tone="neutral">{c.targeting_type}</Badge>
                  {c.portfolio_name && (
                    <Link href={`/campaigns?portfolio=${c.portfolio_id}`}>
                      <Badge tone="accent">
                        <Layers className="mr-1 inline size-3" />
                        {c.portfolio_name}
                      </Badge>
                    </Link>
                  )}
                  <span className="text-muted">{c.country}</span>
                  <span className="text-muted">·</span>
                  <span className="text-muted">
                    budżet {money(c.budget, c.currency ?? currency)} / dzień
                  </span>
                  {c.bidding_strategy && (
                    <>
                      <span className="text-muted">·</span>
                      <span className="text-muted">
                        {c.bidding_strategy.replace("LEGACY_FOR_", "")}
                      </span>
                    </>
                  )}
                </div>
              </div>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3 lg:grid-cols-6">
            <KpiCard label="Wydatek" value={money(c.cost, currency)} />
            <KpiCard label="Sprzedaż" value={money(c.sales, currency)} />
            <KpiCard label="ACOS" value={pct(c.acos)} hint={acosTone(c.acos) ? undefined : undefined} />
            <KpiCard label="ROAS" value={ratio(c.roas)} />
            <KpiCard label="Zamówienia" value={num(c.orders)} />
            <KpiCard
              label="CPC"
              value={money(c.cpc, currency)}
              hint={`CTR ${pct(c.ctr, 2)} · CVR ${pct(c.cvr)}`}
            />
          </div>
        </>
      ) : null}

      <Card
        title={
          <div className="flex flex-wrap gap-1">
            {TABS.map((t) => {
              const Icon = t.icon;
              return (
                <button
                  key={t.key}
                  onClick={() => setTab(t.key)}
                  className={clsx(
                    "flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-sm font-medium transition-colors",
                    tab === t.key
                      ? "bg-accent-soft text-accent"
                      : "text-muted hover:bg-surface-2 hover:text-text",
                  )}
                >
                  <Icon className="size-3.5" />
                  {t.label}
                </button>
              );
            })}
          </div>
        }
        subtitle={`${params.start} → ${params.end}`}
      >
        {activeState.loading && !activeState.data ? (
          <Skeleton rows={10} />
        ) : tab === "adgroups" ? (
          <DataTable
            data={adGroups.data ?? []}
            columns={adGroupColumns}
            getRowId={(r) => r.ad_group_id}
            initialSort={[{ id: "cost", desc: true }]}
            selected={selectedGroups}
            onSelectedChange={setSelectedGroups}
            onRowClick={(r) => {
              // Skok do produktow tej grupy - zamiast drugiego miejsca na to samo
              setTab("products");
              setGrupaDocelowa(r.ad_group_id);
            }}
            emptyLabel="Brak grup reklam"
            emptyHint="Kliknij grupę, żeby zobaczyć jej produkty."
            maxHeight="calc(100vh - 32rem)"
          />
        ) : tab === "products" ? (
          <ProductsTab
            rows={productAds.data ?? []}
            columns={productColumns}
            grupy={adGroups.data ?? []}
            selected={selectedAds}
            onSelectedChange={setSelectedAds}
            grupaDocelowa={grupaDocelowa}
            onGrupaChange={setGrupaDocelowa}
            doDodania={doDodania}
            onDoDodaniaChange={setDoDodania}
            profileId={profileId}
            onPlan={(adGroupId, skus, asins) =>
              bulk.preview(() => api.planAddProducts(profileId!, adGroupId, skus, asins))
            }
          />
        ) : tab === "targeting" ? (
          <DataTable
            data={targets.data ?? []}
            columns={targetColumns}
            getRowId={(r) => r.target_id}
            initialSort={[{ id: "cost", desc: true }]}
            selected={selectedTargets}
            onSelectedChange={setSelectedTargets}
            emptyLabel="Brak targetowania w tym okresie"
            maxHeight="calc(100vh - 32rem)"
          />
        ) : tab === "terms" ? (
          <DataTable
            data={terms.data ?? []}
            columns={termColumns}
            selected={selectedTerms}
            onSelectedChange={setSelectedTerms}
            getRowId={(r) => `${r.target_id}|${r.search_term}`}
            initialSort={[{ id: "cost", desc: true }]}
            emptyLabel="Brak fraz w tym okresie"
            maxHeight="calc(100vh - 32rem)"
          />
        ) : tab === "placements" ? (
          <DataTable
            data={placements.data ?? []}
            columns={placementColumns}
            getRowId={(r) => r.placement}
            initialSort={[{ id: "cost", desc: true }]}
            emptyLabel="Brak danych o placementach"
            maxHeight="calc(100vh - 32rem)"
          />
        ) : (
          <>
          <DataTable
            data={negRows}
            columns={negColumns}
            getRowId={(r: any) => String(r.keyword_id ?? r.target_id)}
            selected={selectedNegs}
            onSelectedChange={setSelectedNegs}
            emptyLabel="Ta kampania nie ma wykluczeń"
            emptyHint="Dodasz je formularzem poniżej albo zaznaczając frazy na zakładce Frazy wyszukiwania."
            maxHeight="calc(100vh - 38rem)"
          />
          <AddNegatives
            campaignId={id}
            grupy={adGroups.data ?? []}
            canPlan={profileId !== null}
            onNegatives={(items, level) =>
              bulk.preview(() => api.planNegatives(profileId!, items, level))
            }
            onNegativeAsins={(items) =>
              bulk.preview(() => api.planNegativeAsins(profileId!, items))
            }
            onNegativeBrands={(brands) =>
              bulk.preview(() => api.planNegativeBrands(profileId!, brands))
            }
            profileId={profileId}
          />
          </>
        )}
      </Card>

      {tab === "targeting" && (
        <DraftBar
          count={bidDrafts.size}
          label={bidDrafts.size === 1 ? "zmieniona stawka" : "zmienionych stawek"}
          onDiscard={() => setBidDrafts(new Map())}
          onSubmit={submitBids}
          busy={bulk.busy}
        />
      )}

      {tab === "placements" && (
        <DraftBar
          count={modDrafts.size}
          label={modDrafts.size === 1 ? "zmieniony modyfikator" : "zmienionych modyfikatorów"}
          onDiscard={() => setModDrafts(new Map())}
          onSubmit={submitModifiers}
          busy={bulk.busy}
        />
      )}

      <BulkBar
        count={
          tab === "adgroups"
            ? selectedGroups.size
            : tab === "products"
              ? selectedAds.size
              : tab === "targeting"
                ? selectedTargets.size
                : tab === "negatives"
                  ? selectedNegs.size
                  : tab === "terms"
                    ? selectedTerms.size
                    : 0
        }
        onClear={() => {
          setSelectedGroups(new Set());
          setSelectedAds(new Set());
          setSelectedTargets(new Set());
          setSelectedNegs(new Set());
          setSelectedTerms(new Set());
        }}
        blockedReason={
          profileId === null ? "Wybierz konkretny rynek, żeby wprowadzać zmiany" : null
        }
        pending={bulk.pending}
        busy={bulk.busy}
        result={bulk.result}
        onConfirm={bulk.confirm}
        onCancel={bulk.cancel}
      >
        {/* Wstrzymanie grupy zatrzymuje wszystko, co w niej siedzi - produkty
            i targetowanie naraz. Wstrzymanie reklamy dotyczy jednego produktu,
            wiec to dwie rozne decyzje i celowo nie dzielimy dla nich przyciskow. */}
        {tab === "adgroups" ? (
          <>
            <Button size="sm" onClick={() => changeGroupState("PAUSED")}>
              Wstrzymaj grupy
            </Button>
            <Button size="sm" onClick={() => changeGroupState("ENABLED")}>
              Wznów grupy
            </Button>
          </>
        ) : tab === "terms" ? (
          <>
            {/* ASIN-y konkurencji ida osobnym zasobem, wiec typ dopasowania
                ich nie dotyczy - rozdzielamy to w excludeTerms. */}
            <Button size="sm" onClick={() => excludeTerms("NEGATIVE_EXACT")}>
              Wyklucz dokładnie
            </Button>
            <Button size="sm" onClick={() => excludeTerms("NEGATIVE_PHRASE")}>
              Wyklucz frazowo
            </Button>
          </>
        ) : tab === "negatives" ? (
          // Amazon nie pozwala zmienic tekstu ani dopasowania wykluczenia -
          // "edycja" to usuniecie i dodanie nowego. Usuniecie jest trwale.
          <Button size="sm" variant="danger" onClick={removeNegatives}>
            Usuń wykluczenia
          </Button>
        ) : tab === "targeting" ? (
          <>
            <Button size="sm" onClick={() => changeTargetState("PAUSED")}>
              Wstrzymaj targetowanie
            </Button>
            <Button size="sm" onClick={() => changeTargetState("ENABLED")}>
              Wznów targetowanie
            </Button>
          </>
        ) : (
          <>
            <Button size="sm" onClick={() => changeAdState("PAUSED")}>
              Wstrzymaj produkty
            </Button>
            <Button size="sm" onClick={() => changeAdState("ENABLED")}>
              Wznów produkty
            </Button>
          </>
        )}
      </BulkBar>
    </div>
  );
}
