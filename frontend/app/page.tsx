"use client";

import { useMemo } from "react";
import { Anomalies } from "@/components/Anomalies";
import { BulkBar, useBulkAction } from "@/components/BulkBar";
import { TrendChart } from "@/components/TrendChart";
import { HomeStatus } from "@/components/HomeStatus";
import { MonthlyTable } from "@/components/MonthlyTable";
import { Card, ErrorState, KpiCard, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import { acosTone, money, num, pct, ratio } from "@/lib/format";
import { useFilters, useRangeParams } from "@/lib/filters";
import { useData } from "@/lib/useData";

export default function OverviewPage() {
  const params = useRangeParams();
  const { currency, revision, profileId, bump } = useFilters();
  const bulk = useBulkAction(bump);

  const overview = useData(["overview", params, revision], () => api.overview(params));
  // Miesiace nie zaleza od wybranego zakresu dat - to osobne spojrzenie,
  // z wlasnym oknem. Stad brak params w kluczu.
  // Stan konta idzie osobnym zapytaniem: liczy sugestie, wiec trwa dluzej niz
  // KPI i nie ma powodu, zeby wstrzymywal caly ekran.
  const status = useData(["home-status", params, revision], () =>
    api.homeStatus(params),
  );
  const monthly = useData(["monthly", profileId, revision], () =>
    api.monthly(profileId, 6),
  );
  const markets = useData(["markets", params.start, params.end, revision], () =>
    api.markets({ start: params.start, end: params.end }),
  );
  // Anomalie maja wlasne, stale okno - nie zaleza od filtra dat u gory,
  // bo pytanie brzmi "czy cos sie wlasnie zmienilo", a nie "jak bylo w maju".
  const anomalies = useData(["anomalies", profileId, revision], () =>
    api.anomalies(profileId),
  );

  const totals = overview.data?.kpis.current;
  const delta = overview.data?.kpis.delta_pct;

  const placementTotal = useMemo(
    () => overview.data?.placements.reduce((acc, p) => acc + p.cost, 0) ?? 0,
    [overview.data],
  );

  if (overview.error) return <ErrorState message={overview.error} />;

  // TACOS bez sprzedazy calkowitej nie istnieje - Ads API zna tylko sprzedaz
  // z reklam. Zamiast pokazac mylaca liczbe, mowimy wprost, czego brakuje.
  const tacosDays = totals?.tacos_days ?? 0;
  const rangeDays = totals?.range_days ?? 0;
  const tacosHint =
    totals?.tacos == null
      ? "brak danych o sprzedaży całkowitej"
      : tacosDays < rangeDays
        ? `tylko ${tacosDays} z ${rangeDays} dni okresu`
        : `reklamy to ${pct(totals?.ad_share)} przychodu`;

  // TROAS liczy sie z tej samej sprzedazy calkowitej co TACOS, wiec dziedziczy
  // te same ograniczenia. Podpowiedz mowi jednak co innego: nie jaki jest
  // udzial reklamy w obrocie, tylko ile obrotu wraca z jednostki wydatku -
  // bo to jest pytanie, na ktore odpowiada TROAS.
  const troasHint =
    totals?.troas == null
      ? "brak danych o sprzedaży całkowitej"
      : tacosDays < rangeDays
        ? `tylko ${tacosDays} z ${rangeDays} dni okresu`
        : "cały przychód na jednostkę wydatku";

  // Udzial FBA. Liczony ze sprzedazy z reklam, nie z calego obrotu - podpowiedz
  // musi to powiedziec wprost, bo te dwie liczby latwo pomylic, a roznia sie
  // istotnie. Przy sporej sprzedazy bez dopasowanej oferty ostrzegamy, bo
  // wtedy sam procent robi sie niepewny.
  const fba = overview.data?.fba;
  const fbaHint =
    fba?.fba_pct == null
      ? "brak danych — pobierz raport zamówień"
      : fba.source === "ads"
        ? "tylko ze sprzedaży z reklam — pobierz raport zamówień"
        : fba.converted
          ? `${fba.markets} rynk${fba.markets === 1 ? "" : "i"} w ${fba.currency} po kursie dnia`
          : "z całej sprzedaży · reszta to wysyłka własna";

  // Widok zbiorczy sumuje rynki w roznych walutach, wiec kwoty sa przeliczone
  // po kursie z dnia transakcji (EBC). Przy jednym rynku zostaje jego waluta
  // i zadna podpowiedz nie jest potrzebna.
  const przeliczone = profileId === null;
  const hintKwoty = przeliczone ? "po kursie dnia" : undefined;

  return (
    <div className="space-y-4">
      <HomeStatus status={status.data} currency={currency} />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 2xl:grid-cols-8">
        {overview.loading && !totals ? (
          Array.from({ length: 10 }).map((_, i) => (
            <div key={i} className="h-[86px] animate-pulse rounded-xl bg-surface-2" />
          ))
        ) : (
          <>
            <KpiCard
              label="Wydatek"
              value={money(totals?.cost, currency)}
              delta={delta?.cost}
              hint={hintKwoty}
              lowerIsBetter
            />
            <KpiCard hint={hintKwoty} label="Sprzedaż" value={money(totals?.sales, currency)} delta={delta?.sales} />
            <KpiCard
              label="Sprzedaż całkowita"
              value={totals?.total_sales != null ? money(totals.total_sales, currency) : "—"}
              delta={totals?.total_sales != null ? delta?.total_sales : undefined}
              hint={
                totals?.total_sales == null
                  ? "brak danych — wczytaj w zakładce Sprzedaż całkowita"
                  : `reklamy to ${pct(totals?.ad_share)} tej kwoty` +
                    (przeliczone ? " · po kursie dnia" : "")
              }
            />
            <KpiCard label="ACOS" value={pct(totals?.acos)} delta={delta?.acos} lowerIsBetter />
            <KpiCard
              label="TACOS"
              value={totals?.tacos != null ? pct(totals.tacos) : "—"}
              delta={totals?.tacos != null ? delta?.tacos : undefined}
              lowerIsBetter
              hint={tacosHint}
            />
            <KpiCard label="ROAS" value={ratio(totals?.roas)} delta={delta?.roas} />
            {/* TROAS stoi na tej samej parze liczb co TACOS, tylko odwroconej:
                TACOS mowi, ile procent obrotu zjada reklama, TROAS ile obrotu
                z niej wraca. Dlatego dzieli z nim zastrzezenia o pokryciu dni. */}
            <KpiCard
              label="TROAS"
              value={totals?.troas != null ? ratio(totals.troas) : "—"}
              delta={totals?.troas != null ? delta?.troas : undefined}
              hint={troasHint}
            />
            {/* Udzial FBA liczy sie ze sprzedazy REKLAMOWEJ per produkt, bo
                raport sprzedazy calkowitej nie daje rozbicia na produkty w oknie
                dluzszym niz dzien. Podpowiedz mowi to wprost, zeby nikt nie
                wzial tego za udzial FBA w calym obrocie. */}
            <KpiCard
              label="FBA"
              value={fba?.fba_pct != null ? pct(fba.fba_pct) : "—"}
              hint={fbaHint}
            />
            <KpiCard label="Zamówienia" value={num(totals?.orders)} delta={delta?.orders} />
            <KpiCard
              label="CPC"
              value={money(totals?.cpc, currency)}
              delta={delta?.cpc}
              lowerIsBetter
              hint={`CTR ${pct(totals?.ctr, 2)} · CVR ${pct(totals?.cvr)}`}
            />
          </>
        )}
      </div>

      <Card
        title="Wydatek, sprzedaż i ACOS"
        subtitle={
          overview.data
            ? `${overview.data.kpis.range.start} → ${overview.data.kpis.range.end} · porównanie z poprzednimi ${overview.data.kpis.range.days} dniami`
            : undefined
        }
      >
        {overview.loading && !overview.data ? (
          <Skeleton rows={7} />
        ) : (
          <TrendChart data={overview.data?.timeseries ?? []} currency={currency} />
        )}
      </Card>

      <Anomalies
        data={anomalies.data}
        loading={anomalies.loading}
        currency={currency}
        canPlan={profileId !== null}
        onNegatives={(items) =>
          bulk.preview(() => api.planNegatives(profileId!, items))
        }
      />

      <Card
        title="Miesiąc po miesiącu"
        subtitle="Reklama obok całego przychodu — widać, czy sprzedaż rośnie dzięki reklamie, czy mimo niej"
      >
        {monthly.loading && !monthly.data ? (
          <Skeleton rows={5} />
        ) : (
          <MonthlyTable rows={monthly.data ?? []} currency={currency} />
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card
          title="Rynki"
          subtitle="Porównanie profili reklamowych w tym samym okresie"
          action={
            profileId !== null ? (
              <span className="text-[11px] text-muted">zawsze wszystkie rynki</span>
            ) : undefined
          }
        >
          {markets.error ? (
            <ErrorState message={markets.error} />
          ) : markets.loading && !markets.data ? (
            <Skeleton rows={3} />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-xs text-muted">
                    <th className="px-4 py-2 text-left font-semibold">Rynek</th>
                    <th className="px-4 py-2 text-right font-semibold">Wydatek</th>
                    <th className="px-4 py-2 text-right font-semibold">Sprzedaż</th>
                    <th className="px-4 py-2 text-right font-semibold">ACOS</th>
                    <th className="px-4 py-2 text-right font-semibold">ROAS</th>
                  </tr>
                </thead>
                <tbody>
                  {(markets.data ?? []).map((m) => (
                    <tr key={m.profile_id} className="border-b border-border/60 last:border-0">
                      <td className="px-4 py-2">
                        <div className="font-medium">{m.country ?? m.profile_id}</div>
                        <div className="text-[11px] text-muted">{m.account_name}</div>
                      </td>
                      <td className="tnum px-4 py-2 text-right">
                        {money(m.cost, m.currency ?? currency)}
                      </td>
                      <td className="tnum px-4 py-2 text-right">
                        {money(m.sales, m.currency ?? currency)}
                      </td>
                      <td className={`tnum px-4 py-2 text-right font-medium ${acosTone(m.acos)}`}>
                        {pct(m.acos)}
                      </td>
                      <td className="tnum px-4 py-2 text-right">{ratio(m.roas)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card title="Placementy" subtitle="Gdzie wydają się pieniądze">
          {overview.loading && !overview.data ? (
            <Skeleton rows={3} />
          ) : (
            <div className="space-y-3 p-4">
              {(overview.data?.placements ?? []).map((p) => {
                const share = placementTotal ? (p.cost / placementTotal) * 100 : 0;
                return (
                  <div key={p.placement}>
                    <div className="mb-1 flex items-baseline justify-between gap-2 text-xs">
                      <span className="font-medium">
                        {p.placement.replace("PLACEMENT_", "").replaceAll("_", " ")}
                      </span>
                      <span className="tnum text-muted">
                        {money(p.cost, currency)} · {pct(share, 0)} udziału ·{" "}
                        <span className={acosTone(p.acos)}>ACOS {pct(p.acos)}</span>
                      </span>
                    </div>
                    <div className="h-1.5 overflow-hidden rounded-full bg-surface-2">
                      <div
                        className="h-full rounded-full bg-accent"
                        style={{ width: `${Math.min(100, share)}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </Card>
      </div>

      {/* Wykluczenia z rozwinietego alertu i tak przechodza przez plan -
          do Amazona nie idzie nic bez zatwierdzenia tutaj. */}
      <BulkBar
        count={0}
        onClear={() => {}}
        pending={bulk.pending}
        busy={bulk.busy}
        result={bulk.result}
        onConfirm={bulk.confirm}
        onCancel={bulk.cancel}
      >
        {null}
      </BulkBar>
    </div>
  );
}
