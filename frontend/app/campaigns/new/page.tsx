"use client";

import { AlertTriangle, ArrowRight, Loader2, Plus, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { ProductPicker } from "@/components/ProductPicker";
import { Badge, Button, Card, ErrorState } from "@/components/ui";
import { api, type CampaignSpec } from "@/lib/api";
import { useFilters } from "@/lib/filters";
import { count, money } from "@/lib/format";
import { useData } from "@/lib/useData";

/** Grupy targetowania automatycznego tak, jak nazywa je panel Amazona. */
const AUTO_GROUPS: Array<{ key: string; label: string; hint: string }> = [
  {
    key: "QUERY_HIGH_REL_MATCHES",
    label: "Ściśle powiązane",
    hint: "Zapytania bliskie Twojemu produktowi",
  },
  {
    key: "QUERY_BROAD_REL_MATCHES",
    label: "Luźno powiązane",
    hint: "Szersze zapytania z tej samej okolicy",
  },
  {
    key: "ASIN_SUBSTITUTE_RELATED",
    label: "Zamienniki",
    hint: "Karty produktów, które konkurują z Twoim",
  },
  {
    key: "ASIN_ACCESSORY_RELATED",
    label: "Uzupełniające",
    hint: "Produkty kupowane razem z Twoim",
  },
];

const MATCH_TYPES: Array<{ key: string; label: string }> = [
  { key: "EXACT", label: "ścisłe" },
  { key: "PHRASE", label: "wyrażenie" },
  { key: "BROAD", label: "przybliżone" },
];

const STRATEGIES: Array<{ key: CampaignSpec["bidding_strategy"]; label: string; hint: string }> = [
  {
    key: "LEGACY_FOR_SALES",
    label: "Obniżaj tylko",
    hint: "Amazon może zejść ze stawką, gdy sprzedaż jest mało prawdopodobna. Nigdy nie podnosi.",
  },
  {
    key: "AUTO_FOR_SALES",
    label: "Podnoś i obniżaj",
    hint: "Amazon może podnieść stawkę nawet o 100% tam, gdzie widzi szansę na sprzedaż.",
  },
  { key: "MANUAL", label: "Stała", hint: "Twoja stawka bez korekt po stronie Amazona." },
];

/** Dzieli wklejony tekst na wiersze, ucinając puste i powtórzone. */
function lines(text: string): string[] {
  const out = text
    .split(/[\n,;]+/)
    .map((s) => s.trim())
    .filter(Boolean);
  return [...new Set(out)];
}

const ASIN = /^B0[A-Z0-9]{8}$/;

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="text-xs font-medium">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11px] text-muted">{hint}</span>}
    </label>
  );
}

const inputClass =
  "mt-1 w-full rounded-lg border border-border bg-bg px-2.5 py-1.5 text-sm outline-none focus:border-accent";

