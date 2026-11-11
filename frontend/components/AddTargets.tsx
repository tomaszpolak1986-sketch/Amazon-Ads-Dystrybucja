"use client";

import { Crosshair, X } from "lucide-react";
import { useMemo, useState } from "react";
import { BrandPicker } from "@/components/BrandPicker";
import { CategoryPicker } from "@/components/CategoryPicker";
import { Button } from "@/components/ui";
import type { AdGroupRow, TargetRow } from "@/lib/api";

/**
 * Dodawanie targetowania produktowego do grupy, ktora juz dziala.
 *
 * Dotad panel potrafil zalozyc targetowanie wylacznie razem z cala nowa
 * kampania. Dopisanie jednej marki do istniejacej grupy wymagalo wejscia do
 * konsoli Amazona - czyli dokladnie tego, czego ten panel ma oszczedzac.
 *
 * Kategoria jest obowiazkowa i to nie jest nasz wymysl: Amazon traktuje marke,
 * cene i ocene jako ZAWEZENIA kategorii, nie jako samodzielny cel. Wyrazenie
 * z sama marka konczy sie odmowa. Dlatego kategorie wybiera sie z tych, ktore
 * ta kampania juz targetuje - to naturalny zbior przy dokladaniu marki i nie
 * wymaga przeszukiwania 258 tysiecy kategorii Amazona.
 */

const OCENY = [
  ["", "bez zawężenia oceną"],
  ["4-5", "ocena 4-5"],
  ["3-5", "ocena 3-5"],
  ["1-5", "ocena 1-5"],
] as const;

export type NowyTarget = {
  campaignId: string;
  adGroupId: string;
  categoryId: string;
  categoryName?: string;
  brandId?: string;
  brandName?: string;
  rating?: string;
  priceFrom?: number;
  priceTo?: number;
  bid?: number;
};

