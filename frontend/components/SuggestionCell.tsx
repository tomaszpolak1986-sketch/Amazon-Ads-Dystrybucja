"use client";

import clsx from "clsx";
import { AlertTriangle, ArrowDown, ArrowUp, Ban, Check, Info } from "lucide-react";
import { useState } from "react";
import type { Suggestion } from "@/lib/api";

const TONE: Record<string, string> = {
  good: "bg-good-soft text-good border-good/25",
  warn: "bg-warn-soft text-warn border-warn/25",
  bad: "bg-bad-soft text-bad border-bad/25",
  info: "bg-surface-2 text-muted border-border",
};

// Ikona dobrana do rodzaju akcji, nie tylko do wagi - zeby "podnies stawke"
// i "obniz stawke" dalo sie odroznic jednym spojrzeniem
const ICON: Record<string, typeof Info> = {
  bid_up: ArrowUp,
  placement_up: ArrowUp,
  underused: ArrowUp,
  budget_capped: ArrowUp,
  bid_down: ArrowDown,
  placement_down: ArrowDown,
  kill: Ban,
  no_conversion: Ban,
  healthy: Check,
  performer: Check,
};

export function SuggestionCell({ suggestions }: { suggestions?: Suggestion[] }) {
  const [open, setOpen] = useState(false);

  if (!suggestions?.length) {
    return <span className="text-muted">—</span>;
  }

  const top = suggestions[0];
  const Icon = ICON[top.code] ?? (top.level === "bad" ? AlertTriangle : Info);

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className={clsx(
          "inline-flex max-w-full items-center gap-1.5 rounded-md border px-1.5 py-0.5 text-left text-xs font-medium",
          TONE[top.level] ?? TONE.info,
        )}
        title="Kliknij po uzasadnienie"
      >
        <Icon className="size-3 shrink-0" />
        <span className="truncate">{top.title}</span>
        {suggestions.length > 1 && (
          <span className="shrink-0 opacity-60">+{suggestions.length - 1}</span>
        )}
      </button>

      {open && (
        <>
          {/* Kliknięcie obok zamyka — bez tego panel zostawałby otwarty */}
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute top-full right-0 z-50 mt-1 w-80 rounded-lg border border-border bg-surface p-3 shadow-lg">
            <div className="flex flex-col gap-3">
              {suggestions.map((s, i) => {
                const I = ICON[s.code] ?? (s.level === "bad" ? AlertTriangle : Info);
                return (
                  <div key={i} className="flex gap-2">
                    <I
                      className={clsx(
                        "mt-0.5 size-3.5 shrink-0",
                        s.level === "good" && "text-good",
                        s.level === "warn" && "text-warn",
                        s.level === "bad" && "text-bad",
                        s.level === "info" && "text-muted",
                      )}
                    />
                    <div className="min-w-0">
                      <div className="text-xs font-medium">{s.title}</div>
                      <p className="mt-0.5 text-xs leading-relaxed text-muted">{s.detail}</p>
                      {s.basis && (
                        <p className="tnum mt-1 text-[10px] text-muted/70">
                          Podstawa: {s.basis}
                        </p>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
