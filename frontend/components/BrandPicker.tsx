"use client";

import clsx from "clsx";
import { Check, Loader2, Search } from "lucide-react";
import { useState } from "react";
import { Button, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import { useData } from "@/lib/useData";

/**
 * Wyszukiwarka marek do wykluczenia.
 *
 * Amazon nie ma wyszukiwarki marek po nazwie - jedyne zrodlo to lista marek
 * obecnych w konkretnej kategorii. Dlatego panel trzyma wlasny slownik,
 * zbudowany z kategorii, w ktorych lezy asortyment konta, a tutaj tylko po
 * nim szuka.
 * Gdy slownik jest pusty (nowa instancja), przycisk dociaga go z Amazona -
 * to kilkanascie sekund, wiec nie robimy tego przy kazdym otwarciu.
 */
export function BrandPicker({
  profileId,
  wybrane,
  onWybierz,
}: {
  profileId: number | null;
  /** Identyfikatory marek juz dodanych - do oznaczenia "wybrana". */
  wybrane: Set<string>;
  onWybierz: (brandId: string, nazwa: string) => void;
}) {
  const [szukaj, setSzukaj] = useState("");
  const [otwarte, setOtwarte] = useState(false);
  const [pobieram, setPobieram] = useState(false);
  const [komunikat, setKomunikat] = useState<string | null>(null);

  const { data, loading, reload } = useData(
    ["brands", profileId, otwarte ? szukaj : null],
    () =>
      profileId && otwarte
        ? api.brands(profileId, szukaj || undefined)
        : Promise.resolve([]),
  );

  async function dociagnij() {
    if (!profileId) return;
    setPobieram(true);
    setKomunikat(null);
    try {
      const r = await api.refreshBrands(profileId);
      setKomunikat(
        r.brands
          ? `Dodano ${r.brands} marek z ${r.categories} kategorii.`
          : "Słownik był już kompletny.",
      );
      reload();
    } catch (err) {
      setKomunikat((err as Error).message.slice(0, 120));
    } finally {
      setPobieram(false);
    }
  }

  if (profileId === null) {
    return (
      <p className="text-xs text-muted">
        Wybierz konkretny rynek u góry, żeby przeglądać marki.
      </p>
    );
  }

  const marki = data ?? [];
  const pusto = otwarte && !loading && marki.length === 0;

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
          placeholder="Szukaj marki do wykluczenia"
          className="w-full bg-transparent text-sm outline-none placeholder:text-muted"
        />
      </div>

      {otwarte && (
        <div className="max-h-56 overflow-y-auto border-t border-border">
          {loading ? (
            <div className="p-2">
              <Skeleton rows={3} />
            </div>
          ) : pusto ? (
            <div className="flex flex-col items-start gap-2 p-3">
              <p className="text-xs text-muted">
                {szukaj
                  ? "Nie znalazłem takiej marki w słowniku."
                  : "Słownik marek jest pusty."}{" "}
                Marki pochodzą z kategorii, w których leży Twój asortyment —
                pobierz je, jeśli czegoś brakuje.
              </p>
              <Button size="sm" onClick={dociagnij} disabled={pobieram}>
                {pobieram && <Loader2 className="size-3.5 animate-spin" />}
                Pobierz marki z Amazona
              </Button>
              {komunikat && <p className="text-[11px] text-muted">{komunikat}</p>}
            </div>
          ) : (
            marki.map((m) => {
              const juz = wybrane.has(m.brand_id);
              return (
                <button
                  key={m.brand_id}
                  onClick={() => onWybierz(m.brand_id, m.name)}
                  disabled={juz}
                  className={clsx(
                    "flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-sm",
                    juz ? "text-muted" : "hover:bg-surface-2",
                  )}
                >
                  <span className="min-w-0 flex-1 truncate">
                    {m.name}
                    {m.category && (
                      <span className="ml-1.5 text-[11px] text-muted">{m.category}</span>
                    )}
                  </span>
                  {juz && <Check className="size-3.5 shrink-0 text-accent" />}
                </button>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}