export function AddTargets({
  campaignId,
  grupy,
  targety,
  canPlan,
  profileId,
  onTargets,
}: {
  campaignId: string;
  grupy: AdGroupRow[];
  /** Targety tej kampanii - zrodlo listy kategorii do wyboru. */
  targety: TargetRow[];
  canPlan: boolean;
  profileId: number | null;
  onTargets: (items: NowyTarget[]) => void;
}) {
  const [grupaId, setGrupaId] = useState("");
  const [kategoria, setKategoria] = useState<{ id: string; nazwa: string } | null>(null);
  const [marki, setMarki] = useState<Array<{ id: string; nazwa: string }>>([]);
  const [ocena, setOcena] = useState("");
  const [cenaOd, setCenaOd] = useState("");
  const [cenaDo, setCenaDo] = useState("");
  const [stawka, setStawka] = useState("");

  // Kategorie, ktore ta kampania juz targetuje. Nazwa pochodzi ze slownika
  // Amazona, wiec w selekcie stoi "Sockelleisten", a nie goly numer.
  const kategorie = useMemo(() => {
    const mapa = new Map<string, string>();
    for (const t of targety) {
      if (t.category_id) mapa.set(t.category_id, t.category_name || t.category_id);
    }
    return [...mapa.entries()].map(([id, nazwa]) => ({ id, nazwa }));
  }, [targety]);

  const grupa = grupaId || (grupy.length === 1 ? String(grupy[0].ad_group_id) : "");
  const gotowe = canPlan && !!grupa && !!kategoria && marki.length > 0;

  function wyslij() {
    if (!gotowe) return;
    if (!kategoria) return;
    const liczba = (s: string) => (s.trim() === "" ? undefined : Number(s));
    onTargets(
      marki.map((m) => ({
        campaignId,
        adGroupId: grupa,
        categoryId: kategoria.id,
        categoryName: kategoria.nazwa,
        brandId: m.id,
        brandName: m.nazwa,
        ...(ocena ? { rating: ocena } : {}),
        ...(liczba(cenaOd) !== undefined ? { priceFrom: liczba(cenaOd) } : {}),
        ...(liczba(cenaDo) !== undefined ? { priceTo: liczba(cenaDo) } : {}),
        ...(liczba(stawka) !== undefined ? { bid: liczba(stawka) } : {}),
      })),
    );
    setMarki([]);
  }

  return (
    <div className="mt-4 rounded-lg border border-border p-3">
      <div className="mb-2.5 flex items-center gap-2 text-sm font-medium">
        <Crosshair className="size-4 text-muted" />
        Dodaj targetowanie produktowe
      </div>

      {(
        <>
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={grupa}
              onChange={(e) => setGrupaId(e.target.value)}
              className="rounded-md border border-border bg-surface px-2 py-1.5 text-sm"
            >
              <option value="">Grupa reklam…</option>
              {grupy.map((g) => (
                <option key={g.ad_group_id} value={String(g.ad_group_id)}>
                  {g.name}
                </option>
              ))}
            </select>

            <select
              value={ocena}
              onChange={(e) => setOcena(e.target.value)}
              className="rounded-md border border-border bg-surface px-2 py-1.5 text-sm"
            >
              {OCENY.map(([v, label]) => (
                <option key={v} value={v}>
                  {label}
                </option>
              ))}
            </select>

            <input
              type="number"
              step="0.01"
              min="0"
              value={cenaOd}
              onChange={(e) => setCenaOd(e.target.value)}
              placeholder="cena od"
              className="w-24 rounded-md border border-border bg-surface px-2 py-1.5 text-sm"
            />
            <input
              type="number"
              step="0.01"
              min="0"
              value={cenaDo}
              onChange={(e) => setCenaDo(e.target.value)}
              placeholder="cena do"
              className="w-24 rounded-md border border-border bg-surface px-2 py-1.5 text-sm"
            />
            <input
              type="number"
              step="0.01"
              min="0.02"
              value={stawka}
              onChange={(e) => setStawka(e.target.value)}
              placeholder="stawka"
              className="w-24 rounded-md border border-border bg-surface px-2 py-1.5 text-sm"
              title="Pusta stawka oznacza stawkę domyślną grupy reklam"
            />
          </div>

          <div className="mt-2.5">
            <CategoryPicker
              profileId={profileId}
              wybrana={kategoria}
              sugerowane={kategorie}
              onWybierz={(id, nazwa) => setKategoria({ id, nazwa })}
            />
          </div>

          <div className="mt-2.5">
            <BrandPicker
              placeholder="Szukaj marki do targetowania"
              profileId={profileId}
              wybrane={new Set(marki.map((m) => m.id))}
              onWybierz={(brandId, nazwa) =>
                setMarki((p) =>
                  p.some((m) => m.id === brandId) ? p : [...p, { id: brandId, nazwa }],
                )
              }
            />
          </div>

          {marki.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {marki.map((m) => (
                <span
                  key={m.id}
                  className="inline-flex items-center gap-1 rounded-full bg-surface-2 px-2 py-0.5 text-xs"
                >
                  {m.nazwa}
                  <button
                    onClick={() => setMarki((p) => p.filter((x) => x.id !== m.id))}
                    className="text-muted hover:text-ink"
                    aria-label={`Usuń ${m.nazwa}`}
                  >
                    <X className="size-3" />
                  </button>
                </span>
              ))}
            </div>
          )}

          <div className="mt-3 flex items-center gap-3">
            <Button variant="primary" size="sm" onClick={wyslij} disabled={!gotowe}>
              Policz zmianę
            </Button>
            <span className="text-xs text-muted">
              {!kategoria
                ? "Zacznij od kategorii - Amazon traktuje markę jako jej zawężenie."
                : marki.length === 0
                  ? "Wybierz markę, na którą chcesz się reklamować."
                  : `${marki.length} nowych targetów w kategorii ${kategoria.nazwa} - trafią do kolejki, nie do Amazona.`}
            </span>
          </div>
        </>
      )}
    </div>
  );
}
