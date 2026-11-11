"use client";

import clsx from "clsx";
import { Check, Search } from "lucide-react";
import { useState } from "react";
import { Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import { useData } from "@/lib/useData";

/**
 * Wyszukiwarka kategorii Amazona do targetowania produktowego.
 *
 * Slownik rynku ma kilkadziesiat tysiecy pozycji (DE: 32 858), wiec lista bez
 * szukania nie ma sensu - zaczyna sie od "(X)HTML" i ".NET". Dopoki pole jest
 * puste, pokazujemy wiec `sugerowane`: kategorie, ktore ta kampania juz
 * targetuje. To pokrywa najczestszy przypadek (dolozenie marki do kategorii,
 * w ktorej juz jestesmy), a wpisanie fragmentu nazwy otwiera caly slownik.
 *
 * Kategoria nadrzedna jest pokazana obok nazwy, bo Amazon ma kilka galezi
 * o identycznej nazwie liscia - bez rodzica nie da sie ich rozroznic.
 */
export function CategoryPicker({
  profileId,
  wybrana,
  sugerowane,
  onWybierz,
}: {
  profileId: number | null;
  wybrana: { id: string; nazwa: string } | null;
  /** Kategorie juz targetowane w tej kampanii - propozycja na start. */
  sugerowane: Array<{ id: string; nazwa: string }>;
  onWybierz: (categoryId: string, nazwa: string) => void;
}) {
  const [szukaj, setSzukaj] = useState("");
  const [otwarte, setOtwarte] = useState(false);

  const fraza = szukaj.trim();
  const { data, loading } = useData(
    ["categories", profileId, otwarte && fraza.length >= 2 ? fraza : null],
    () =>
      profileId && otwarte && fraza.length >= 2
        ? api.categories(profileId, fraza)
        : Promise.resolve([]),
  );

  if (profileId === null) {
    return (
      <p className="text-xs text-muted">
        Wybierz konkretny rynek u góry, żeby przeglądać kategorie.
      </p>
    );
  }

  const znalezione = (data ?? []).map((k) => ({
    id: k.category_id,
    nazwa: k.name,
    rodzic: k.parent,
  }));
  const lista =
    fraza.length >= 2
      ? znalezione
      : sugerowane.map((k) => ({ ...k, rodzic: "w tej kampanii" }));

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
          placeholder={
            wybrana ? `Kategoria: ${wybrana.nazwa}` : "Szukaj kategorii do targetowania"
          }
          className="w-full bg-transparent text-sm outline-none placeholder:text-muted"
        />
      </div>

      {otwarte && (
        <div className="max-h-56 overflow-y-auto border-t border-border">
          {loading ? (
            <div className="p-2">
              <Skeleton rows={3} />
            </div>
          ) : lista.length === 0 ? (
            <p className="p-3 text-xs text-muted">
              {fraza.length >= 2
                ? "Nie znalazłem takiej kategorii w słowniku tego rynku."
                : "Wpisz co najmniej dwa znaki - słownik ma dziesiątki tysięcy kategorii."}
            </p>
          ) : (
            lista.map((k) => {
              const juz = wybrana?.id === k.id;
              return (
                <button
                  key={k.id}
                  onClick={() => {
                    onWybierz(k.id, k.nazwa);
                    setOtwarte(false);
                    setSzukaj("");
                  }}
                  className={clsx(
                    "flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-sm",
                    juz ? "text-muted" : "hover:bg-surface-2",
                  )}
                >
                  <span className="min-w-0 flex-1 truncate">
                    {k.nazwa}
                    {k.rodzic && (
                      <span className="ml-1.5 text-[11px] text-muted">{k.rodzic}</span>
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
