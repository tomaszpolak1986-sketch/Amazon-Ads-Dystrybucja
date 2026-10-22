"use client";

import { Eye, Pencil, Play, Plus, ShieldCheck, Trash2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { RuleEditor } from "@/components/RuleEditor";
import { Badge, Button, Card, ErrorState, Skeleton } from "@/components/ui";
import { api, type Rule, type RuleInput, type RulePreview, type RuleTemplate } from "@/lib/api";
import { useFilters } from "@/lib/filters";
import { dateTime } from "@/lib/format";
import { useData } from "@/lib/useData";

const EMPTY_RULE: RuleInput = {
  name: "",
  description: "",
  enabled: true,
  profile_id: null,
  scope: "SEARCH_TERM",
  lookback_days: 30,
  conditions: { all: [{ metric: "clicks", op: ">=", value: 50 }, { metric: "orders", op: "==", value: 0 }] },
  action: { type: "ADD_NEGATIVE_KEYWORD", match_type: "NEGATIVE_EXACT" },
  limits: { max_items: 100 },
  schedule_hours: null,
};

const STATUS_TONE: Record<string, "good" | "warn" | "bad" | "neutral"> = {
  PLAN_CREATED: "good",
  NO_MATCH: "neutral",
  NO_CHANGE: "neutral",
  ERROR: "bad",
};

export default function RulesPage() {
  const { profileId, profiles, bump } = useFilters();
  const [editing, setEditing] = useState<{ rule: RuleInput; id: number | null } | null>(null);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [message, setMessage] = useState<{ text: string; tone: "good" | "bad" } | null>(null);
  const [preview, setPreview] = useState<{ id: number; data: RulePreview } | null>(null);
  const [showTemplates, setShowTemplates] = useState(false);

  const meta = useData(["rules-meta"], () => api.rulesMeta());
  const rules = useData(["rules"], () => api.rules());

  const effectiveProfile = profileId ?? profiles[0]?.profile_id ?? null;

  async function save(rule: RuleInput) {
    setSaving(true);
    setFormError(null);
    try {
      if (editing?.id) await api.updateRule(editing.id, rule);
      else await api.createRule(rule);
      setEditing(null);
      rules.reload();
    } catch (err) {
      setFormError((err as Error).message.slice(0, 240));
    } finally {
      setSaving(false);
    }
  }

  async function run(rule: Rule) {
    setBusyId(rule.id);
    setMessage(null);
    setPreview(null);
    try {
      const res = await api.runRule(rule.id, profileId);
      const created = res.results.filter((r) => r.plan_id).length;
      const matched = res.results.reduce((a, r) => a + (r.matched ?? 0), 0);
      setMessage({
        text:
          created > 0
            ? `Znaleziono ${matched} dopasowań → ${created} propozycji czeka w „Do zatwierdzenia”. Nic nie wysłano.`
            : `Znaleziono ${matched} dopasowań, ale żadne nie prowadzi do zmiany wartości.`,
        tone: "good",
      });
      rules.reload();
      bump();
    } catch (err) {
      setMessage({ text: (err as Error).message.slice(0, 240), tone: "bad" });
    } finally {
      setBusyId(null);
    }
  }

  async function showPreview(rule: Rule) {
    if (!effectiveProfile) return;
    setBusyId(rule.id);
    setMessage(null);
    try {
      const data = await api.previewRule(rule.id, effectiveProfile);
      setPreview({ id: rule.id, data });
    } catch (err) {
      setMessage({ text: (err as Error).message.slice(0, 240), tone: "bad" });
    } finally {
      setBusyId(null);
    }
  }

  async function toggle(rule: Rule) {
    await api.updateRule(rule.id, { ...rule, enabled: !rule.enabled });
    rules.reload();
  }

  async function remove(rule: Rule) {
    await api.deleteRule(rule.id);
    rules.reload();
  }

  function fromTemplate(t: RuleTemplate) {
    const { key: _key, ...rest } = t;
    void _key;
    setEditing({ rule: { ...rest, enabled: true, schedule_hours: null }, id: null });
    setShowTemplates(false);
  }

  if (rules.error) return <ErrorState message={rules.error} />;
  if (meta.error) return <ErrorState message={meta.error} />;

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-2 rounded-xl border border-border bg-surface px-4 py-3">
        <ShieldCheck className="mt-0.5 size-4 shrink-0 text-accent" />
        <div className="min-w-0 flex-1 text-xs">
          <div className="text-sm font-medium">Reguły proponują, nie wykonują</div>
          <p className="mt-0.5 text-muted">
            Uruchomiona reguła ocenia dane i tworzy propozycję w kolejce{" "}
            <Link href="/approvals" className="text-accent underline">
              Do zatwierdzenia
            </Link>
            . Nic nie trafia do Amazona, dopóki sam tego nie klikniesz — dotyczy to także reguł
            z harmonogramem.
          </p>
        </div>
        <Button onClick={() => setShowTemplates((v) => !v)}>
          <Plus className="size-4" /> Nowa reguła
        </Button>
      </div>

      {message && (
        <div
          className={`rounded-lg px-4 py-2.5 text-sm ${
            message.tone === "good" ? "bg-good-soft text-good" : "bg-bad-soft text-bad"
          }`}
        >
          {message.text}
        </div>
      )}

      {showTemplates && meta.data && (
        <Card title="Zacznij od szablonu" subtitle="Każdy próg możesz zmienić przed zapisaniem">
          <div className="grid gap-2 p-4 sm:grid-cols-2">
            {meta.data.templates.map((t) => (
              <button
                key={t.key}
                onClick={() => fromTemplate(t)}
                className="rounded-lg border border-border p-3 text-left transition-colors hover:border-accent hover:bg-surface-2"
              >
                <div className="text-sm font-medium">{t.name}</div>
                <div className="mt-0.5 text-xs text-muted">{t.description}</div>
              </button>
            ))}
            <button
              onClick={() => {
                setEditing({ rule: EMPTY_RULE, id: null });
                setShowTemplates(false);
              }}
              className="rounded-lg border border-dashed border-border p-3 text-left transition-colors hover:border-accent"
            >
              <div className="text-sm font-medium">Pusta reguła</div>
              <div className="mt-0.5 text-xs text-muted">Zbuduj warunki od zera</div>
            </button>
          </div>
        </Card>
      )}

      {editing && meta.data && (
        <Card title={editing.id ? "Edycja reguły" : "Nowa reguła"}>
          <div className="p-4">
            <RuleEditor
              meta={meta.data}
              initial={editing.rule}
              onSave={save}
              onCancel={() => setEditing(null)}
              saving={saving}
              error={formError}
            />
          </div>
        </Card>
      )}

      {rules.loading && !rules.data ? (
        <Card>
          <Skeleton rows={4} />
        </Card>
      ) : (rules.data ?? []).length === 0 ? (
        <Card>
          <div className="flex flex-col items-center gap-1 px-6 py-14 text-center">
            <p className="text-sm font-medium">Nie masz jeszcze żadnych reguł</p>
            <p className="max-w-md text-xs text-muted">
              Zacznij od szablonu — np. „Wyklucz frazy bez konwersji” albo „Przenieś budżet na
              lepiej konwertujący placement”.
            </p>
          </div>
        </Card>
      ) : (
        (rules.data ?? []).map((rule) => (
          <Card
            key={rule.id}
            title={
              <span className="flex items-center gap-2">
                {rule.name}
                {!rule.enabled && <Badge tone="neutral">wyłączona</Badge>}
                {rule.schedule_hours ? (
                  <Badge tone="accent">co {rule.schedule_hours} h</Badge>
                ) : (
                  <Badge tone="neutral">ręcznie</Badge>
                )}
              </span>
            }
            subtitle={rule.description || rule.summary}
            action={
              <div className="flex items-center gap-1">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => showPreview(rule)}
                  disabled={busyId === rule.id || !effectiveProfile}
                  title="Pokaż, co reguła by złapała"
                >
                  <Eye className="size-3.5" /> Podgląd
                </Button>
                <Button
                  size="sm"
                  onClick={() => run(rule)}
                  disabled={busyId === rule.id}
                  title="Utwórz propozycje do zatwierdzenia"
                >
                  <Play className="size-3.5" /> Uruchom
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setEditing({ rule, id: rule.id })}
                  title="Edytuj"
                >
                  <Pencil className="size-3.5" />
                </Button>
                <Button size="sm" variant="ghost" onClick={() => toggle(rule)}>
                  {rule.enabled ? "Wyłącz" : "Włącz"}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => remove(rule)} title="Usuń">
                  <Trash2 className="size-3.5" />
                </Button>
              </div>
            }
          >
            <div className="space-y-3 p-4">
              <div className="flex flex-wrap items-center gap-1.5 text-xs">
                <Badge tone="neutral">{meta.data?.scopes[rule.scope] ?? rule.scope}</Badge>
                <span className="text-muted">ostatnie {rule.lookback_days} dni</span>
                <span className="text-muted">·</span>
                {(rule.conditions.all ?? []).map((c, i) => (
                  <Badge key={i} tone="neutral">
                    {c.metric} {c.op} {c.value}
                  </Badge>
                ))}
                <span className="text-muted">→</span>
                <Badge tone="warn">{rule.action.type}</Badge>
              </div>

              {rule.last_run && (
                <div className="flex flex-wrap items-center gap-2 rounded-lg bg-surface-2 px-3 py-2 text-xs">
                  <Badge tone={STATUS_TONE[rule.last_run.status] ?? "neutral"}>
                    {rule.last_run.status}
                  </Badge>
                  <span className="text-muted">
                    {dateTime(rule.last_run.ts)} · oceniono{" "}
                    <span className="tnum">{rule.last_run.evaluated}</span>, dopasowano{" "}
                    <span className="tnum">{rule.last_run.matched}</span>
                  </span>
                  {rule.last_run.plan_id && (
                    <Link href="/approvals" className="text-accent underline">
                      zobacz propozycję
                    </Link>
                  )}
                </div>
              )}

              {preview?.id === rule.id && (
                <div className="rounded-lg border border-border">
                  <div className="border-b border-border px-3 py-2 text-xs">
                    Podgląd na {preview.data.range.start} → {preview.data.range.end}: oceniono{" "}
                    <span className="tnum font-medium">{preview.data.evaluated}</span>, pasuje{" "}
                    <span className="tnum font-medium">{preview.data.matched}</span>
                    {preview.data.matched > preview.data.limited_to && (
                      <span className="text-warn">
                        {" "}
                        (limit przebiegu: {preview.data.limited_to})
                      </span>
                    )}
                  </div>
                  {preview.data.sample.length > 0 && (
                    <div className="max-h-56 overflow-auto">
                      <table className="w-full text-xs">
                        <tbody>
                          {preview.data.sample.map((row, i) => (
                            <tr key={i} className="border-b border-border/60 last:border-0">
                              <td className="px-3 py-1">
                                {String(
                                  row.search_term ??
                                    row.target_text ??
                                    row.name ??
                                    row.placement ??
                                    row.sku ??
                                    "—",
                                )}
                              </td>
                              <td className="tnum px-3 py-1 text-right text-muted">
                                {String(row.clicks ?? 0)} klik.
                              </td>
                              <td className="tnum px-3 py-1 text-right text-muted">
                                {String(row.orders ?? 0)} zam.
                              </td>
                              <td className="tnum px-3 py-1 text-right text-muted">
                                ACOS {row.acos === null ? "—" : `${String(row.acos)}%`}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}
            </div>
          </Card>
        ))
      )}
    </div>
  );
}
