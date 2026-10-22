"use client";

import clsx from "clsx";
import { Wallet } from "lucide-react";
import { useMemo, useState } from "react";
import { Badge, Button, EmptyState } from "@/components/ui";
import type { BudgetCappedResult } from "@/lib/api";
import { count, money, pct, ratio } from "@/lib/format";

/**
 * Kampanie, ktore domykaja dzienny budzet.
 *
 * Tego nie widac w tabeli kampanii: tam taka kampania wyglada wzorowo - niski
 * ACOS, wysoki ROAS - bo liczy sie tylko to, co zdazyla wydac. Dopiero rozklad
 * po dniach pokazuje, ze wylaczala sie przed koncem dnia.
 */
export function BudgetCapped({
  data,
  currency,
  disabled,
  onPlan,
}: {
  data: BudgetCappedResult;
  currency: string;
  /** Powod, dla ktorego nie da sie teraz nic wyslac - np. widok laczony. */
  disabled?: string | null;
  onPlan: (changes: Array<{ campaign_id: string; budget: number }>) => void;
}) {
  const [wybrane, setWybrane] = useState<Set<string>>(new Set());
  const [kwoty, setKwoty] = useState<Record<string, string>>({});

  // Zmiana rynku albo zakresu uniewaznia zaznaczenie - inaczej dalo by sie
  // wyslac budzet policzony dla kampanii, ktorej juz nie ma na liscie.
  // Kasujemy w trakcie renderu, a nie w efekcie: to dostosowanie stanu do
  // nowych danych, nie synchronizacja z czyms na zewnatrz.
  const klucz = data.items.map((i) => i.campaign_id).join(",");
  const [poprzedniKlucz, setPoprzedniKlucz] = useState(klucz);
  if (poprzedniKlucz !== klucz) {
    setPoprzedniKlucz(klucz);
    setWybrane(new Set());
    setKwoty({});
  }

  const domyslna = (id: string, sugestia: number | null, budzet: number) =>
    kwoty[id] ?? String(sugestia ?? budzet);

  const zmiany = useMemo(
    () =>
      data.items
        .filter((i) => wybrane.has(i.campaign_id))
        .map((i) => ({
          campaign_id: i.campaign_id,
          budget: Number(
            domyslna(i.campaign_id, i.suggested_budget, i.budget).replace(",", "."),
          ),
        }))
        .filter((c) => c.budget > 0),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [data.items, wybrane, kwoty],
  );

  if (!data.items.length) {
    return (
      <EmptyState
        title="Żadna kampania nie dobija do budżetu"
        hint={`Sprawdzone na dniach ${data.window.start} → ${data.window.end}. Dzień liczy się jako domknięty, gdy wydatek sięgnął ${Math.round(data.cap_ratio * 100)}% budżetu.`}
      />
    );
  }

  const doPodniesienia = data.items.filter((i) => i.beats_average).length;

  return (
    <div className="space-y-3">
      <div className="flex items-start gap-2 rounded-lg border border-border bg-surface-2 px-3 py-2 text-xs text-muted">
        <Wallet className="mt-0.5 size-4 shrink-0 text-accent" />
        <p>
          Okno: <span className="tnum">{data.window.start}</span> →{" "}
          <span className="tnum">{data.window.end}</span>. Krótsze niż wybrany zakres
          celowo — Amazon podaje tylko dzisiejszy budżet, więc porównywanie go z
          wydatkiem sprzed miesiąca wprowadzałoby w błąd. Odniesienie to średni ACOS
          aktywnych kampanii z budżetem: <span className="tnum">{pct(data.acos_ref)}</span>.
          {doPodniesienia > 0 && (
            <>
              {" "}
              {count(doPodniesienia, "kampania zarabia", "kampanie zarabiają", "kampanii zarabia")}{" "}
              lepiej niż ta średnia — tylko dla nich proponuję krok w górę.
            </>
          )}
        </p>
      </div>

      <ul className="divide-y divide-border">
        {data.items.map((it) => {
          const zaznaczona = wybrane.has(it.campaign_id);
          return (
            <li key={it.campaign_id} className="py-2.5">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                <input
                  type="checkbox"
                  checked={zaznaczona}
                  disabled={!!disabled}
                  onChange={(e) => {
                    const next = new Set(wybrane);
                    if (e.target.checked) next.add(it.campaign_id);
                    else next.delete(it.campaign_id);
                    setWybrane(next);
                  }}
                  className="size-4 shrink-0 accent-[var(--accent)] disabled:opacity-40"
                />

                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="truncate text-sm font-medium" title={it.name ?? ""}>
                      {it.name ?? it.campaign_id}
                    </span>
                    {it.targeting_type && <Badge>{it.targeting_type}</Badge>}
                    {it.portfolio_name && <Badge tone="accent">{it.portfolio_name}</Badge>}
                    <Badge tone={it.beats_average ? "good" : "warn"}>
                      {it.beats_average ? "lepsza niż średnia" : "słabsza niż średnia"}
                    </Badge>
                  </div>

                  <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-muted">
                    <span className="tnum">
                      Budżet {money(it.budget, currency)}/dzień
                    </span>
                    <span className="tnum">
                      Średnio wydaje {money(it.daily_cost, currency)}
                    </span>
                    <span className="tnum">ACOS {pct(it.acos)}</span>
                    <span className="tnum">ROAS {ratio(it.roas)}</span>
                  </div>

                  {/* Pasek mowi wiecej niz liczba: widac, czy to codziennosc,
                      czy kilka pojedynczych dni. */}
                  <div className="mt-1.5 flex items-center gap-2">
                    <div
                      className="flex h-1.5 flex-1 overflow-hidden rounded-full bg-surface-2"
                      title={`${it.days_capped} z ${it.days} dni na limicie`}
                    >
                      <div
                        className={clsx(
                          "h-full rounded-full",
                          it.beats_average ? "bg-good" : "bg-warn",
                        )}
                        style={{ width: `${it.capped_share}%` }}
                      />
                    </div>
                    <span className="tnum shrink-0 text-[11px] text-muted">
                      {it.days_capped}/{it.days} dni na limicie
                    </span>
                  </div>
                </div>

                <div className="flex shrink-0 items-center gap-1.5">
                  <span className="text-xs text-muted">Nowy budżet</span>
                  <input
                    type="text"
                    inputMode="decimal"
                    value={domyslna(it.campaign_id, it.suggested_budget, it.budget)}
                    disabled={!!disabled}
                    onChange={(e) =>
                      setKwoty({ ...kwoty, [it.campaign_id]: e.target.value })
                    }
                    className="tnum w-20 rounded-lg border border-border bg-bg px-2 py-1 text-right text-sm outline-none focus:border-accent disabled:opacity-40"
                  />
                </div>
              </div>
            </li>
          );
        })}
      </ul>

      <div className="flex items-center justify-between gap-3 border-t border-border pt-3">
        <p className="text-xs text-muted">
          {disabled
            ? disabled
            : zmiany.length
              ? `Zaznaczono ${count(zmiany.length, "kampanię", "kampanie", "kampanii")} — trafi do zatwierdzenia, nie do Amazona.`
              : "Zaznacz kampanie, których budżet chcesz zmienić."}
        </p>
        <Button
          size="sm"
          disabled={!!disabled || !zmiany.length}
          onClick={() => onPlan(zmiany)}
        >
          Wyślij do zatwierdzenia
        </Button>
      </div>
    </div>
  );
}
