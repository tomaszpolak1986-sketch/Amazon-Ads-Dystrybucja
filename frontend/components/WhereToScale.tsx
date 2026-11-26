"use client";

import { Card, Skeleton } from "@/components/ui";
import type { ScaleResult, ScaleRow } from "@/lib/api";
import { money, pct } from "@/lib/format";

const TYP: Record<string, string> = {
  SP: "Sponsored Products",
  SB: "Sponsored Brands",
  SD: "Sponsored Display",
};

/**
 * Gdzie doplacic - odwrotnosc wszystkich pozostalych sugestii.
 *
 * Panel umial dotad wylacznie hamowac: kazdy sygnal odpowiadal na pytanie
 * "gdzie przeplacam". Pytania "gdzie nie doplacam" nie zadawal nikt, choc
 * odpowiedz bywa warta wiecej - rynek z ACOS czterokrotnie lepszym od
 * glownego, ktory dostaje cztery promile budzetu, to nie jest drobiazg.
 *
 * Warunek jest podwojny i oba czlony sa potrzebne: wyraznie lepszy zwrot
 * (ACOS ponizej trzech czwartych sredniej konta) ORAZ maly udzial w wydatku
 * (ponizej dziesieciu procent). Sam dobry ACOS przy duzym budzecie nie jest
 * okazja, tylko opisem glownego kanalu.
 *
 * Swiadomie nie ma tu przycisku. Dolozenie pieniedzy rynkowi nie jest jedna
 * operacja: kampanii dobijajacej budzet trzeba podniesc budzet, a takiej,
 * ktora go nie wyczerpuje - stawki. Panel nie zgadnie, ktora droga jest
 * wlasciwa dla dwudziestu kampanii naraz, a udawanie, ze zgadnie, byloby
 * gorsze niz wskazanie kierunku.
 */
function Wiersz({ r, currency }: { r: ScaleRow; currency: string }) {
  const nazwa = r.kind === "ad_product" ? (TYP[r.name ?? ""] ?? r.name) : r.name;
  return (
    <div className="flex items-center gap-3 px-4 py-2">
      <div className="w-36 shrink-0 truncate text-xs font-medium">{nazwa}</div>
      <div className="tnum w-20 shrink-0 text-right text-xs text-good">{pct(r.acos)}</div>
      <div className="tnum w-24 shrink-0 text-right text-xs text-muted">
        {money(r.cost, currency)}
      </div>
      <div className="min-w-0 flex-1">
        <div className="h-1.5 overflow-hidden rounded-full bg-surface-2">
          <div
            className="h-full rounded-full bg-good"
            style={{ width: `${Math.max(1, Math.min(100, r.share_of_cost))}%` }}
          />
        </div>
      </div>
      <div className="tnum w-14 shrink-0 text-right text-[11px] text-muted">
        {pct(r.share_of_cost, 1)}
      </div>
    </div>
  );
}

export function WhereToScale({
  data,
  loading,
  currency,
}: {
  data?: ScaleResult | null;
  loading: boolean;
  currency: string;
}) {
  if (loading && !data) return <Card><Skeleton rows={4} /></Card>;
  if (!data) return null;

  const warte = [...data.markets, ...data.ad_products].filter((r) => r.worth_scaling);
  if (!warte.length) return null;

  return (
    <Card
      title="Gdzie dołożyć"
      subtitle={
        `Zarabiają wyraźnie lepiej niż średnia konta (${pct(data.acos_ref)}), ` +
        "a dostają ułamek budżetu"
      }
    >
      <div className="flex items-center gap-3 border-b border-border px-4 pb-1.5 text-[11px] text-muted">
        <div className="w-36 shrink-0">Rynek / typ</div>
        <div className="w-20 shrink-0 text-right">ACOS</div>
        <div className="w-24 shrink-0 text-right">Wydatek</div>
        <div className="flex-1">Udział w budżecie</div>
        <div className="w-14 shrink-0 text-right"></div>
      </div>

      <div className="divide-y divide-border">
        {warte.map((r) => (
          <Wiersz key={`${r.kind}-${r.name}`} r={r} currency={currency} />
        ))}
      </div>

      <p className="px-4 py-3 text-xs leading-relaxed text-muted">
        To wskazanie kierunku, nie gotowa zmiana - i dlatego nie ma tu przycisku.
        Kampanii, która dobija budżet, trzeba podnieść budżet; takiej, która go nie
        wyczerpuje - stawki. Panel nie zgadnie, która droga jest właściwa dla
        kilkunastu kampanii naraz, więc pokazuje, gdzie szukać, a decyzję zostawia
        na ekranie kampanii.
      </p>
    </Card>
  );
}
