"use client";

import { Plus, Trash2, X } from "lucide-react";
import { useState } from "react";
import type { Condition, RuleInput, RuleScope, RulesMeta } from "@/lib/api";
import { Badge, Button } from "./ui";

const METRIC_LABEL: Record<string, string> = {
  impressions: "Wyświetlenia",
  clicks: "Kliknięcia",
  cost: "Wydatek",
  orders: "Zamówienia",
  sales: "Sprzedaż",
  units: "Sztuki",
  ctr: "CTR (%)",
  cpc: "CPC",
  cvr: "CVR (%)",
  acos: "ACOS (%)",
  roas: "ROAS",
  cpa: "Koszt zamówienia",
  bid: "Stawka",
  tos_share: "Udział na górze wyników (%)",
};

const ACTION_LABEL: Record<string, string> = {
  SET_BID_PCT: "Zmień stawkę o %",
  SET_BID_TARGET_ACOS: "Ustaw stawkę pod docelowy ACOS",
  PAUSE: "Wstrzymaj",
  ENABLE: "Wznów",
  SET_BUDGET_PCT: "Zmień budżet o %",
  ADD_NEGATIVE_KEYWORD: "Dodaj negatyw (słowo kluczowe)",
  ADD_NEGATIVE_ASIN: "Wyklucz ASIN",
  HARVEST_KEYWORD: "Dodaj jako nowy keyword",
  SET_PLACEMENT_MODIFIER: "Podnieś modyfikator placementu",
};

const SCHEDULE_OPTIONS = [
  { value: "", label: "Tylko ręcznie" },
  { value: "6", label: "Co 6 godzin" },
  { value: "24", label: "Codziennie" },
  { value: "72", label: "Co 3 dni" },
  { value: "168", label: "Co tydzień" },
];

const field = "w-full rounded-lg border border-border bg-bg px-2.5 py-1.5 text-sm outline-none focus:border-accent";
const label = "mb-1 block text-xs font-medium text-muted";