export default function NewCampaignPage() {
  const router = useRouter();
  const { profileId, currency, bump } = useFilters();

  const portfolios = useData(["portfolios-list", profileId], () =>
    api.portfolios({ profile_id: profileId ?? undefined, days: 7 }),
  );

  const [name, setName] = useState("");
  const [budget, setBudget] = useState("20");
  const [portfolioId, setPortfolioId] = useState("");
  const [targeting, setTargeting] = useState<"AUTO" | "MANUAL">("AUTO");
  const [strategy, setStrategy] =
    useState<CampaignSpec["bidding_strategy"]>("LEGACY_FOR_SALES");
  const [startEnabled, setStartEnabled] = useState(false);

  const [groupName, setGroupName] = useState("Grupa 1");
  const [defaultBid, setDefaultBid] = useState("0.35");

  const [productsText, setProductsText] = useState("");
  const [autoGroups, setAutoGroups] = useState<Set<string>>(
    () => new Set(AUTO_GROUPS.map((g) => g.key)),
  );
  const [keywordsText, setKeywordsText] = useState("");
  const [matchTypes, setMatchTypes] = useState<Set<string>>(() => new Set(["EXACT"]));
  const [keywordBid, setKeywordBid] = useState("");
  const [targetsText, setTargetsText] = useState("");
  const [negativesText, setNegativesText] = useState("");

  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  // Produkty rozdzielamy po kształcie: ASIN ma stały format, reszta to SKU.
  const { skus, asins } = useMemo(() => {
    const all = lines(productsText);
    return {
      asins: all.filter((v) => ASIN.test(v.toUpperCase())).map((v) => v.toUpperCase()),
      skus: all.filter((v) => !ASIN.test(v.toUpperCase())),
    };
  }, [productsText]);

  const keywords = useMemo(() => {
    if (targeting !== "MANUAL") return [];
    const bid = keywordBid.trim() ? Number(keywordBid) : null;
    return lines(keywordsText).flatMap((text) =>
      [...matchTypes].map((match_type) => ({ text, match_type, bid })),
    );
  }, [targeting, keywordsText, matchTypes, keywordBid]);

  const productTargets = useMemo(
    () =>
      targeting === "MANUAL"
        ? lines(targetsText)
            .map((v) => v.toUpperCase())
            .filter((v) => ASIN.test(v))
            .map((asin) => ({ asin }))
        : [],
    [targeting, targetsText],
  );

  const negatives = useMemo(
    () => lines(negativesText).map((text) => ({ text, match_type: "NEGATIVE_PHRASE" })),
    [negativesText],
  );

  const badTargets = useMemo(
    () =>
      targeting === "MANUAL"
        ? lines(targetsText).filter((v) => !ASIN.test(v.toUpperCase()))
        : [],
    [targeting, targetsText],
  );

  const elementCount =
    2 + skus.length + asins.length + keywords.length + productTargets.length +
    negatives.length + (targeting === "AUTO" ? autoGroups.size : 0);

  function toggle(set: Set<string>, key: string, apply: (s: Set<string>) => void) {
    const next = new Set(set);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    apply(next);
  }

  async function submit() {
    if (profileId === null) return;
    setBusy(true);
    setProblem(null);
    try {
      const spec: CampaignSpec = {
        name: name.trim(),
        daily_budget: Number(budget),
        targeting_type: targeting,
        bidding_strategy: strategy,
        state: startEnabled ? "ENABLED" : "PAUSED",
        portfolio_id: portfolioId || null,
        ad_group: { name: groupName.trim(), default_bid: Number(defaultBid) },
        skus,
        asins,
        keywords,
        product_targets: productTargets,
        auto_groups: targeting === "AUTO" ? [...autoGroups] : [],
        negative_keywords: negatives,
      };
      await api.createCampaign(profileId, spec);
      bump();
      router.push("/approvals");
    } catch (err) {
      setProblem((err as Error).message.slice(0, 400));
    } finally {
      setBusy(false);
    }
  }

  if (portfolios.error) return <ErrorState message={portfolios.error} />;

  if (profileId === null) {
    return (
      <Card>
        <div className="flex flex-col items-center gap-1 px-6 py-14 text-center">
          <p className="text-sm font-medium">Wybierz rynek</p>
          <p className="max-w-md text-xs text-muted">
            Kampania powstaje na konkretnym rynku — wybierz go u góry, żeby wiedzieć,
            w jakiej walucie i wśród jakich portfolii ją tworzysz.
          </p>
        </div>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-2 rounded-xl border border-border bg-surface px-4 py-3">
        <ShieldCheck className="mt-0.5 size-4 shrink-0 text-accent" />
        <div className="min-w-0 text-xs">
          <div className="text-sm font-medium">Ten formularz niczego nie tworzy</div>
          <p className="mt-0.5 text-muted">
            Wypełnij i policz plan — trafi do{" "}
            <Link href="/approvals" className="text-accent underline">
              zatwierdzeń
            </Link>{" "}
            jako lista elementów do obejrzenia. Kampania powstanie w Amazonie dopiero,
            gdy ją tam zatwierdzisz. Nowej kampanii nie da się zatwierdzić po kawałku:
            grupa reklam i słowa kluczowe powstają wewnątrz niej, więc idzie całość albo nic.
          </p>
        </div>
      </div>

      {problem && (
        <div className="flex items-start gap-2 rounded-lg bg-bad-soft px-4 py-2.5 text-sm text-bad">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          <span>{problem}</span>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Podstawy">
          <div className="space-y-3 px-4 py-3">
            <Field label="Nazwa kampanii" hint="Musi być niepowtarzalna na tym rynku.">
              <input
                className={inputClass}
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="SP | Listwy | Exact"
              />
            </Field>

            <div className="grid grid-cols-2 gap-3">
              <Field label={`Budżet dzienny (${currency})`}>
                <input
                  className={inputClass}
                  type="number"
                  min={1}
                  step="0.5"
                  value={budget}
                  onChange={(e) => setBudget(e.target.value)}
                />
              </Field>
              <Field label="Portfolio">
                <select
                  className={inputClass}
                  value={portfolioId}
                  onChange={(e) => setPortfolioId(e.target.value)}
                >
                  <option value="">bez portfolio</option>
                  {(portfolios.data ?? [])
                    .filter((p) => p.portfolio_id && String(p.portfolio_id) !== "0")
                    .map((p) => (
                      <option key={p.portfolio_id} value={String(p.portfolio_id)}>
                        {p.name}
                      </option>
                    ))}
                </select>
              </Field>
            </div>

            <Field label="Rodzaj targetowania">
              <div className="mt-1 flex gap-2">
                {(["AUTO", "MANUAL"] as const).map((t) => (
                  <button
                    key={t}
                    onClick={() => setTargeting(t)}
                    className={`flex-1 rounded-lg border px-3 py-2 text-left text-xs transition-colors ${
                      targeting === t
                        ? "border-accent bg-accent-soft"
                        : "border-border hover:bg-surface-2"
                    }`}
                  >
                    <div className="text-sm font-medium">
                      {t === "AUTO" ? "Automatyczna" : "Ręczna"}
                    </div>
                    <div className="mt-0.5 text-muted">
                      {t === "AUTO"
                        ? "Amazon dobiera zapytania sam"
                        : "Ty podajesz frazy i produkty"}
                    </div>
                  </button>
                ))}
              </div>
            </Field>

            <Field label="Strategia stawek">
              <select
                className={inputClass}
                value={strategy}
                onChange={(e) =>
                  setStrategy(e.target.value as CampaignSpec["bidding_strategy"])
                }
              >
                {STRATEGIES.map((s) => (
                  <option key={s.key} value={s.key}>
                    {s.label}
                  </option>
                ))}
              </select>
              <span className="mt-1 block text-[11px] text-muted">
                {STRATEGIES.find((s) => s.key === strategy)?.hint}
              </span>
            </Field>

            <label className="flex items-start gap-2 rounded-lg border border-border px-3 py-2">
              <input
                type="checkbox"
                className="mt-0.5 size-3.5 accent-[var(--accent)]"
                checked={startEnabled}
                onChange={(e) => setStartEnabled(e.target.checked)}
              />
              <span className="text-xs">
                <span className="font-medium">Uruchom od razu po zatwierdzeniu</span>
                <span className="mt-0.5 block text-muted">
                  Domyślnie kampania powstaje wstrzymana, żebyś mógł ją obejrzeć, zanim
                  zacznie wydawać. Jeśli którykolwiek element nie powstanie, zostanie
                  wstrzymana mimo tego zaznaczenia.
                </span>
              </span>
            </label>
          </div>
        </Card>

        <Card title="Grupa reklam i produkty">
          <div className="space-y-3 px-4 py-3">
            <div className="grid grid-cols-2 gap-3">
              <Field label="Nazwa grupy">
                <input
                  className={inputClass}
                  value={groupName}
                  onChange={(e) => setGroupName(e.target.value)}
                />
              </Field>
              <Field label={`Stawka domyślna (${currency})`}>
                <input
                  className={inputClass}
                  type="number"
                  min={0.02}
                  step="0.01"
                  value={defaultBid}
                  onChange={(e) => setDefaultBid(e.target.value)}
                />
              </Field>
            </div>

            <Field
              label="Produkty"
              hint="Wyszukaj i kliknij, żeby dodać. Możesz też wkleić listę — po jednym w wierszu, ASIN-y rozpoznaję po formacie."
            >
              <div className="space-y-2">
                <ProductPicker
                  profileId={profileId}
                  wybrane={new Set([...skus, ...asins])}
                  onDodaj={(kod) =>
                    setProductsText((biezacy) =>
                      biezacy.trim() ? `${biezacy.trimEnd()}\n${kod}` : kod,
                    )
                  }
                />
                <textarea
                  className={`${inputClass} h-24 font-mono text-xs`}
                  value={productsText}
                  onChange={(e) => setProductsText(e.target.value)}
                  placeholder={"FBA-800-18-BIALY-5\nB09VZ4K1XG"}
                />
              </div>
            </Field>
            {(skus.length > 0 || asins.length > 0) && (
              <div className="flex flex-wrap gap-1.5">
                {skus.length > 0 && <Badge tone="neutral">{skus.length} SKU</Badge>}
                {asins.length > 0 && <Badge tone="neutral">{asins.length} ASIN</Badge>}
              </div>
            )}

            <Field label="Wykluczenia na start" hint="Frazy jako wyrażenie. Możesz zostawić puste.">
              <textarea
                className={`${inputClass} h-16 text-xs`}
                value={negativesText}
                onChange={(e) => setNegativesText(e.target.value)}
                placeholder="holz"
              />
            </Field>
          </div>
        </Card>
      </div>

      <Card
        title={targeting === "AUTO" ? "Grupy automatyczne" : "Na co licytujesz"}
        subtitle={
          targeting === "AUTO"
            ? "Amazon sam dobiera zapytania w obrębie zaznaczonych grup. Osobne stawki dla każdej grupy ustawisz później, gdy będą wyniki."
            : "Podaj frazy, produkty konkurencji albo jedno i drugie. Puste pole stawki oznacza stawkę domyślną grupy."
        }
      >
        <div className="space-y-3 px-4 py-3">
          {targeting === "AUTO" ? (
            <div className="grid gap-2 sm:grid-cols-2">
              {AUTO_GROUPS.map((g) => (
                <label
                  key={g.key}
                  className={`flex cursor-pointer items-start gap-2 rounded-lg border px-3 py-2 transition-colors ${
                    autoGroups.has(g.key)
                      ? "border-accent bg-accent-soft"
                      : "border-border hover:bg-surface-2"
                  }`}
                >
                  <input
                    type="checkbox"
                    className="mt-0.5 size-3.5 accent-[var(--accent)]"
                    checked={autoGroups.has(g.key)}
                    onChange={() => toggle(autoGroups, g.key, setAutoGroups)}
                  />
                  <span className="text-xs">
                    <span className="font-medium">{g.label}</span>
                    <span className="mt-0.5 block text-muted">{g.hint}</span>
                  </span>
                </label>
              ))}
            </div>
          ) : (
            <div className="grid gap-3 lg:grid-cols-2">
              <div className="space-y-2">
                <Field label="Słowa kluczowe" hint="Po jednym w wierszu.">
                  <textarea
                    className={`${inputClass} h-28 text-xs`}
                    value={keywordsText}
                    onChange={(e) => setKeywordsText(e.target.value)}
                    placeholder={"słowo kluczowe\ndrugie słowo kluczowe"}
                  />
                </Field>
                <div className="flex flex-wrap items-end gap-2">
                  <div>
                    <span className="text-xs font-medium">Dopasowanie</span>
                    <div className="mt-1 flex gap-1">
                      {MATCH_TYPES.map((m) => (
                        <button
                          key={m.key}
                          onClick={() => toggle(matchTypes, m.key, setMatchTypes)}
                          className={`rounded-lg px-2.5 py-1 text-xs font-medium transition-colors ${
                            matchTypes.has(m.key)
                              ? "bg-accent-soft text-accent"
                              : "text-muted hover:bg-surface-2"
                          }`}
                        >
                          {m.label}
                        </button>
                      ))}
                    </div>
                  </div>
                  <Field label={`Stawka (${currency})`}>
                    <input
                      className={`${inputClass} w-28`}
                      type="number"
                      min={0.02}
                      step="0.01"
                      value={keywordBid}
                      onChange={(e) => setKeywordBid(e.target.value)}
                      placeholder="domyślna"
                    />
                  </Field>
                </div>
                {keywordsText.trim() && matchTypes.size === 0 && (
                  <p className="text-[11px] text-warn">
                    Zaznacz przynajmniej jedno dopasowanie, inaczej frazy nie powstaną.
                  </p>
                )}
              </div>

              <Field
                label="Targetowanie produktowe"
                hint="ASIN-y konkurencji, po jednym w wierszu."
              >
                <textarea
                  className={`${inputClass} h-28 font-mono text-xs`}
                  value={targetsText}
                  onChange={(e) => setTargetsText(e.target.value)}
                  placeholder="B0XXXXXXXX"
                />
                {badTargets.length > 0 && (
                  <span className="mt-1 block text-[11px] text-warn">
                    Pominę {count(badTargets.length, "wpis", "wpisy", "wpisów")} bez
                    formatu ASIN:{" "}
                    {badTargets.slice(0, 3).join(", ")}
                    {badTargets.length > 3 && "…"}
                  </span>
                )}
              </Field>
            </div>
          )}
        </div>
      </Card>

      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
          <div className="text-xs text-muted">
            Do policzenia:{" "}
            <span className="font-medium text-text">
              {count(elementCount, "element", "elementy", "elementów")}
            </span>{" "}
            · budżet{" "}
            <span className="font-medium text-text">
              {money(Number(budget) || 0, currency)}
            </span>{" "}
            dziennie ·{" "}
            <span className="font-medium text-text">
              {startEnabled ? "start od razu" : "start wstrzymany"}
            </span>
          </div>
          <Button variant="primary" onClick={submit} disabled={busy}>
            {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Plus className="size-3.5" />}
            Policz plan
            <ArrowRight className="size-3.5" />
          </Button>
        </div>
      </Card>
    </div>
  );
}
