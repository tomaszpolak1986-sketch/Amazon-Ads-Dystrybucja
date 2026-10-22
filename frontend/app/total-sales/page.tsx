"use client";

import { AlertTriangle, Check, FileUp, Info, Loader2, Trash2 } from "lucide-react";
import { useState } from "react";
import { Badge, Button, Card, ErrorState, Skeleton } from "@/components/ui";
import {
  api,
  type ManualSalesEntry,
  type SalesImportResult,
  type SalesPreview,
} from "@/lib/api";
import { useFilters } from "@/lib/filters";
import { count, money } from "@/lib/format";
import { useData } from "@/lib/useData";

const inputClass =
  "mt-1 w-full rounded-lg border border-border bg-bg px-2.5 py-1.5 text-sm outline-none focus:border-accent";

/** Ostatnie dni do ręcznego wpisania — od wczoraj wstecz. */
function lastDays(n: number): string[] {
  const out: string[] = [];
  const d = new Date();
  d.setDate(d.getDate() - 1);
  for (let i = 0; i < n; i++) {
    out.push(d.toISOString().slice(0, 10));
    d.setDate(d.getDate() - 1);
  }
  return out;
}

export default function TotalSalesPage() {
  const { profileId, currency, revision, bump } = useFilters();

  const coverage = useData(["sales-coverage", profileId, revision], () =>
    api.salesCoverage(profileId),
  );

  const [fileName, setFileName] = useState<string | null>(null);
  const [content, setContent] = useState("");
  const [preview, setPreview] = useState<SalesPreview | null>(null);
  const [mapping, setMapping] = useState<Record<string, string | null>>({});
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<SalesImportResult | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const [manual, setManual] = useState<Record<string, string>>({});

  async function onFile(file: File) {
    setProblem(null);
    setResult(null);
    setFileName(file.name);
    const text = await file.text();
    setContent(text);
    setBusy(true);
    try {
      const p = await api.salesPreview(text);
      setPreview(p);
      setMapping(p.mapping);
    } catch (err) {
      setProblem((err as Error).message.slice(0, 300));
      setPreview(null);
    } finally {
      setBusy(false);
    }
  }

  async function doImport() {
    if (profileId === null || !preview) return;
    setBusy(true);
    setProblem(null);
    try {
      const r = await api.salesImport({
        profile_id: profileId,
        content,
        mapping,
        delimiter: preview.delimiter,
      });
      setResult(r);
      setPreview(null);
      setContent("");
      setFileName(null);
      coverage.reload();
      bump();
    } catch (err) {
      setProblem((err as Error).message.slice(0, 300));
    } finally {
      setBusy(false);
    }
  }

  async function saveManual() {
    if (profileId === null) return;
    const entries: ManualSalesEntry[] = Object.entries(manual)
      .filter(([, v]) => v.trim() !== "")
      .map(([date, v]) => ({ date, sales: Number(v.replace(",", ".")) }))
      .filter((e) => Number.isFinite(e.sales));

    if (!entries.length) {
      setProblem("Nie wpisałeś żadnej kwoty.");
      return;
    }

    setBusy(true);
    setProblem(null);
    try {
      const r = await api.salesManual(profileId, entries);
      setResult(r);
      setManual({});
      coverage.reload();
      bump();
    } catch (err) {
      setProblem((err as Error).message.slice(0, 300));
    } finally {
      setBusy(false);
    }
  }

  async function removeRange(start: string, end: string) {
    if (profileId === null) return;
    setBusy(true);
    try {
      await api.salesDelete(profileId, start, end);
      coverage.reload();
      bump();
    } catch (err) {
      setProblem((err as Error).message.slice(0, 300));
    } finally {
      setBusy(false);
    }
  }

  if (coverage.error) return <ErrorState message={coverage.error} />;

  const rows = coverage.data ?? [];
  const mine = profileId ? rows.filter((r) => r.profile_id === profileId) : rows;

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-2 rounded-xl border border-border bg-surface px-4 py-3">
        <Info className="mt-0.5 size-4 shrink-0 text-accent" />
        <div className="min-w-0 text-xs">
          <div className="text-sm font-medium">Po co to jest</div>
          <p className="mt-0.5 text-muted">
            TACOS to wydatek na reklamę podzielony przez <strong>cały</strong> przychód —
            reklamowy i organiczny razem. Ads API zna wyłącznie sprzedaż przypisaną do
            reklam, więc brakującą część trzeba dostarczyć stąd: z raportu biznesowego
            Seller Central albo wpisując kwoty ręcznie. Bez tego panel pokazuje ACOS,
            ale przy TACOS postawi kreskę — zamiast liczby, która wygląda wiarygodnie
            i jest nieprawdziwa.
          </p>
        </div>
      </div>

      {problem && (
        <div className="flex items-start gap-2 rounded-lg bg-bad-soft px-4 py-2.5 text-sm text-bad">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          <span>{problem}</span>
        </div>
      )}

      {result && (
        <div className="flex items-start gap-2 rounded-lg bg-good-soft px-4 py-2.5 text-sm text-good">
          <Check className="mt-0.5 size-4 shrink-0" />
          <span>
            Zapisano {count(result.stored, "dzień", "dni", "dni")} ({result.start} →{" "}
            {result.end})
            {result.total_sales != null && ` · ${money(result.total_sales, currency)}`}
            {result.replaced > 0 &&
              ` · nadpisano ${count(result.replaced, "wcześniejszy wiersz", "wcześniejsze wiersze", "wcześniejszych wierszy")}`}
            {(result.skipped_no_date ?? 0) + (result.skipped_no_amount ?? 0) > 0 &&
              ` · pominięto ${(result.skipped_no_date ?? 0) + (result.skipped_no_amount ?? 0)} wierszy bez daty lub kwoty`}
          </span>
        </div>
      )}

      {profileId === null ? (
        <Card>
          <div className="flex flex-col items-center gap-1 px-6 py-14 text-center">
            <p className="text-sm font-medium">Wybierz rynek</p>
            <p className="max-w-md text-xs text-muted">
              Sprzedaż całkowitą importuje się osobno dla każdego rynku — kwoty są
              w jego walucie.
            </p>
          </div>
        </Card>
      ) : (
        <>
          <Card
            title="Import z raportu biznesowego"
            subtitle="Seller Central → Raporty → Raporty biznesowe → pobierz CSV. Nazwy kolumn mogą być w języku rynku — rozpoznaję je i pokazuję do sprawdzenia."
          >
            <div className="space-y-3 px-4 py-3">
              <label className="flex cursor-pointer items-center gap-3 rounded-lg border border-dashed border-border px-4 py-6 transition-colors hover:border-accent hover:bg-surface-2">
                <FileUp className="size-5 text-muted" />
                <div className="text-xs">
                  <div className="text-sm font-medium">
                    {fileName ?? "Wybierz plik CSV"}
                  </div>
                  <div className="mt-0.5 text-muted">
                    Plik jest czytany w przeglądarce i wysyłany jako tekst.
                  </div>
                </div>
                <input
                  type="file"
                  accept=".csv,.txt,text/csv"
                  className="hidden"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) void onFile(f);
                  }}
                />
              </label>

              {preview && (
                <>
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <Badge tone="neutral">separator „{preview.delimiter}”</Badge>
                    <Badge tone="neutral">
                      {count(preview.headers.length, "kolumna", "kolumny", "kolumn")}
                    </Badge>
                    {preview.missing_required.length > 0 && (
                      <Badge tone="warn">
                        brakuje: {preview.missing_required.join(", ")}
                      </Badge>
                    )}
                  </div>

                  <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                    {preview.fields.map((f) => (
                      <label key={f.key} className="block">
                        <span className="text-xs font-medium">
                          {f.label}
                          {f.required && <span className="text-bad"> *</span>}
                        </span>
                        <select
                          className={inputClass}
                          value={mapping[f.key] ?? ""}
                          onChange={(e) =>
                            setMapping((m) => ({
                              ...m,
                              [f.key]: e.target.value || null,
                            }))
                          }
                        >
                          <option value="">— pomiń —</option>
                          {preview.headers.map((h) => (
                            <option key={h} value={h}>
                              {h}
                            </option>
                          ))}
                        </select>
                      </label>
                    ))}
                  </div>

                  {preview.sample.length > 0 && (
                    <div className="overflow-x-auto rounded-lg border border-border">
                      <table className="w-full text-xs">
                        <thead className="bg-surface-2">
                          <tr className="text-muted">
                            {preview.headers.map((h) => (
                              <th key={h} className="px-2.5 py-1.5 text-left font-medium">
                                {h}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {preview.sample.map((row, i) => (
                            <tr key={i} className="border-t border-border/60">
                              {preview.headers.map((h) => (
                                <td key={h} className="whitespace-nowrap px-2.5 py-1.5">
                                  {row[h]}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}

                  <div className="flex justify-end">
                    <Button
                      variant="primary"
                      onClick={doImport}
                      disabled={busy || !mapping.date || !mapping.sales}
                    >
                      {busy && <Loader2 className="size-3.5 animate-spin" />}
                      Wczytaj sprzedaż
                    </Button>
                  </div>
                </>
              )}
            </div>
          </Card>

          <Card
            title="Albo wpisz ręcznie"
            subtitle="Kwoty dzienne za cały rynek. Przydaje się, gdy nie chcesz eksportować raportu — wystarczy do TACOS na poziomie rynku."
          >
            <div className="space-y-3 px-4 py-3">
              <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-7">
                {lastDays(7).map((d) => (
                  <label key={d} className="block">
                    <span className="text-[11px] font-medium text-muted">{d.slice(5)}</span>
                    <input
                      className={inputClass}
                      inputMode="decimal"
                      placeholder="0,00"
                      value={manual[d] ?? ""}
                      onChange={(e) =>
                        setManual((m) => ({ ...m, [d]: e.target.value }))
                      }
                    />
                  </label>
                ))}
              </div>
              <div className="flex justify-end">
                <Button onClick={saveManual} disabled={busy}>
                  {busy && <Loader2 className="size-3.5 animate-spin" />}
                  Zapisz kwoty
                </Button>
              </div>
            </div>
          </Card>
        </>
      )}

      <Card title="Co już mamy" subtitle="Bez tego nie wiadomo, z jakiego okresu policzył się TACOS">
        {coverage.loading && !coverage.data ? (
          <Skeleton rows={3} />
        ) : mine.length === 0 ? (
          <div className="flex flex-col items-center gap-1 px-6 py-10 text-center">
            <p className="text-sm font-medium">Jeszcze nic nie zaimportowano</p>
            <p className="max-w-md text-xs text-muted">
              Dopóki tu pusto, panel przy TACOS pokazuje kreskę.
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-surface-2 text-xs text-muted">
                <tr>
                  <th className="px-4 py-2 text-left font-semibold">Okres</th>
                  <th className="px-4 py-2 text-right font-semibold">Dni</th>
                  <th className="px-4 py-2 text-right font-semibold">ASIN-y</th>
                  <th className="px-4 py-2 text-right font-semibold">Sprzedaż</th>
                  <th className="px-4 py-2" />
                </tr>
              </thead>
              <tbody>
                {mine.map((r) => (
                  <tr key={r.profile_id} className="border-t border-border/60">
                    <td className="px-4 py-2 text-xs">
                      {r.start} → {r.end}
                    </td>
                    <td className="tnum px-4 py-2 text-right">{r.days}</td>
                    <td className="tnum px-4 py-2 text-right text-muted">
                      {r.asins || "—"}
                    </td>
                    <td className="tnum px-4 py-2 text-right font-medium">
                      {money(r.sales, currency)}
                    </td>
                    <td className="px-4 py-2 text-right">
                      {r.start && r.end && (
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={busy}
                          onClick={() => removeRange(r.start!, r.end!)}
                        >
                          <Trash2 className="size-3.5" /> Usuń
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
