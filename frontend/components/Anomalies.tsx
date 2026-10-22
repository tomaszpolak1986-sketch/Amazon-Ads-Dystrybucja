"use client";

import clsx from "clsx";
import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  ChevronDown,
  ChevronRight,
} from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Badge, Button, Card, EmptyState, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import type { AnomaliesResult, Anomaly } from "@/lib/api";
import { useData } from "@/lib/useData";
import { count, money, num, pct } from "@/lib/format";

/**
 * Nagle zmiany z ostatnich dni.
 *
 * Reszta dashboardu patrzy na sumy za okres, a suma z definicji wygladza to,
 * co zdarzylo sie wczoraj. Ten kafel odpowiada na inne pytanie: czy cos
 * wlasnie sie zmienilo.
 */

/** Metryki wyrazone w pieniadzu formatujemy inaczej niz liczniki. */
function wartosc(a: Anomaly, v: number, currency: string): string {
  if (a.metric === "cost" || a.metric === "cpc") {
    return money(v, currency);
  }
  if (a.metric === "sales") return money(v, currency);
  if (a.metric === "cvr") return pct(v);
  return num(Math.round(v));
}

/**
 * Rozwiniecie alertu: ktore frazy napedzily zmiane.
 *
 * Sam alert mowi "wydatek skoczyl". W kampaniach automatycznych przyczyna jest
 * prawie zawsze ta sama - Amazon rozszerzyl dopasowanie - ale zeby to
 * potwierdzic, trzeba bylo do tej pory wejsc do panelu Amazona.
 *
 * Komponent montuje sie dopiero po rozwinieciu, wiec zapytanie idzie na
 * zadanie, a nie dla kazdego alertu z listy.
 */
function Winowajcy({
  campaignId,
  currency,
  canPlan,
  onNegatives,
}: {
  campaignId: string;
  currency: string;
  canPlan: boolean;
  onNegatives: (
    items: Array<{ campaignId: string; adGroupId?: string; keywordText: string; matchType: string }>,
  ) => void;
}) {
  const { data, loading, error } = useData(["anomaly-contributors", campaignId], () =>
    api.anomalyContributors(campaignId),
  );
  const [wybrane, setWybrane] = useState<Set<string>>(new Set());

  if (loading && !data) return <Skeleton rows={3} />;
  if (error) return <p className="py-2 text-xs text-bad">{error}</p>;
  if (!data?.items.length) {
    return (
      <p className="py-2 text-xs text-muted">
        Żadna pojedyncza fraza nie tłumaczy tej zmiany — rozłożyła się równomiernie.
      </p>
    );
  }

  const bezSprzedazy = data.items.filter((w) => !w.orders);
  const udzial = data.daily_increase
    ? Math.round((data.daily_increase_no_sales / data.daily_increase) * 100)
    : 0;

  function przelacz(fraza: string) {
    const next = new Set(wybrane);
    if (next.has(fraza)) next.delete(fraza);
    else next.add(fraza);
    setWybrane(next);
  }

  function wyslij(matchType: string) {
    const items = data!.items
      .filter((w) => wybrane.has(w.search_term))
      .map((w) => ({
        campaignId,
        adGroupId: w.ad_group_id ?? undefined,
        keywordText: w.search_term,
        matchType,
      }));
    if (items.length) onNegatives(items);
  }

  return (
    <div className="mt-2 rounded-lg border border-border bg-surface-2 p-2.5">
      <p className="text-xs text-muted">
        Wydatek na rosnących frazach zwiększył się o{" "}
        <span className="tnum font-medium text-text">
          {money(data.daily_increase, currency)}
        </span>{" "}
        dziennie. Z tego{" "}
        <span className={clsx("tnum font-medium", udzial >= 50 ? "text-bad" : "text-warn")}>
          {money(data.daily_increase_no_sales, currency)} ({udzial}%)
        </span>{" "}
        poszło na frazy bez ani jednego zamówienia.{" "}
        {data.total_terms > data.items.length &&
          `Pokazane ${data.items.length} z ${data.total_terms}.`}
      </p>

      <ul className="mt-2 space-y-0.5">
        {data.items.map((w) => (
          <li
            key={w.search_term}
            className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs"
          >
            <input
              type="checkbox"
              checked={wybrane.has(w.search_term)}
              onChange={() => przelacz(w.search_term)}
              disabled={!canPlan}
              aria-label={`Zaznacz frazę ${w.search_term}`}
              className="size-3.5 shrink-0 accent-[var(--accent)] disabled:opacity-40"
            />
            <span className="min-w-0 flex-1 truncate" title={w.search_term}>
              {w.search_term}
            </span>
            {w.is_new && <Badge tone="warn">nowa</Badge>}
            <span className="tnum text-muted">
              {money(w.daily_before, currency)} → {money(w.daily_now, currency)}/d
            </span>
            <span
              className={clsx("tnum w-24 text-right", w.orders ? "text-muted" : "text-bad")}
            >
              {w.orders ? `${count(w.orders, "zamówienie", "zamówienia", "zamówień")}` : "0 zamówień"}
            </span>
          </li>
        ))}
      </ul>

      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 border-t border-border pt-2">
        <span className="text-[11px] text-muted">
          {!canPlan
            ? "Wybierz konkretny rynek, żeby wykluczać frazy"
            : wybrane.size
              ? `Zaznaczono ${count(wybrane.size, "frazę", "frazy", "fraz")} — trafi do zatwierdzenia`
              : `${count(bezSprzedazy.length, "fraza nie sprzedała", "frazy nie sprzedały", "fraz nie sprzedało")} nic`}
        </span>
        <div className="flex gap-1.5">
          <Button
            size="sm"
            disabled={!canPlan || !wybrane.size}
            onClick={() => wyslij("NEGATIVE_EXACT")}
          >
            Wyklucz dokładne
          </Button>
          <Button
            size="sm"
            disabled={!canPlan || !wybrane.size}
            onClick={() => wyslij("NEGATIVE_PHRASE")}
          >
            Wyklucz frazowe
          </Button>
        </div>
      </div>
    </div>
  );
}

