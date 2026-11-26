"use client";

import Link from "next/link";
import { Badge, Card, Skeleton } from "@/components/ui";
import type { TodayResult } from "@/lib/api";
import { money, pct } from "@/lib/format";

const ZAKRES: Record<string, string> = {
  CAMPAIGN: "Kampania",
  TARGET: "Target",
  SEARCH_TERM: "Fraza",
  PLACEMENT: "Placement",
  PRODUCT: "Produkt",
};

const TON: Record<string, "bad" | "warn" | "good" | "neutral"> = {
  bad: "bad",
  warn: "warn",
  good: "good",
  info: "neutral",
};

/**
 * Kilka rzeczy na dzis, zamiast calej listy sugestii.
 *
 * Na tym koncie sugestii jest ponad piecset. To nie jest lista do przerobienia,
 * tylko archiwum - nikt nie zaczyna dnia od pytania "ktora z 557". Pulpit
 * liczyl wszystko znakomicie i nie mowil, od czego zaczac.
 *
 * Kolejnosc jest dwustopniowa i to jest rozstrzygniecie merytoryczne, nie
 * kosmetyka. NAJPIERW poziom pilnosci (straty przed okazjami), a dopiero
 * w jego obrebie waga pieniezna. Wspolny ranking po samych pieniadzach
 * wypchnalby okazje nad straty, bo zapasu na wzrost jest z natury wiecej
 * niz strat - a "mozesz dolozyc 100 EUR" i "tracisz 100 EUR" to nie sa te
 * same sto euro.
 *
 * Nie ma tu przyciskow zatwierdzania i to jest decyzja, nie brak czasu:
 * kazdy typ sugestii ma inna tresc do zatwierdzenia (stawka do poprawienia,
 * typ dopasowania wykluczenia, grupa docelowa przejmowanej frazy), a te
 * wybory naleza do ekranu Sugestii. Tutaj jest odpowiedz na "czym sie zajac",
 * nie druga kopia tamtego ekranu.
 */
export function Today({
  data,
  loading,
  currency,
}: {
  data?: TodayResult | null;
  loading: boolean;
  currency: string;
}) {
  if (loading && !data) return <Card><Skeleton rows={5} /></Card>;
  if (!data || !data.items.length) return null;

  const reszta = data.total - data.items.length;

  return (
    <Card
      title="Czym zająć się dzisiaj"
      subtitle={
        `${data.items.length} z ${data.total} - najpierw straty, w każdej grupie po pieniądzach. ` +
        `W całej liście stoi ${money(data.total_impact, currency)} na 30 dni.`
      }
    >
      <div className="divide-y divide-border">
        {data.items.map((r, i) => (
          <div key={`${r.scope}-${r.entity_id}-${r.code}-${i}`} className="flex items-start gap-3 px-4 py-2.5">
            <div className="tnum w-20 shrink-0 text-right text-sm font-semibold">
              {money(r.impact, r.currency ?? currency)}
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-1.5">
                <Badge tone={TON[r.level] ?? "neutral"}>{r.title}</Badge>
                <span className="text-xs text-muted">{ZAKRES[r.scope] ?? r.scope}</span>
                <span className="truncate text-xs font-medium" title={r.entity_name ?? ""}>
                  {r.entity_name}
                </span>
              </div>
              <div className="mt-0.5 line-clamp-1 text-[11px] text-muted" title={r.detail}>
                {r.campaign_name ? `${r.campaign_name} · ` : ""}
                ACOS {pct(r.acos)} · wydatek {money(r.cost, r.currency ?? currency)}
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="flex items-center justify-between gap-3 px-4 py-3 text-xs">
        <span className="text-muted">
          {reszta > 0
            ? `Pozostałe ${reszta} czeka - wrócą tutaj, gdy te znikną.`
            : "To cała lista."}
        </span>
        <Link href="/suggestions" className="font-medium text-accent hover:underline">
          Otwórz Sugestie
        </Link>
      </div>
    </Card>
  );
}
