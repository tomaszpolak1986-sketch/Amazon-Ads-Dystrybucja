"use client";

import { PackagePlus, X } from "lucide-react";
import { DataTable } from "@/components/DataTable";
import { ProductPicker } from "@/components/ProductPicker";
import { Button } from "@/components/ui";
import type { AdGroupRow, ProductAdRow } from "@/lib/api";
import { count } from "@/lib/format";

/**
 * Produkty reklamowane w kampanii - jedna zakladka obok grup i targetowania.
 *
 * Tabela grup pokazuje wynik zbiorczy, z ktorego nie widac skladu grupy.
 * A to od skladu zalezy, czy slaby wynik da sie poprawic stawka, czy trzeba
 * wyjac produkt, ktory ciagnie reszte w dol.
 */

// ASIN ma staly ksztalt, wiec rozdzielamy wpisy po nim - tak samo jak
// w formularzu nowej kampanii.
const ASIN = /^B[0-9A-Z]{9}$/;

export function ProductsTab({
  rows,
  columns,
  grupy,
  selected,
  onSelectedChange,
  grupaDocelowa,
  onGrupaChange,
  doDodania,
  onDoDodaniaChange,
  profileId,
  onPlan,
}: {
  rows: ProductAdRow[];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  columns: any[];
  grupy: AdGroupRow[];
  selected: Set<string>;
  onSelectedChange: (next: Set<string>) => void;
  grupaDocelowa: string;
  onGrupaChange: (id: string) => void;
  doDodania: string[];
  onDoDodaniaChange: (kody: string[]) => void;
  profileId: number | null;
  onPlan: (adGroupId: string, skus: string[], asins: string[]) => void;
}) {
  // Grupa domyslna: pierwsza z listy, zeby nie wymagac wyboru przy jednej grupie
  const grupaId = grupaDocelowa || grupy[0]?.ad_group_id || "";
  const grupa = grupy.find((g) => g.ad_group_id === grupaId);

  const skus = doDodania.filter((k) => !ASIN.test(k.toUpperCase()));
  const asins = doDodania.filter((k) => ASIN.test(k.toUpperCase()));

  // Co juz jest w tej grupie - zeby wyszukiwarka oznaczyla to jako dodane
  const wGrupie = new Set<string>([
    ...doDodania,
    ...rows
      .filter((r) => r.ad_group_id === grupaId)
      .flatMap((r) => [r.sku, r.asin].filter(Boolean) as string[]),
  ]);

  const brakRynku = profileId === null;

  return (
    <div className="space-y-3">
      <DataTable
        data={rows}
        columns={columns}
        getRowId={(r) => r.ad_id}
        initialSort={[{ id: "cost", desc: true }]}
        selected={selected}
        onSelectedChange={onSelectedChange}
        emptyLabel="Ta kampania nie ma reklam produktowych"
        emptyHint="Kampania bez produktów nie wyświetli się, nawet gdy jest aktywna."
        maxHeight="calc(100vh - 38rem)"
      />

      <div className="rounded-xl border border-border bg-surface p-3">
        <div className="flex items-center gap-1.5 text-sm font-medium">
          <PackagePlus className="size-4 text-accent" />
          Dodaj produkty do grupy
        </div>

        <div className="mt-2 space-y-2">
          <label className="flex flex-wrap items-center gap-2 text-xs">
            <span className="text-muted">Grupa docelowa</span>
            <select
              value={grupaId}
              onChange={(e) => onGrupaChange(e.target.value)}
              disabled={!grupy.length}
              className="min-w-56 rounded-lg border border-border bg-bg px-2 py-1 outline-none focus:border-accent disabled:opacity-40"
            >
              {grupy.map((g) => (
                <option key={g.ad_group_id} value={g.ad_group_id}>
                  {g.name ?? g.ad_group_id}
                  {g.state === "PAUSED" ? " (wstrzymana)" : ""}
                </option>
              ))}
            </select>
            {grupa?.state === "PAUSED" && (
              <span className="text-warn">
                Grupa jest wstrzymana — produkty nie zaczną się wyświetlać, dopóki
                jej nie wznowisz.
              </span>
            )}
          </label>

          <ProductPicker
            profileId={profileId}
            wybrane={wGrupie}
            onDodaj={(kod) =>
              onDoDodaniaChange(doDodania.includes(kod) ? doDodania : [...doDodania, kod])
            }
          />

          {doDodania.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5">
              {doDodania.map((k) => (
                <button
                  key={k}
                  onClick={() => onDoDodaniaChange(doDodania.filter((x) => x !== k))}
                  title="Usuń z listy"
                  className="inline-flex items-center gap-1 rounded bg-accent-soft px-1.5 py-0.5 font-mono text-[11px] text-accent hover:opacity-80"
                >
                  {k}
                  <X className="size-3" />
                </button>
              ))}
            </div>
          )}

          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-[11px] text-muted">
              {brakRynku
                ? "Wybierz konkretny rynek, żeby dodawać produkty."
                : "Trafi do zatwierdzenia. W grupie automatycznej nowy produkt zacznie wydawać od razu, po stawce domyślnej grupy."}
            </p>
            <Button
              size="sm"
              variant="primary"
              disabled={brakRynku || !grupaId || !doDodania.length}
              onClick={() => onPlan(grupaId, skus, asins)}
            >
              {doDodania.length
                ? `Wyślij ${count(doDodania.length, "produkt", "produkty", "produktów")}`
                : "Wyślij do zatwierdzenia"}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