export function RuleEditor({
  meta,
  initial,
  onSave,
  onCancel,
  saving,
  error,
}: {
  meta: RulesMeta;
  initial: RuleInput;
  onSave: (rule: RuleInput) => void;
  onCancel: () => void;
  saving: boolean;
  error: string | null;
}) {
  const [rule, setRule] = useState<RuleInput>(initial);

  const allowedActions = meta.scope_actions[rule.scope] ?? [];
  const conditions = rule.conditions.all ?? [];

  function patch(next: Partial<RuleInput>) {
    setRule((r) => ({ ...r, ...next }));
  }

  function setCondition(index: number, next: Partial<Condition>) {
    const updated = conditions.map((c, i) => (i === index ? { ...c, ...next } : c));
    patch({ conditions: { all: updated } });
  }

  function addCondition() {
    patch({ conditions: { all: [...conditions, { metric: "clicks", op: ">=", value: 10 }] } });
  }

  function removeCondition(index: number) {
    patch({ conditions: { all: conditions.filter((_, i) => i !== index) } });
  }

  function changeScope(scope: RuleScope) {
    const actions = meta.scope_actions[scope] ?? [];
    // akcja z poprzedniego zakresu moze byc tu niedozwolona
    const type = actions.includes(rule.action.type) ? rule.action.type : (actions[0] ?? "");
    patch({ scope, action: { ...rule.action, type } });
  }

  const action = rule.action;

  return (
    <div className="space-y-4">
      {error && (
        <div className="rounded-lg bg-bad-soft px-3 py-2 text-sm text-bad">{error}</div>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label className={label}>Nazwa</label>
          <input
            className={field}
            value={rule.name}
            onChange={(e) => patch({ name: e.target.value })}
            placeholder="np. Wyklucz frazy bez konwersji"
          />
        </div>
        <div>
          <label className={label}>Harmonogram</label>
          <select
            className={field}
            value={rule.schedule_hours ?? ""}
            onChange={(e) =>
              patch({ schedule_hours: e.target.value ? Number(e.target.value) : null })
            }
          >
            {SCHEDULE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div>
        <label className={label}>Opis (opcjonalnie)</label>
        <input
          className={field}
          value={rule.description ?? ""}
          onChange={(e) => patch({ description: e.target.value })}
          placeholder="Po co ta reguła istnieje"
        />
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label className={label}>Co oceniamy</label>
          <select
            className={field}
            value={rule.scope}
            onChange={(e) => changeScope(e.target.value as RuleScope)}
          >
            {Object.entries(meta.scopes).map(([key, text]) => (
              <option key={key} value={key}>
                {text}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className={label}>Okres danych</label>
          <select
            className={field}
            value={rule.lookback_days}
            onChange={(e) => patch({ lookback_days: Number(e.target.value) })}
          >
            {[7, 14, 30, 60, 90].map((d) => (
              <option key={d} value={d}>
                Ostatnie {d} dni
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* --- warunki --- */}
      <div>
        <div className="mb-1.5 flex items-center justify-between">
          <span className="text-xs font-medium text-muted">
            Warunki — wszystkie muszą być spełnione
          </span>
          <Button size="sm" variant="ghost" onClick={addCondition}>
            <Plus className="size-3.5" /> Dodaj warunek
          </Button>
        </div>
        <div className="space-y-2">
          {conditions.length === 0 && (
            <p className="rounded-lg border border-dashed border-border px-3 py-3 text-xs text-muted">
              Reguła bez warunków nic nie zrobi. Dodaj przynajmniej jeden.
            </p>
          )}
          {conditions.map((c, i) => (
            <div key={i} className="flex items-center gap-2">
              <select
                className={`${field} flex-1`}
                value={c.metric}
                onChange={(e) => setCondition(i, { metric: e.target.value })}
              >
                {meta.metrics.map((m) => (
                  <option key={m} value={m}>
                    {METRIC_LABEL[m] ?? m}
                  </option>
                ))}
              </select>
              <select
                className={`${field} w-20 shrink-0`}
                value={c.op}
                onChange={(e) => setCondition(i, { op: e.target.value })}
              >
                {meta.operators.map((o) => (
                  <option key={o} value={o}>
                    {o}
                  </option>
                ))}
              </select>
              <input
                className={`${field} tnum w-24 shrink-0`}
                inputMode="decimal"
                value={c.value}
                onChange={(e) => setCondition(i, { value: Number(e.target.value) })}
              />
              <button
                onClick={() => removeCondition(i)}
                className="shrink-0 text-muted hover:text-bad"
                title="Usuń warunek"
              >
                <Trash2 className="size-4" />
              </button>
            </div>
          ))}
        </div>
      </div>

      {/* --- akcja --- */}
      <div className="rounded-lg border border-border bg-surface-2 p-3">
        <div className="mb-2 flex items-center gap-2">
          <span className="text-xs font-medium text-muted">Co zrobić z dopasowaniami</span>
          <Badge tone="warn">wymaga zatwierdzenia</Badge>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <select
            className={field}
            value={action.type}
            onChange={(e) => patch({ action: { ...action, type: e.target.value } })}
          >
            {allowedActions.map((a) => (
              <option key={a} value={a}>
                {ACTION_LABEL[a] ?? a}
              </option>
            ))}
          </select>

          {(action.type === "SET_BID_PCT" || action.type === "SET_BUDGET_PCT") && (
            <div className="flex items-center gap-2">
              <input
                className={`${field} tnum`}
                inputMode="decimal"
                value={action.percent ?? 0}
                onChange={(e) => patch({ action: { ...action, percent: Number(e.target.value) } })}
              />
              <span className="shrink-0 text-xs text-muted">% (ujemne = w dół)</span>
            </div>
          )}

          {(action.type === "SET_BID_TARGET_ACOS" || action.type === "HARVEST_KEYWORD") && (
            <div className="flex items-center gap-2">
              <input
                className={`${field} tnum`}
                inputMode="decimal"
                value={action.target_acos ?? 30}
                onChange={(e) =>
                  patch({ action: { ...action, target_acos: Number(e.target.value) } })
                }
              />
              <span className="shrink-0 text-xs text-muted">% docelowy ACOS</span>
            </div>
          )}

          {action.type === "ADD_NEGATIVE_KEYWORD" && (
            <select
              className={field}
              value={action.match_type ?? "NEGATIVE_EXACT"}
              onChange={(e) => patch({ action: { ...action, match_type: e.target.value } })}
            >
              <option value="NEGATIVE_EXACT">Dopasowanie ścisłe</option>
              <option value="NEGATIVE_PHRASE">Dopasowanie do wyrażenia</option>
            </select>
          )}

          {action.type === "SET_PLACEMENT_MODIFIER" && (
            <div className="flex items-center gap-2">
              <input
                className={`${field} tnum`}
                inputMode="decimal"
                value={action.step ?? 10}
                onChange={(e) => patch({ action: { ...action, step: Number(e.target.value) } })}
              />
              <span className="shrink-0 text-xs text-muted">p.p. na przebieg, sufit</span>
              <input
                className={`${field} tnum w-20`}
                inputMode="decimal"
                value={action.max_percent ?? 100}
                onChange={(e) =>
                  patch({ action: { ...action, max_percent: Number(e.target.value) } })
                }
              />
              <span className="shrink-0 text-xs text-muted">%</span>
            </div>
          )}
        </div>
      </div>

      {/* --- limity --- */}
      <div className="grid gap-3 sm:grid-cols-3">
        <div>
          <label className={label}>Maks. pozycji na przebieg</label>
          <input
            className={`${field} tnum`}
            inputMode="numeric"
            value={rule.limits?.max_items ?? 100}
            onChange={(e) =>
              patch({ limits: { ...rule.limits, max_items: Number(e.target.value) } })
            }
          />
        </div>
        {(action.type === "SET_BID_PCT" || action.type === "SET_BID_TARGET_ACOS") && (
          <>
            <div>
              <label className={label}>Stawka min.</label>
              <input
                className={`${field} tnum`}
                inputMode="decimal"
                value={rule.limits?.min_bid ?? 0.05}
                onChange={(e) =>
                  patch({ limits: { ...rule.limits, min_bid: Number(e.target.value) } })
                }
              />
            </div>
            <div>
              <label className={label}>Stawka maks.</label>
              <input
                className={`${field} tnum`}
                inputMode="decimal"
                value={rule.limits?.max_bid ?? 5}
                onChange={(e) =>
                  patch({ limits: { ...rule.limits, max_bid: Number(e.target.value) } })
                }
              />
            </div>
          </>
        )}
      </div>

      <div className="flex items-center justify-end gap-2 border-t border-border pt-3">
        <Button variant="ghost" onClick={onCancel}>
          <X className="size-4" /> Anuluj
        </Button>
        <Button
          variant="primary"
          onClick={() => onSave(rule)}
          disabled={saving || !rule.name || conditions.length === 0}
        >
          Zapisz regułę
        </Button>
      </div>
    </div>
  );
}
