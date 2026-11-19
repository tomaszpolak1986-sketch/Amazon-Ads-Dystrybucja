"use client";

/* eslint-disable @typescript-eslint/no-explicit-any */

import { useMemo, useState } from "react";
import { DataTable } from "@/components/DataTable";
import { Badge, Card, Skeleton } from "@/components/ui";
import type { OffAmazonResult } from "@/lib/api";
import { acosTone, money, num, pct } from "@/lib/format";

/**
 * Emisja poza witrynami Amazona.
 *
 * Osobna sekcja, a nie czwarty kafelek obok pozostałych placementów, bo to
 * nie jest placement w tym samym sensie. Trzech pozostałych można sterować
 * mnożnikiem stawki; tego nie - Amazon odrzuca taki zapis, a emisję włącza
 * się przełącznikiem na poziomie kampanii.
 *
 * Ten widok istnieje przede wszystkim dlatego, że samego USTAWIENIA nie da
 * się odczytać: `offAmazonSettings` wraca z API puste dla każdej kampanii.
 * Raport jest więc jedynym świadkiem - kampania z kliknięciami ma emisję
 * włączoną, a kampania bez nich może mieć ją włączoną i nie dostawać ruchu.
 * Dlatego wiersze bez kliknięć są domyślnie schowane: nie niosą informacji,
 * a przy stu kampaniach zalewają tabelę.
 *
 * Porównanie z wynikiem NA Amazonie jest tu sednem, nie ozdobą. Sam ACOS
 * poza Amazonem niczego nie rozstrzyga: 4,6% brzmi znakomicie, dopóki nie
 * widać, że ta sama kampania robi na Amazonie 2,5%.
 */
const WERDYKT: Record<string, { label: string; tone: "good" | "bad" | "warn" | "neutral" }> = {
  "lepiej niz na Amazonie": { label: "lepiej niż na Amazonie", tone: "good" },
  "gorzej niz na Amazonie": { label: "gorzej niż na Amazonie", tone: "warn" },
  "placi, nie sprzedaje": { label: "płaci, nie sprzedaje", tone: "bad" },
  sprzedaje: { label: "sprzedaje", tone: "neutral" },
  "bez ruchu": { label: "bez ruchu", tone: "neutral" },
};

