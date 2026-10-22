"use client";

import { AlertTriangle, Check, Loader2, ShieldCheck, X } from "lucide-react";
import { useState } from "react";
import { PlanTable } from "@/components/PlanTable";
import { Badge, Button, Card, ErrorState, Skeleton } from "@/components/ui";
import { api, type PendingPlan } from "@/lib/api";
import { useFilters } from "@/lib/filters";
import { count, dateTime , opisBledu} from "@/lib/format";
import { planKindLabel } from "@/lib/labels";
import { useData } from "@/lib/useData";

const SOURCE_LABEL: Record<string, string> = {
  RULE: "z reguły",
  SUGGESTION: "z sugestii",
};

/** Wiek planu po ludzku - pokazywany dopiero od godziny wzwyz. */
function planAge(minutes: number): string {
  if (minutes < 120) return `${minutes} min`;
  const godziny = Math.floor(minutes / 60);
  if (godziny < 48) return count(godziny, "godzinę", "godziny", "godzin");
  return count(Math.floor(godziny / 24), "dzień", "dni", "dni");
}

export default function ApprovalsPage() {
  const { profileId, revision, bump } = useFilters();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [message, setMessage] = useState<{ text: string; tone: "good" | "bad" } | null>(null);
  // Zaznaczone pozycje w obrębie planu. Klucz = plan_id.
  const [picked, setPicked] = useState<Map<string, Set<number>>>(new Map());

  const plans = useData(["plans", profileId, revision], () => api.pendingPlans(profileId));

  function selectionFor(plan: PendingPlan): Set<number> {
    // Domyślnie wszystko zaznaczone — częściowe zatwierdzanie ma być możliwe,
    // ale nie ma być dodatkową pracą przy typowym „zatwierdź całość"
    return picked.get(plan.plan_id) ?? new Set(plan.planned.map((_, i) => i));
  }

  function setSelection(planId: string, next: Set<number>) {
    setPicked((m) => new Map(m).set(planId, next));
  }

  async function apply(plan: PendingPlan) {
    const chosen = selectionFor(plan);
    if (chosen.size === 0) return;

    setBusyId(plan.plan_id);
    setMessage(null);
    try {
      const partial = chosen.size < plan.planned.length;
      const res = await api.applyPlan(
        plan.plan_id,
        partial ? [...chosen].sort((a, b) => a - b) : undefined,
      );
      setMessage({
        text:
          res.failed > 0
            ? `Zastosowano ${res.applied}, błędów: ${res.failed}. ${opisBledu(res.errors)}`
            : `Zastosowano ${res.applied} zmian` +
              (res.remaining ? ` · ${res.remaining} zostało w kolejce` : ""),
        tone: res.failed > 0 ? "bad" : "good",
      });
      setPicked((m) => {
        const next = new Map(m);
        next.delete(plan.plan_id);
        return next;
      });
      plans.reload();
      bump();
    } catch (err) {
      setMessage({ text: (err as Error).message.slice(0, 200), tone: "bad" });
    } finally {
      setBusyId(null);
    }
  }

  async function reject(plan: PendingPlan) {
    setBusyId(plan.plan_id);
    setMessage(null);
    try {
      await api.cancelPlan(plan.plan_id);
      setMessage({ text: "Plan usunięty z kolejki — nic nie zostało wysłane.", tone: "good" });
      plans.reload();
      bump();
    } catch (err) {
      setMessage({ text: (err as Error).message.slice(0, 200), tone: "bad" });
    } finally {
      setBusyId(null);
    }
  }

  if (plans.error) return <ErrorState message={plans.error} />;

  const rows = plans.data ?? [];

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-2 rounded-xl border border-border bg-surface px-4 py-3">
        <ShieldCheck className="mt-0.5 size-4 shrink-0 text-accent" />
        <div className="text-xs">
          <div className="text-sm font-medium">Nic nie dzieje się bez Twojej zgody</div>
          <p className="mt-0.5 text-muted">
            Każda zmiana — ręczna, z reguły czy z sugestii — trafia najpierw tutaj jako
            policzony plan. Możesz zatwierdzić całość albo tylko wybrane pozycje; reszta
            zostanie w kolejce. Plan czeka tu tak długo, aż go zatwierdzisz albo
            usuniesz — nie wygasa. Wysłana sugestia znika z listy Sugestii, żeby
            ta sama zmiana nie wisiała w dwóch miejscach.
          </p>
        </div>
      </div>

      {message && (
        <div
          className={`flex items-center gap-2 rounded-lg px-4 py-2.5 text-sm ${
            message.tone === "good" ? "bg-good-soft text-good" : "bg-bad-soft text-bad"
          }`}
        >
          {message.tone === "good" ? (
            <Check className="size-4" />
          ) : (
            <AlertTriangle className="size-4" />
          )}
          {message.text}
        </div>
      )}

      {plans.loading && !plans.data ? (
        <Card>
          <Skeleton rows={4} />
        </Card>
      ) : rows.length === 0 ? (
        <Card>
          <div className="flex flex-col items-center gap-1 px-6 py-14 text-center">
            <p className="text-sm font-medium">Nic nie czeka na zatwierdzenie</p>
            <p className="max-w-md text-xs text-muted">
              Plany pojawią się tutaj, gdy użyjesz akcji masowej, wyślesz sugestie albo
              gdy reguła coś zaproponuje.
            </p>
          </div>
        </Card>
      ) : (
        rows.map((plan) => {
          const chosen = selectionFor(plan);
          const partial = chosen.size < plan.planned.length;
          const left = plan.planned.length - chosen.size;
          return (
            <Card
              key={plan.plan_id}
              title={
                <span className="flex flex-wrap items-center gap-2">
                  <Badge tone="accent">{planKindLabel(plan.kind)}</Badge>
                  {plan.summary}
                  {plan.source && plan.source !== "MANUAL" && (
                    <Badge tone="neutral">{SOURCE_LABEL[plan.source] ?? plan.source}</Badge>
                  )}
                </span>
              }
              subtitle={`Utworzony ${dateTime(plan.created_at)}${
                plan.age_minutes >= 60
                  ? ` · czeka ${planAge(plan.age_minutes)}`
                  : ""
              } · ${plan.item_count} pozycji`}
              action={
                <div className="flex items-center gap-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => reject(plan)}
                    disabled={busyId === plan.plan_id}
                  >
                    <X className="size-3.5" /> Usuń
                  </Button>
                  <Button
                    variant="danger"
                    size="sm"
                    onClick={() => apply(plan)}
                    disabled={busyId === plan.plan_id || chosen.size === 0}
                  >
                    {busyId === plan.plan_id && (
                      <Loader2 className="size-3.5 animate-spin" />
                    )}
                    {partial
                      ? `Zatwierdź ${chosen.size} z ${plan.planned.length}`
                      : "Zatwierdź i wyślij"}
                  </Button>
                </div>
              }
            >
              <PlanTable
                planned={plan.planned}
                maxHeight="18rem"
                selected={chosen}
                onSelectedChange={(next) => setSelection(plan.plan_id, next)}
              />
              {partial && (
                <p className="border-t border-border px-4 py-2 text-xs text-muted">
                  {left === 1 ? "Niezaznaczona 1 pozycja zostanie" : `Niezaznaczone ${left} pozycji zostanie`}{" "}
                  w kolejce do osobnej decyzji.
                </p>
              )}
            </Card>
          );
        })
      )}
    </div>
  );
}
