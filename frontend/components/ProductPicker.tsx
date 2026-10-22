"use client";

import clsx from "clsx";
import { Check, Search } from "lucide-react";
import { useState } from "react";
import { Badge, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import { useData } from "@/lib/useData";

/**
 * Wyszukiwarka produktow do formularza nowej kampanii.
 *
 * Wczesniej trzeba bylo wpisac SKU z pamieci, a `FBA-800-18-BIALY-5-NEW` nie
 * mowi nic nawet osobie, ktora ten kod nadala. Szukamy po tytule tak samo jak
 * po kodzie, bo uzytkownik pamieta jedno albo drugie - rzadko oba.
 *
 * Pole tekstowe zostaje zrodlem prawdy: wyszukiwarka tylko do niego dopisuje.
 * Dzieki temu wklejenie gotowej listy dziala jak dotad.
 */
export function ProductPicker({
  profileId,
  wybrane,
  onDodaj,
}: {
  profileId: number | null;
  /** Kody juz obecne w polu tekstowym - do oznaczenia "dodany". */
  wybrane: Set<string>;
  onDodaj: (kod: string) => void;
}) {
  const [szukaj, setSzukaj] = useState("");
  const [otwarte, setOtwarte] = useState(false);

  const { data, loading } = useData(
    ["catalog-search", profileId, otwarte ? szukaj : null],
    () =>
      profileId && otwarte
        ? api.catalogSearch(profileId, szukaj || undefined)
        : Promise.resolve([]),
  );

  if (profileId === null) {
    return (
      <p className="text-xs text-muted">
        Wybierz konkretny rynek u góry, żeby przeglądać produkty.
      </p>
    );
  }

  return (
    <div className="rounded-lg border border-border">
      <div className="flex items-center gap-2 px-2.5 py-2">
        <Search className="size-4 shrink-0 text-muted" />
        <input
          type="text"
          value={szukaj}
          onFocus={() => setOtwarte(true)}
          onChange={(e) => {
            setSzukaj(e.target.value);
            setOtwarte(true);
          }}
          placeholder="Szukaj po nazwie, SKU albo ASIN…"
          className="w-full bg-transparent text-sm outline-none placeholder:text-muted"
        />
      </div>

      {otwarte && (
        <div className="max-h-72 overflow-auto border-t border-border">
          {loading && !data ? (
            <div className="p-2">
              <Skeleton rows={4} />
            </div>
          ) : !data?.length ? (
            <p className="px-3 py-4 text-center text-xs text-muted">
              {szukaj
                ? "Nic nie pasuje. Produkt spoza tej listy możesz wpisać ręcznie niżej."
                : "Brak produktów na tym rynku."}
            </p>
          ) : (
            <ul className="divide-y divide-border">
              {data.map((p) => {
                const kod = p.sku ?? p.asin ?? "";
                const dodany = wybrane.has(kod);
                return (
                  <li key={`${p.sku}-${p.asin}`}>
                    <button
                      type="button"
                      onClick={() => !dodany && onDodaj(kod)}
                      disabled={dodany}
                      className={clsx(
                        "flex w-full items-center gap-2 px-2.5 py-2 text-left transition-colors",
                        dodany ? "opacity-55" : "hover:bg-surface-2",
                      )}
                    >
                      {p.image ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                          src={p.image}
                          alt=""
                          width={32}
                          height={32}
                          loading="lazy"
                          decoding="async"
                          className="size-8 shrink-0 rounded border border-border bg-surface-2 object-contain"
                        />
                      ) : (
                        <span className="size-8 shrink-0 rounded border border-border bg-surface-2" />
                      )}

                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-xs" title={p.title ?? ""}>
                          {p.title ?? "(bez tytułu w katalogu)"}
                        </span>
                        <span className="block truncate font-mono text-[11px] text-muted">
                          {p.sku}
                          {p.asin && ` · ${p.asin}`}
                        </span>
                      </span>

                      <span className="flex shrink-0 items-center gap-1.5">
                        {p.out_of_stock && <Badge tone="bad">brak stanu</Badge>}
                        {p.campaigns > 0 && (
                          <Badge tone="neutral">{p.campaigns} kamp.</Badge>
                        )}
                        {dodany && <Check className="size-4 text-good" />}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
