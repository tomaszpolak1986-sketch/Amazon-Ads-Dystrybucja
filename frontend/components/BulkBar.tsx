"use client";

import { AlertTriangle, Check, Loader2, ShieldCheck, X } from "lucide-react";
import { useCallback, useState, type ReactNode } from "react";
import { api, type ActionResult } from "@/lib/api";
import { opisBledu } from "@/lib/format";
import { PlanTable } from "./PlanTable";
import { Button } from "./ui";

/**
 * Zatwierdzanie zmian.
 *
 * Backend nie ma endpointu, ktory po prostu "robi zmiane" - akcje zwracaja
 * policzony plan z plan_id, a dopiero applyPlan go wykonuje. Ten hook obsluguje
 * te dwa kroki: `preview` liczy plan, `confirm` go zatwierdza, `cancel` unieważnia.
 *
 * Nie ma tu sciezki, ktora pomijalaby potwierdzenie.
 */
export function useBulkAction(onApplied?: () => void) {
  const [pending, setPending] = useState<ActionResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ text: string; tone: "good" | "bad" } | null>(null);

  const preview = useCallback(async (run: () => Promise<ActionResult>) => {
    setBusy(true);
    setResult(null);
    try {
      setPending(await run());
    } catch (err) {
      setResult({ text: (err as Error).message.slice(0, 160), tone: "bad" });
    } finally {
      setBusy(false);
    }
  }, []);

  const confirm = useCallback(async () => {
    if (!pending) return;
    setBusy(true);
    try {
      const res = await api.applyPlan(pending.plan_id);
      setResult({
        text:
          res.failed > 0
            ? `Zastosowano ${res.applied}, błędów: ${res.failed}. ${opisBledu(res.errors)}`
            : `Zastosowano ${res.applied} zmian`,
        tone: res.failed > 0 ? "bad" : "good",
      });
      setPending(null);
      onApplied?.();
    } catch (err) {
      setResult({ text: (err as Error).message.slice(0, 200), tone: "bad" });
    } finally {
      setBusy(false);
    }
  }, [pending, onApplied]);

  const cancel = useCallback(async () => {
    if (!pending) return;
    const id = pending.plan_id;
    setPending(null);
    try {
      await api.cancelPlan(id);
    } catch {
      // plan wygasnie sam - brak akcji
    }
  }, [pending]);

  /** Komunikat bez akcji - np. gdy zaznaczenie wymaga doprecyzowania. */
  const notify = useCallback((text: string, tone: "good" | "bad" = "bad") => {
    setResult({ text, tone });
  }, []);

  return { preview, confirm, cancel, notify, pending, busy, result };
}

export function BulkBar({
  count,
  onClear,
  children,
  blockedReason,
  pending,
  busy,
  result,
  onConfirm,
  onCancel,
}: {
  count: number;
  onClear: () => void;
  children: ReactNode;
  blockedReason?: string | null;
  pending: ActionResult | null;
  busy: boolean;
  result: { text: string; tone: "good" | "bad" } | null;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  if (count === 0 && !result && !pending) return null;

  const plannedCount = pending?.item_count ?? 0;

  return (
    <div className="sticky bottom-4 z-30 mt-4">
      <div className="mx-auto flex max-w-4xl flex-col gap-2 rounded-xl border border-border bg-surface p-3 shadow-lg">
        {result && (
          <div
            className={`flex items-center gap-2 rounded-lg px-3 py-2 text-sm ${
              result.tone === "good" ? "bg-good-soft text-good" : "bg-bad-soft text-bad"
            }`}
          >
            {result.tone === "good" ? (
              <Check className="size-4" />
            ) : (
              <AlertTriangle className="size-4" />
            )}
            {result.text}
          </div>
        )}

        {pending ? (
          <div className="space-y-2">
            <div className="flex items-start gap-2 rounded-lg bg-warn-soft px-3 py-2">
              <ShieldCheck className="mt-0.5 size-4 shrink-0 text-warn" />
              <div className="min-w-0 text-xs">
                <div className="font-medium text-warn">Wymaga Twojego zatwierdzenia</div>
                <div className="mt-0.5 text-muted">
                  Nic nie zostało jeszcze wysłane do Amazona. Plan wygasa o{" "}
                  {new Date(pending.expires_at).toLocaleTimeString("pl-PL", {
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                  .
                </div>
              </div>
            </div>

            {/* Pokazujemy takze wtedy, gdy plan jest pusty, a sa pominiete
                pozycje - to wlasnie ich powod jest tu tresci wart. */}
            {(plannedCount > 0 || !!pending.skipped?.length) && (
              <div className="overflow-hidden rounded-lg border border-border">
                <PlanTable
                  planned={pending.planned}
                  skipped={pending.skipped}
                  limit={20}
                  maxHeight="12rem"
                />
              </div>
            )}

            <div className="flex flex-wrap items-center gap-3">
              <div className="min-w-0 flex-1 text-sm font-medium">{pending.summary}</div>
              <Button variant="ghost" size="sm" onClick={onCancel} disabled={busy}>
                Odrzuć
              </Button>
              <Button
                variant="danger"
                size="sm"
                onClick={onConfirm}
                disabled={busy || plannedCount === 0}
              >
                {busy && <Loader2 className="size-3.5 animate-spin" />}
                Zatwierdź i wyślij
              </Button>
            </div>
          </div>
        ) : (
          count > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-medium">
                Zaznaczono <span className="tnum">{count}</span>
              </span>
              <button
                onClick={onClear}
                className="text-muted hover:text-text"
                title="Wyczyść zaznaczenie"
              >
                <X className="size-3.5" />
              </button>
              <div className="mx-1 h-4 w-px bg-border" />
              {blockedReason ? (
                <span className="text-xs text-warn">{blockedReason}</span>
              ) : (
                <div className="flex flex-wrap items-center gap-2">{children}</div>
              )}
              {busy && <Loader2 className="size-4 animate-spin text-muted" />}
            </div>
          )
        )}
      </div>
    </div>
  );
}
