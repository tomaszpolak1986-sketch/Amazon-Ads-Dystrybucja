"use client";

import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";
import type { OverlapPart } from "@/lib/api";
import { acosTone, money, num, pct } from "@/lib/format";
import { Badge } from "./ui";

const TARGETING_LABEL: Record<string, string> = {
  AUTO: "auto",
  MANUAL: "ręczna",
};

export interface OverlapItem {
  key: string;
  /** Naglowek wiersza - fraza albo SKU. */
  title: string;
  /** Drobny podpis pod naglowkiem, np. ASIN. */
  subtitle?: string | null;
  /** Miniatura produktu; przy frazach pusta. */
  image?: string | null;
  /** Pelna nazwa produktu z katalogu Amazona. */
  productName?: string | null;
  campaign_count: number;
  campaign_count_all: number;
  clicks: number;
  cost: number;
  orders: number;
  sales: number;
  acos: number | null;
  /** Miara rozproszenia pokazywana po prawej - zalezna od rodzaju listy. */
  spreadLabel: string;
  spreadValue: string;
  spreadTone?: string;
  breakdown: OverlapPart[];
}

/**
 * Lista pozycji dzielonych miedzy kampanie, z rozwijanym rozbiciem.
 *
 * Wiersz zwiniety odpowiada na pytanie "czy to w ogole problem", rozwiniety na
 * "ktora kampania jest lepsza". Kampanie bez realnego udzialu w ruchu sa
 * przygaszone, a nie ukryte: sam fakt, ze fraza otarla sie o dziesiec kampanii,
 * bywa wart zauwazenia, ale nie moze przeslaniac tych dwoch, ktore licza sie
 * naprawde.
 */
export function OverlapList({
  items,
  currency,
  emptyLabel,
}: {
  items: OverlapItem[];
  currency: string;
  emptyLabel: string;
}) {
  const [open, setOpen] = useState<Set<string>>(new Set());

  function toggle(key: string) {
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  if (!items.length) {
    return (
      <div className="flex flex-col items-center gap-1 px-6 py-14 text-center">
        <p className="text-sm font-medium">{emptyLabel}</p>
        <p className="max-w-md text-xs text-muted">
          Poluzuj progi w prawym górnym rogu albo poszerz zakres dat.
        </p>
      </div>
    );
  }

  return (
    <div className="divide-y divide-border">
      {items.map((it) => {
        const expanded = open.has(it.key);
        const ukryte = it.campaign_count_all - it.campaign_count;
        return (
          <div key={it.key}>
            <button
              onClick={() => toggle(it.key)}
              aria-expanded={expanded}
              className="flex w-full items-center gap-3 px-4 py-2.5 text-left transition-colors hover:bg-surface-2"
            >
              {expanded ? (
                <ChevronDown className="size-4 shrink-0 text-muted" />
              ) : (
                <ChevronRight className="size-4 shrink-0 text-muted" />
              )}

              {/* Miniatura mowi od razu, o ktory produkt chodzi - kod SKU nie.
                  Zwykly <img>, bo obrazy Amazona sa zewnetrzne i nie ma sensu
                  przepuszczac ich przez optymalizator Next. */}
              {it.image && (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={it.image}
                  alt=""
                  width={40}
                  height={40}
                  loading="lazy"
                  decoding="async"
                  className="size-10 shrink-0 rounded border border-border bg-surface-2 object-contain"
                />
              )}

              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium" title={it.title}>
                  {it.title}
                </div>
                {it.productName && (
                  <div className="truncate text-[11px] text-muted" title={it.productName}>
                    {it.productName}
                  </div>
                )}
                <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-muted">
                  <Badge tone="neutral">{it.campaign_count} kampanie</Badge>
                  {ukryte > 0 && <span>+{ukryte} ze śladowym ruchem</span>}
                  {it.subtitle && <span className="font-mono">{it.subtitle}</span>}
                </div>
              </div>

              <div className="hidden shrink-0 gap-5 text-right text-xs sm:flex">
                <div>
                  <div className="text-muted">Kliknięcia</div>
                  <div className="tnum font-medium">{num(it.clicks)}</div>
                </div>
                <div>
                  <div className="text-muted">Wydatek</div>
                  <div className="tnum font-medium">{money(it.cost, currency)}</div>
                </div>
                <div>
                  <div className="text-muted">ACOS</div>
                  <div className={`tnum font-medium ${acosTone(it.acos)}`}>{pct(it.acos)}</div>
                </div>
                <div className="w-24">
                  <div className="text-muted">{it.spreadLabel}</div>
                  <div className={`tnum font-medium ${it.spreadTone ?? ""}`}>
                    {it.spreadValue}
                  </div>
                </div>
              </div>
            </button>

            {expanded && (
              <div className="overflow-x-auto bg-surface-2/40 px-4 pb-3">
                <table className="w-full min-w-[36rem] text-xs">
                  <thead>
                    <tr className="text-muted">
                      <th className="py-1.5 text-left font-medium">Kampania</th>
                      <th className="py-1.5 text-right font-medium">Kliknięcia</th>
                      <th className="py-1.5 text-right font-medium">Wydatek</th>
                      <th className="py-1.5 text-right font-medium">Zam.</th>
                      <th className="py-1.5 text-right font-medium">CVR</th>
                      <th className="py-1.5 text-right font-medium">ACOS</th>
                    </tr>
                  </thead>
                  <tbody>
                    {it.breakdown.map((b) => (
                      <tr
                        key={b.campaign_id}
                        className={b.significant ? "" : "opacity-45"}
                      >
                        <td className="max-w-[18rem] truncate py-1.5 pr-3">
                          <span title={b.campaign_name ?? ""}>{b.campaign_name || "—"}</span>
                          {b.targeting_type && (
                            <span className="ml-1.5 text-[10px] text-muted">
                              {TARGETING_LABEL[b.targeting_type] ?? b.targeting_type.toLowerCase()}
                            </span>
                          )}
                        </td>
                        <td className="tnum py-1.5 text-right">{num(b.clicks)}</td>
                        <td className="tnum py-1.5 text-right">{money(b.cost, currency)}</td>
                        <td className="tnum py-1.5 text-right">{num(b.orders)}</td>
                        <td className="tnum py-1.5 text-right text-muted">{pct(b.cvr)}</td>
                        <td className={`tnum py-1.5 text-right font-medium ${acosTone(b.acos)}`}>
                          {pct(b.acos)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
