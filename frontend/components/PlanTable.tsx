"use client";

import { ArrowRight } from "lucide-react";
import type { PlannedChange } from "@/lib/api";
import { entityLabel } from "@/lib/labels";
import { Badge } from "./ui";

/**
 * Lista zmian w planie.
 *
 * Wczesniej kolejka pokazywala sam identyfikator ("10000000010003"), z ktorego
 * nie dalo sie poznac, czego dotyczy zmiana. Teraz kazdy wiersz mowi: co za
 * obiekt, ktore pole i z jakiej wartosci na jaka - a identyfikator zostaje
 * drobnym drukiem, bo bywa potrzebny przy szukaniu w panelu Amazona.
 *
 * Gdy podano `selected`, kazdy wiersz dostaje zaznaczenie: plan da sie wtedy
 * zatwierdzic czesciowo, a reszta zostaje w kolejce.
 */
export function PlanTable({
  planned,
  limit = 50,
  maxHeight = "16rem",
  selected,
  onSelectedChange,
  skipped,
}: {
  planned: PlannedChange[];
  limit?: number;
  maxHeight?: string;
  selected?: Set<number>;
  onSelectedChange?: (next: Set<number>) => void;
  /** Pozycje, których nie da się wykonać — z powodem. */
  skipped?: { keyword: string; reason: string }[];
}) {
  // Pominięte pokazujemy zawsze, także obok niepustego planu: część pozycji
  // może odpaść i bez tego znikałaby bez śladu.
  const pominiete = skipped?.length ? (
    <div className="border-t border-border px-4 py-3">
      <div className="mb-1.5 text-[11px] font-semibold tracking-wide text-muted uppercase">
        Pominięto ({skipped.length})
      </div>
      <ul className="space-y-1.5">
        {skipped.map((s, i) => (
          <li key={i} className="text-xs">
            <span className="font-medium">{s.keyword}</span>
            <span className="text-muted"> — {s.reason}</span>
          </li>
        ))}
      </ul>
    </div>
  ) : null;

  if (!planned.length) {
    return (
      <div>
        <p className="px-4 py-3 text-xs text-muted">
          {/* Bez powodu ten ekran mowil "wartosci sa juz takie same", co przy
              pominietych pozycjach bylo po prostu nieprawda. */}
          {skipped?.length
            ? "Nie ma czego dodać — żadnej pozycji nie da się wykonać."
            : "Nic się nie zmieni — wartości są już takie same."}
        </p>
        {pominiete}
      </div>
    );
  }

  const selectable = Boolean(selected && onSelectedChange);
  const shown = planned.slice(0, limit);
  const allSelected = selectable && planned.every((_, i) => selected!.has(i));

  function toggleAll() {
    if (!onSelectedChange) return;
    onSelectedChange(allSelected ? new Set() : new Set(planned.map((_, i) => i)));
  }

  function toggleOne(index: number) {
    if (!onSelectedChange || !selected) return;
    const next = new Set(selected);
    if (next.has(index)) next.delete(index);
    else next.add(index);
    onSelectedChange(next);
  }

  return (
    <div>
      <div className="overflow-auto" style={{ maxHeight }}>
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-surface-2">
            <tr className="border-b border-border text-xs text-muted">
              {selectable && (
                <th className="w-9 px-3 py-2">
                  <input
                    type="checkbox"
                    aria-label="Zaznacz wszystkie zmiany"
                    className="size-3.5 accent-[var(--accent)]"
                    checked={allSelected}
                    ref={(el) => {
                      if (el) {
                        el.indeterminate =
                          !allSelected && planned.some((_, i) => selected!.has(i));
                      }
                    }}
                    onChange={toggleAll}
                  />
                </th>
              )}
              <th className="px-4 py-2 text-left font-semibold">Czego dotyczy</th>
              <th className="px-4 py-2 text-left font-semibold">Co się zmienia</th>
              <th className="px-4 py-2 text-right font-semibold">Zmiana</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((p, i) => {
              const hasOld = p.old !== null && p.old !== undefined && p.old !== "";
              const oldText = String(p.old_label ?? p.old ?? "");
              const newText = String(p.new_label ?? p.new ?? "");
              const isSelected = selectable && selected!.has(i);
              return (
                <tr
                  key={`${p.id}-${i}`}
                  className={`border-b border-border/60 last:border-0 ${
                    isSelected ? "bg-accent-soft" : ""
                  }`}
                >
                  {selectable && (
                    <td className="px-3 py-2">
                      <input
                        type="checkbox"
                        aria-label="Zaznacz zmianę"
                        className="size-3.5 accent-[var(--accent)]"
                        checked={isSelected}
                        onChange={() => toggleOne(i)}
                      />
                    </td>
                  )}
                  <td className="px-4 py-2">
                    <div className="flex items-center gap-1.5">
                      {p.entity_type && (
                        <Badge tone="neutral">{entityLabel(p.entity_type)}</Badge>
                      )}
                      <span className="truncate text-xs font-medium" title={p.entity_name ?? ""}>
                        {p.entity_name || "—"}
                      </span>
                    </div>
                    {p.context && (
                      <div className="truncate text-[11px] text-muted" title={p.context}>
                        w kampanii {p.context}
                      </div>
                    )}
                    <div className="mt-0.5 font-mono text-[10px] text-muted/70">{p.id}</div>
                  </td>
                  <td className="px-4 py-2 text-xs text-muted">
                    <div>{p.field_label ?? p.field}</div>
                    {p.reason && (
                      <div className="mt-0.5 text-[11px] text-muted/80">{p.reason}</div>
                    )}
                  </td>
                  <td className="px-4 py-2 text-right">
                    <span className="tnum inline-flex items-center gap-1.5 text-xs">
                      {hasOld ? (
                        <>
                          <span className="text-muted line-through">{oldText}</span>
                          <ArrowRight className="size-3 text-muted" />
                        </>
                      ) : (
                        <span className="text-muted">dodanie</span>
                      )}
                      <span className="font-medium">{newText}</span>
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {planned.length > limit && (
        <div className="border-t border-border px-4 py-1.5 text-[11px] text-muted">
          …i {planned.length - limit} dalszych pozycji
        </div>
      )}
      {pominiete}
    </div>
  );
}