export function OffAmazon({
  data,
  loading,
  currency,
}: {
  data?: OffAmazonResult | null;
  loading: boolean;
  currency: string;
}) {
  const [zRuchem, setZRuchem] = useState(true);

  const wiersze = useMemo(() => {
    const w = data?.items ?? [];
    return zRuchem ? w.filter((r) => (r.clicks ?? 0) > 0) : w;
  }, [data, zRuchem]);

  const kolumny = useMemo(
    () => [
      {
        id: "campaign",
        header: "Kampania",
        accessorFn: (r: any) => r.campaign_name ?? "",
        meta: { width: "30%" },
        cell: (c: any) => (
          <div className="min-w-0">
            <div className="truncate text-xs font-medium">{c.row.original.campaign_name}</div>
            <div className="mt-0.5 text-[11px] text-muted">{c.row.original.country}</div>
          </div>
        ),
      },
      {
        id: "verdict",
        header: "Ocena",
        accessorFn: (r: any) => r.verdict ?? "",
        meta: { width: "16%" },
        enableSorting: false,
        cell: (c: any) => {
          const w = WERDYKT[c.row.original.verdict] ?? {
            label: c.row.original.verdict,
            tone: "neutral" as const,
          };
          return <Badge tone={w.tone}>{w.label}</Badge>;
        },
      },
      {
        id: "clicks",
        header: "Kliknięcia",
        accessorFn: (r: any) => r.clicks ?? 0,
        meta: { align: "right" as const, width: "10%" },
        cell: (c: any) => <span className="tnum">{num(c.row.original.clicks)}</span>,
      },
      {
        id: "cost",
        header: "Wydatek",
        accessorFn: (r: any) => r.cost ?? 0,
        meta: { align: "right" as const, width: "11%" },
        cell: (c: any) => (
          <span className="tnum">{money(c.row.original.cost, currency)}</span>
        ),
      },
      {
        id: "sales",
        header: "Sprzedaż",
        accessorFn: (r: any) => r.sales ?? 0,
        meta: { align: "right" as const, width: "11%" },
        cell: (c: any) => (
          <span className="tnum">{money(c.row.original.sales, currency)}</span>
        ),
      },
      {
        id: "acos",
        header: "ACOS poza",
        accessorFn: (r: any) => r.acos ?? 0,
        meta: { align: "right" as const, width: "11%" },
        cell: (c: any) => (
          <span className={`tnum ${acosTone(c.row.original.acos)}`}>
            {pct(c.row.original.acos)}
          </span>
        ),
      },
      {
        id: "acos_on",
        header: "ACOS na Amazonie",
        accessorFn: (r: any) => r.acos_on_amazon ?? 0,
        meta: { align: "right" as const, width: "11%" },
        cell: (c: any) => (
          <span className="tnum text-muted">{pct(c.row.original.acos_on_amazon)}</span>
        ),
      },
    ],
    [currency],
  );

  if (loading && !data) return <Card><Skeleton rows={6} /></Card>;
  if (!data || !data.campaigns) return null;

  const bezRuchu = (data.items ?? []).filter((r) => (r.clicks ?? 0) === 0).length;

  return (
    <Card
      title="Poza Amazonem"
      subtitle={
        `${data.campaigns} kampanii z taką emisją · dane od ${data.since ?? "-"} · ` +
        `${pct(data.share_of_cost, 2)} całego wydatku`
      }
    >
      <div className="flex flex-wrap items-end gap-6 px-4 pt-1">
        <div>
          <div className="tnum text-2xl font-semibold">{money(data.cost, currency)}</div>
          <div className="mt-0.5 text-xs text-muted">wydatek poza Amazonem</div>
        </div>
        <div>
          <div className="tnum text-2xl font-semibold">{money(data.sales, currency)}</div>
          <div className="mt-0.5 text-xs text-muted">sprzedaż stamtąd</div>
        </div>
        <div>
          <div className={`tnum text-2xl font-semibold ${acosTone(data.acos)}`}>
            {pct(data.acos)}
          </div>
          <div className="mt-0.5 text-xs text-muted">
            ACOS poza · na Amazonie {pct(data.acos_on_amazon)}
          </div>
        </div>
      </div>

      <div className="flex items-center gap-2 px-4 pt-3 pb-1 text-xs">
        <button
          onClick={() => setZRuchem(true)}
          className={`rounded-md px-2 py-1 ${zRuchem ? "bg-surface-2 font-medium" : "text-muted"}`}
        >
          Z ruchem ({(data.items ?? []).length - bezRuchu})
        </button>
        <button
          onClick={() => setZRuchem(false)}
          className={`rounded-md px-2 py-1 ${!zRuchem ? "bg-surface-2 font-medium" : "text-muted"}`}
        >
          Wszystkie ({(data.items ?? []).length})
        </button>
      </div>

      <DataTable
        data={wiersze}
        columns={kolumny}
        getRowId={(r) => String(r.campaign_id)}
        initialSort={[{ id: "cost", desc: true }]}
        emptyLabel="Brak emisji poza Amazonem w tym okresie"
      />

      <p className="px-4 py-3 text-xs leading-relaxed text-muted">
        Amazon nie zwraca tego ustawienia przy odczycie, więc panel nie wie, które
        kampanie mają je włączone - wie tylko, w których pojawił się ruch. Kampania
        bez kliknięć może mieć emisję włączoną i po prostu nie dostawać wyświetleń.
        Sam przełącznik ustawisz na ekranie kampanii; jak każda zmiana, przez kolejkę
        zatwierdzeń. Kolumna obok pokazuje ACOS tej samej kampanii na Amazonie -
        bez tego porównania niska wartość poza Amazonem potrafi mylić.
      </p>
    </Card>
  );
}
