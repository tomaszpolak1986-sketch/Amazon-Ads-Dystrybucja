"use client";

/* eslint-disable @typescript-eslint/no-explicit-any */

import { useMemo, useState } from "react";
import { DataTable } from "@/components/DataTable";
import { Badge, Button, Card, Skeleton } from "@/components/ui";
import type { GlobalNegativeRow } from "@/lib/api";
import { money, num } from "@/lib/format";

/**
 * Frazy palace pieniadze w kilku kampaniach naraz.
 *
 * Panel proponowal dotad wykluczenie osobno dla kazdej pary kampania+grupa.
 * Fraza palaca sie w pieciu miejscach wymagala wiec pieciu zatwierdzen,
 * a takich fraz jest na koncie blisko trzy tysiace - czyli praca, ktorej
 * nikt nie wykona. Tutaj jedna fraza to jeden wiersz i jedna decyzja;
 * wykluczenie powstaje od razu we wszystkich miejscach, w ktorych wystepuje.
 *
 * Prog jest tu ostrzejszy niz przy zwyklych kandydatach na wykluczenie:
 * zero zamowien, zero sprzedazy i zero sprzedazy innych produktow. Fraza,
 * ktora sprzedaje cokolwiek gdziekolwiek, w ogole sie nie pokazuje - bo
 * decyzja obejmuje cale konto naraz i pomylka kosztuje wiecej niz przy
 * wykluczeniu punktowym.
 */
export function GlobalNegatives({
  data,
  loading,
  currency,
  canPlan,
  onExclude,
}: {
  data?: GlobalNegativeRow[] | null;
  loading: boolean;
  currency: string;
  canPlan: boolean;
  onExclude: (
    items: Array<{ campaignId: string; adGroupId: string; keywordText: string; matchType: string }>,
  ) => void;
}) {
  const [zaznaczone, setZaznaczone] = useState<Set<string>>(new Set());

  // Stala referencja - bez tego kazdy render tworzy nowa tablice i oba
  // ponizsze useMemo licza sie od nowa przy kazdym ruchu myszy.
  const wiersze = useMemo(() => data ?? [], [data]);
  const suma = useMemo(
    () => wiersze.reduce((a, r) => a + (r.cost ?? 0), 0),
    [wiersze],
  );
  const sumaZaznaczonych = useMemo(
    () =>
      wiersze
        .filter((r) => zaznaczone.has(r.search_term))
        .reduce((a, r) => a + (r.cost ?? 0), 0),
    [wiersze, zaznaczone],
  );

  function przelacz(term: string) {
    setZaznaczone((s) => {
      const n = new Set(s);
      if (n.has(term)) n.delete(term);
      else n.add(term);
      return n;
    });
  }

  function wyklucz() {
    const items = wiersze
      .filter((r) => zaznaczone.has(r.search_term))
      .flatMap((r) =>
        r.places.map((m) => ({
          campaignId: m.campaign_id,
          adGroupId: m.ad_group_id,
          keywordText: r.search_term,
          matchType: "NEGATIVE_EXACT",
        })),
      );
    if (items.length) onExclude(items);
  }

  const kolumny = useMemo(
    () => [
      {
        id: "sel",
        header: "",
        meta: { width: "3%" },
        enableSorting: false,
        cell: (c: any) => (
          <input
            type="checkbox"
            checked={zaznaczone.has(c.row.original.search_term)}
            onChange={() => przelacz(c.row.original.search_term)}
            onClick={(e) => e.stopPropagation()}
          />
        ),
      },
      {
        id: "term",
        header: "Fraza",
        accessorFn: (r: any) => r.search_term,
        meta: { width: "42%" },
        cell: (c: any) => (
          <span className="truncate text-xs font-medium">{c.row.original.search_term}</span>
        ),
      },
      {
        id: "places",
        header: "Gdzie się pali",
        accessorFn: (r: any) => r.place_count ?? 0,
        meta: { align: "right" as const, width: "16%" },
        cell: (c: any) => {
          const r = c.row.original as GlobalNegativeRow;
          return (
            <span
              className="cursor-help text-xs"
              title={r.places
                .map((m) => `${m.campaign_name ?? m.campaign_id} - ${m.cost.toFixed(2)}`)
                .join("\n")}
            >
              <Badge tone="warn">{r.campaigns} kampanii</Badge>
            </span>
          );
        },
      },
      {
        id: "clicks",
        header: "Kliknięcia",
        accessorFn: (r: any) => r.clicks ?? 0,
        meta: { align: "right" as const, width: "13%" },
        cell: (c: any) => <span className="tnum">{num(c.row.original.clicks)}</span>,
      },
      {
        id: "cost",
        header: "Wydatek",
        accessorFn: (r: any) => r.cost ?? 0,
        meta: { align: "right" as const, width: "16%" },
        cell: (c: any) => (
          <span className="tnum font-medium">{money(c.row.original.cost, currency)}</span>
        ),
      },
    ],
    [currency, zaznaczone],
  );

  if (loading && !data) return <Card><Skeleton rows={6} /></Card>;
  if (!wiersze.length) return null;

  return (
    <Card
      title="Spala się w kilku kampaniach naraz"
      subtitle={
        `${wiersze.length} fraz bez ani jednego zamówienia, razem ${money(suma, currency)}. ` +
        "Jedno zaznaczenie wyklucza frazę we wszystkich miejscach, w których występuje."
      }
    >
      <div className="flex flex-wrap items-center gap-3 px-4 pb-2 pt-1">
        <Button
          size="sm"
          disabled={!canPlan || zaznaczone.size === 0}
          onClick={wyklucz}
        >
          Wyklucz wszędzie ({zaznaczone.size})
        </Button>
        {zaznaczone.size > 0 && (
          <span className="text-xs text-muted">
            odzyskane: <span className="tnum font-medium">{money(sumaZaznaczonych, currency)}</span> / 30 dni
          </span>
        )}
        <button
          className="text-xs text-accent hover:underline"
          onClick={() =>
            setZaznaczone(
              zaznaczone.size === wiersze.length
                ? new Set()
                : new Set(wiersze.map((r) => r.search_term)),
            )
          }
        >
          {zaznaczone.size === wiersze.length ? "odznacz wszystkie" : "zaznacz wszystkie"}
        </button>
      </div>

      <DataTable
        data={wiersze}
        columns={kolumny}
        getRowId={(r) => r.search_term}
        initialSort={[{ id: "cost", desc: true }]}
        emptyLabel="Brak fraz palących się w kilku kampaniach"
      />

      <p className="px-4 py-3 text-xs leading-relaxed text-muted">
        Pokazujemy tylko frazy z zerem zamówień i zerem sprzedaży - także sprzedaży
        innych produktów z tego samego kliknięcia. Fraza, która sprzedaje cokolwiek
        gdziekolwiek, tu nie trafia. Wykluczenie zakłada się w każdej grupie reklam,
        w której fraza wystąpiła, i jak każda zmiana czeka na Twoje zatwierdzenie.
      </p>
    </Card>
  );
}