export function Anomalies({
  data,
  loading,
  currency,
  canPlan,
  onNegatives,
}: {
  data: AnomaliesResult | null | undefined;
  loading: boolean;
  currency: string;
  /** Czy da sie teraz cokolwiek wyslac - widok laczony nie ma jednego rynku. */
  canPlan: boolean;
  onNegatives: (
    items: Array<{ campaignId: string; adGroupId?: string; keywordText: string; matchType: string }>,
  ) => void;
}) {
  const okno = data?.window;
  const [rozwiniete, setRozwiniete] = useState<string | null>(null);

  return (
    <Card
      title={
        <span className="flex items-center gap-1.5">
          <Activity className="size-4 text-accent" />
          Nagłe zmiany
        </span>
      }
      subtitle={
        okno
          ? `Ostatnie ${okno.recent_days} dni wobec ${okno.baseline_days} dni normy każdej kampanii z osobna. Sygnały o sprzedaży liczone z ${okno.attribution_lag_days}-dniowym opóźnieniem, bo Amazon dolicza zamówienia jeszcze przez tydzień.`
          : undefined
      }
      action={
        data && data.total > data.items.length ? (
          <span className="text-xs text-muted">
            pokazane {data.items.length} z {data.total}
          </span>
        ) : null
      }
    >
      {loading && !data ? (
        <Skeleton rows={4} />
      ) : !data?.items.length ? (
        <EmptyState
          title="Nic nie odstaje od normy"
          hint="Żadna kampania nie zmieniła zachowania na tyle, żeby wyjść poza swój zwykły rozrzut."
        />
      ) : (
        <ul className="divide-y divide-border">
          {data.items.map((a) => {
            const wzrost = a.change_pct > 0;
            const Strzalka = wzrost ? ArrowUpRight : ArrowDownRight;
            const otwarty = rozwiniete === `${a.campaign_id}-${a.code}`;
            return (
              <li
                key={`${a.campaign_id}-${a.code}`}
                className="flex flex-wrap items-start gap-x-3 gap-y-1.5 py-2.5"
              >
                <div
                  className={clsx(
                    "mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg",
                    a.level === "bad" ? "bg-bad-soft text-bad" : "bg-warn-soft text-warn",
                  )}
                >
                  <Strzalka className="size-4" />
                </div>

                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-sm font-medium">{a.title}</span>
                    <Link
                      href={`/campaigns/${a.campaign_id}`}
                      className="truncate text-sm text-accent hover:underline"
                      title={a.campaign_name ?? ""}
                    >
                      {a.campaign_name ?? a.campaign_id}
                    </Link>
                    {a.portfolio_name && <Badge tone="accent">{a.portfolio_name}</Badge>}
                  </div>

                  <p className="mt-0.5 text-xs text-muted">{a.detail}</p>

                  <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs">
                    <span className="tnum text-muted">
                      zwykle {wartosc(a, a.baseline, currency)} → teraz{" "}
                      <span className="font-medium text-text">
                        {wartosc(a, a.current, currency)}
                      </span>{" "}
                      dziennie
                    </span>
                    <span
                      className={clsx(
                        "tnum font-medium",
                        a.level === "bad" ? "text-bad" : "text-warn",
                      )}
                    >
                      {a.change_pct > 0 ? "+" : ""}
                      {a.change_pct}%
                    </span>
                    <span className="tnum text-muted">
                      {a.window.from} → {a.window.to}
                    </span>
                  </div>

                  {/* Ile to kosztowalo i co przynioslo w tych samych dniach -
                      zeby nie trzeba bylo szukac gdzie indziej. */}
                  <div className="mt-0.5 flex flex-wrap items-center gap-x-3 text-[11px] text-muted">
                    <span className="tnum">
                      w tych dniach: {money(a.recent_cost, currency)} kosztu,{" "}
                      {money(a.recent_sales, currency)} sprzedaży
                      {a.recent_acos !== null && ` — ACOS ${pct(a.recent_acos)}`}
                    </span>
                    {a.sales_incomplete && (
                      <span className="text-muted/70">
                        sprzedaż jeszcze niepełna (atrybucja trwa 7 dni)
                      </span>
                    )}
                  </div>

                  {/* Rozwiniecie tylko dla zmian wydatku - przy spadku
                      konwersji przyczyna lezy poza reklama i lista fraz
                      niczego by nie wyjasnila. */}
                  {(a.metric === "cost" || a.metric === "cpc") && (
                    <>
                      <button
                        onClick={() =>
                          setRozwiniete(otwarty ? null : `${a.campaign_id}-${a.code}`)
                        }
                        className="mt-1 inline-flex items-center gap-1 text-xs text-accent hover:underline"
                      >
                        {otwarty ? (
                          <ChevronDown className="size-3.5" />
                        ) : (
                          <ChevronRight className="size-3.5" />
                        )}
                        {otwarty ? "Zwiń" : "Przez co?"}
                      </button>
                      {otwarty && (
                        <Winowajcy
                          campaignId={a.campaign_id}
                          currency={currency}
                          canPlan={canPlan}
                          onNegatives={onNegatives}
                        />
                      )}
                    </>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
