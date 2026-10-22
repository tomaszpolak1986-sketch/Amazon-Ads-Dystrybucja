"use client";

import type { MonthlyRow } from "@/lib/api";
import { acosTone, money, pct } from "@/lib/format";
import { Badge } from "./ui";

const MIESIACE = [
  "styczeń", "luty", "marzec", "kwiecień", "maj", "czerwiec",
  "lipiec", "sierpień", "wrzesień", "październik", "listopad", "grudzień",
];

function nazwaMiesiaca(klucz: string): string {
  const [rok, m] = klucz.split("-");
  return `${MIESIACE[Number(m) - 1]} ${rok}`;
}

/**
 * Wyniki miesiac po miesiacu.
 *
 * Miesiac niepelny jest oznaczony, a nie ukryty: biezacy miesiac zawsze jest
 * krotszy od poprzedniego i porownanie sum wprowadzaloby w blad. Zamiast
 * chowac go albo skalowac (co udaje wiedze, ktorej nie mamy), pokazujemy
 * liczbe dni obok - decyzje o porownywalnosci zostawiamy czytelnikowi.
 */
export function MonthlyTable({
  rows,
  currency,
}: {
  rows: MonthlyRow[];
  currency: string;
}) {
  if (!rows.length) {
    return (
      <p className="px-4 py-8 text-center text-xs text-muted">
        Za mało danych, żeby porównać miesiące.
      </p>
    );
  }

  const najwiekszaSprzedaz = Math.max(
    ...rows.map((r) => r.total_sales ?? r.ad_sales ?? 0),
    1,
  );

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[42rem] text-sm">
        <thead className="bg-surface-2 text-xs text-muted">
          <tr>
            <th className="px-4 py-2 text-left font-semibold">Miesiąc</th>
            <th className="px-4 py-2 text-right font-semibold">Wydatek</th>
            <th className="px-4 py-2 text-right font-semibold">Sprzedaż z reklam</th>
            <th className="px-4 py-2 text-right font-semibold">Sprzedaż całkowita</th>
            <th className="px-4 py-2 text-right font-semibold">ACOS</th>
            <th className="px-4 py-2 text-right font-semibold">TACOS</th>
            <th className="px-4 py-2 text-right font-semibold">Udział reklam</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const szerokosc = ((r.total_sales ?? r.ad_sales ?? 0) / najwiekszaSprzedaz) * 100;
            return (
              <tr key={r.month} className="border-t border-border/60">
                <td className="px-4 py-2">
                  <div className="flex items-center gap-1.5">
                    <span className="font-medium">{nazwaMiesiaca(r.month)}</span>
                    {!r.complete && (
                      <Badge tone="warn">{r.days} z {r.days_in_month} dni</Badge>
                    )}
                  </div>
                  {/* Pasek dlugosci sprzedazy - kierunek widac szybciej niz z cyfr */}
                  <div className="mt-1 h-1 w-full max-w-[10rem] overflow-hidden rounded-full bg-surface-2">
                    <div
                      className="h-full rounded-full bg-accent"
                      style={{ width: `${Math.max(2, szerokosc)}%` }}
                    />
                  </div>
                </td>
                <td className="tnum px-4 py-2 text-right">{money(r.cost, currency)}</td>
                <td className="tnum px-4 py-2 text-right">{money(r.ad_sales, currency)}</td>
                <td className="tnum px-4 py-2 text-right font-medium">
                  {r.total_sales != null ? money(r.total_sales, currency) : (
                    <span className="text-muted/60">—</span>
                  )}
                </td>
                <td className={`tnum px-4 py-2 text-right font-medium ${acosTone(r.acos)}`}>
                  {pct(r.acos)}
                </td>
                <td className="tnum px-4 py-2 text-right">
                  {r.tacos != null ? (
                    <span className="font-medium">{pct(r.tacos)}</span>
                  ) : (
                    <span className="text-muted/60">—</span>
                  )}
                  {r.tacos != null && !r.total_complete && (
                    <div
                      className="text-[10px] text-warn"
                      title="Sprzedaż całkowita nie pokrywa całego miesiąca — TACOS policzony tylko z dni, które mają oba komplety danych"
                    >
                      z {r.days_with_total} dni
                    </div>
                  )}
                </td>
                <td className="tnum px-4 py-2 text-right text-muted">
                  {r.ad_share != null ? pct(r.ad_share, 0) : "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
