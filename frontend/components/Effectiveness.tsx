"use client";

import clsx from "clsx";
import { Hourglass, Info, TrendingDown, TrendingUp } from "lucide-react";
import { Badge, Card, EmptyState, Skeleton } from "@/components/ui";
import type { EffectivenessResult } from "@/lib/api";
import { count, money, pct } from "@/lib/format";

/**
 * Co dzialo sie po wprowadzonych zmianach.
 *
 * Swiadomie nie nazywamy tego skutecznoscia w sensie przyczynowym: rownolegle
 * zmienia sie sezon i konkurencja, a na te sama kampanie potrafi trafic kilka
 * zmian naraz. Panel pokazuje "przed i po", a nie "dzieki temu".
 */

/** Spadek ACOS jest dobry, wzrost zly - odwrotnie niz przy sprzedazy. */
function ton(wartosc: number | null, nizejLepiej: boolean): string {
  if (wartosc === null || Math.abs(wartosc) < 1) return "text-muted";
  const dobrze = nizejLepiej ? wartosc < 0 : wartosc > 0;
  return dobrze ? "text-good" : "text-bad";
}

function Delta({ v, nizejLepiej }: { v: number | null; nizejLepiej: boolean }) {
  if (v === null) return <span className="text-muted">—</span>;
  const Ikona = v > 0 ? TrendingUp : TrendingDown;
  return (
    <span className={clsx("tnum inline-flex items-center gap-0.5", ton(v, nizejLepiej))}>
      <Ikona className="size-3" />
      {v > 0 ? "+" : ""}
      {v}%
    </span>
  );
}

export function Effectiveness({
  data,
  loading,
  currency,
}: {
  data: EffectivenessResult | null | undefined;
  loading: boolean;
  currency: string;
}) {
  if (loading && !data) {
    return (
      <Card title="Co działo się po zmianach">
        <Skeleton rows={4} />
      </Card>
    );
  }

  const gotowe = data?.items ?? [];
  const czekaja = data?.waiting ?? [];
  const najblizsza = czekaja[0];

  return (
    <Card
      title="Co działo się po zmianach"
      subtitle={
        data
          ? `Porównanie ${data.window_days} dni przed zmianą z tym, co po niej. Ocena rusza dopiero ${data.min_days_after} dni po zmianie, bo Amazon dolicza zamówienia przez tydzień.`
          : undefined
      }
      action={
        data?.data_through ? (
          <span className="text-xs text-muted">dane do {data.data_through}</span>
        ) : null
      }
    >
      <div className="space-y-3">
        <div className="flex items-start gap-2 rounded-lg border border-border bg-surface-2 px-3 py-2 text-xs text-muted">
          <Info className="mt-0.5 size-4 shrink-0 text-accent" />
          <p>
            To zestawienie „przed i po”, nie dowód przyczyny — równolegle zmienia się
            sezon, konkurencja i ceny. Jeśli w oknie pomiaru ta sama pozycja dostała
            kolejną zmianę, wynik dotyczy ich obu; takie przypadki są oznaczone i nie
            wchodzą do podsumowania.
          </p>
        </div>

        {data?.summary && data.summary.length > 0 && (
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {data.summary.map((s) => (
              <div key={s.field_label} className="rounded-lg border border-border p-2.5">
                <div className="text-xs font-medium">{s.field_label}</div>
                <div className="mt-0.5 text-[11px] text-muted">
                  {count(s.count, "zmiana", "zmiany", "zmian")} z pełnym okresem
                </div>
                <div className="mt-1.5 flex items-center gap-3 text-xs">
                  <span className="text-muted">
                    ACOS <Delta v={s.median_acos_delta} nizejLepiej />
                  </span>
                  <span className="text-muted">
                    wydatek <Delta v={s.median_cost_delta} nizejLepiej={false} />
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}

        {gotowe.length > 0 ? (
          <ul className="divide-y divide-border">
            {gotowe.map((g) => (
              <li key={g.id} className="flex flex-wrap items-start gap-x-3 gap-y-1 py-2.5">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-sm font-medium">{g.field_label}</span>
                    <span className="tnum text-xs text-muted">
                      {g.old} → {g.new}
                    </span>
                    <span
                      className="truncate text-sm"
                      title={g.entity_name ?? g.entity_id}
                    >
                      {g.entity_name ?? g.entity_id}
                    </span>
                    {g.campaign && <Badge tone="accent">{g.campaign}</Badge>}
                    {g.confounded && (
                      <Badge tone="warn">
                        + {count(g.other_changes, "inna zmiana", "inne zmiany", "innych zmian")} w oknie
                      </Badge>
                    )}
                  </div>
                  <div className="mt-0.5 flex flex-wrap items-center gap-x-3 text-xs text-muted">
                    <span className="tnum">zmiana {g.changed_on}</span>
                    <span className="tnum">
                      wydatek dziennie {money(g.before.daily_cost, currency)} →{" "}
                      {money(g.after.daily_cost, currency)}
                    </span>
                    <span className="tnum">
                      ACOS {pct(g.before.acos)} → {pct(g.after.acos)}
                    </span>
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-3 text-xs">
                  <span>
                    ACOS <Delta v={g.delta.acos} nizejLepiej />
                  </span>
                  <span>
                    sprzedaż <Delta v={g.delta.daily_sales} nizejLepiej={false} />
                  </span>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState
            title="Żadna zmiana nie ma jeszcze pełnego okresu"
            hint={
              najblizsza
                ? `Najwcześniejsza będzie gotowa ${najblizsza.ready_on} — brakuje ${count(najblizsza.days_left, "dnia", "dni", "dni")} danych.`
                : "Nie ma jeszcze wprowadzonych zmian, które dałoby się ocenić."
            }
          />
        )}

        {czekaja.length > 0 && (
          <details className="rounded-lg border border-border">
            <summary className="cursor-pointer list-none px-3 py-2 text-xs text-muted hover:text-text">
              <span className="inline-flex items-center gap-1.5">
                <Hourglass className="size-3.5" />
                {count(data?.waiting_total ?? czekaja.length, "zmiana czeka", "zmiany czekają", "zmian czeka")}{" "}
                na komplet danych
              </span>
            </summary>
            <ul className="divide-y divide-border border-t border-border">
              {czekaja.map((w) => (
                <li
                  key={w.id}
                  className="flex flex-wrap items-center gap-x-3 gap-y-0.5 px-3 py-1.5 text-xs"
                >
                  <span className="font-medium">{w.field_label}</span>
                  <span className="tnum text-muted">
                    {w.old} → {w.new}
                  </span>
                  <span className="truncate" title={w.entity_name ?? w.entity_id}>
                    {w.entity_name ?? w.entity_id}
                  </span>
                  {w.campaign && <span className="text-muted">w {w.campaign}</span>}
                  <span className="tnum ml-auto text-muted">
                    gotowe {w.ready_on}
                  </span>
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </Card>
  );
}
