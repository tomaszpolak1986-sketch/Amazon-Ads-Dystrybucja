"use client";

import { AlertTriangle, ArrowRight, CircleCheck, Clock, ShieldCheck } from "lucide-react";
import Link from "next/link";
import type { HomeStatus as Status } from "@/lib/api";
import { count, money } from "@/lib/format";

/** Ile dni opóźnienia jeszcze uznajemy za normalne. */
const OK_LAG = 1;

function LagBadge({
  lag,
  gapDays,
  gapFrom,
  gapTo,
}: {
  lag: number | null;
  gapDays?: number;
  gapFrom?: string | null;
  gapTo?: string | null;
}) {
  if (lag === null) {
    return <span className="text-muted/70">brak danych</span>;
  }
  // Dziura w srodku okna znaczy tyle samo co obsuwa: liczby sa zanizone.
  // Dotad wystarczylo, ze wczorajszy dzien byl na miejscu, zeby panel pisal
  // "aktualne" - a trzy dni w srodku mialy cztery rynki zamiast siedmiu.
  if (gapDays) {
    const zakres = gapFrom === gapTo ? gapFrom : `${gapFrom} … ${gapTo}`;
    return (
      <span className="text-warn" title={`Braki: ${zakres}`}>
        {count(gapDays, "dzień", "dni", "dni")} z brakami
      </span>
    );
  }
  if (lag <= OK_LAG) {
    return <span className="text-good">aktualne</span>;
  }
  return (
    <span className="text-warn">
      {count(lag, "dzień", "dni", "dni")} opóźnienia
    </span>
  );
}

/**
 * Pasek stanu na gorze strony glownej.
 *
 * Odpowiada na dwa pytania, ktorych reszta ekranu nie zadaje: czy patrze na
 * aktualne liczby i co dzis wymaga mojej decyzji. Bez pierwszego caly dashboard
 * moze pokazywac dane sprzed tygodnia, wygladajac identycznie. Bez drugiego
 * trzeba pamietac, zeby zajrzec do czterech zakladek.
 */
export function HomeStatus({
  status,
  currency,
}: {
  status: Status | null | undefined;
  currency: string;
}) {
  if (!status) return null;

  const { freshness, attention } = status;
  const nieswieze = freshness.sources.some(
    (s) => s.lag_days === null || s.lag_days > OK_LAG || !!s.gap_days,
  );
  const plany = attention.plans;
  const sug = attention.suggestions;
  const jestCoRobic = plany.count > 0 || sug.critical > 0;

  return (
    <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)]">
      {/* Świeżość */}
      <div
        className={`flex items-start gap-2 rounded-xl border px-4 py-3 ${
          nieswieze ? "border-warn/40 bg-warn-soft" : "border-border bg-surface"
        }`}
      >
        {nieswieze ? (
          <Clock className="mt-0.5 size-4 shrink-0 text-warn" />
        ) : (
          <CircleCheck className="mt-0.5 size-4 shrink-0 text-good" />
        )}
        <div className="min-w-0 text-xs">
          <div className="text-sm font-medium">Dane</div>
          <div className="mt-1 space-y-0.5">
            {freshness.sources.map((s) => (
              <div key={s.label} className="flex items-center gap-1.5">
                <span className="text-muted">{s.label}:</span>
                <span className="tnum">{s.through ?? "—"}</span>
                <LagBadge
                  lag={s.lag_days}
                  gapDays={s.gap_days}
                  gapFrom={s.gap_from}
                  gapTo={s.gap_to}
                />
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Co czeka na decyzję */}
      <div
        className={`flex items-start gap-2 rounded-xl border px-4 py-3 ${
          jestCoRobic ? "border-accent/40 bg-accent-soft" : "border-border bg-surface"
        }`}
      >
        <ShieldCheck className="mt-0.5 size-4 shrink-0 text-accent" />
        <div className="min-w-0 flex-1 text-xs">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="text-sm font-medium">Czeka na decyzję</span>
            {plany.count > 0 ? (
              <Link href="/approvals" className="text-accent hover:underline">
                {count(plany.count, "plan", "plany", "planów")} ·{" "}
                {count(plany.items, "zmiana", "zmiany", "zmian")}
                <ArrowRight className="ml-0.5 inline size-3" />
              </Link>
            ) : (
              <span className="text-muted">kolejka pusta</span>
            )}
          </div>

          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1">
            <Link href="/suggestions" className="text-accent hover:underline">
              {count(sug.total, "sugestia", "sugestie", "sugestii")}
              <ArrowRight className="ml-0.5 inline size-3" />
            </Link>
            {sug.critical > 0 && (
              <span className="inline-flex items-center gap-1 text-warn">
                <AlertTriangle className="size-3" />
                {sug.critical} powyżej {money(sug.threshold, currency)}
              </span>
            )}
          </div>

          {/* Rozbicie po portfoliach - przy dwudziestu kilku portfoliach sama
              liczba nie mowi, od czego zaczac. Sortowane po wydatku. */}
          {sug.by_portfolio.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {sug.by_portfolio.slice(0, 5).map((p) => (
                <Link
                  key={p.portfolio_id}
                  href="/suggestions"
                  className="rounded-lg border border-border bg-surface px-2 py-0.5 text-[11px] transition-colors hover:border-accent"
                  title={`${p.count} sugestii obejmujących ${money(p.cost, currency)} wydatku`}
                >
                  <span className="font-medium">{p.portfolio_name}</span>{" "}
                  <span className="tnum text-muted">
                    {p.count} · {money(p.cost, currency)}
                  </span>
                </Link>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
