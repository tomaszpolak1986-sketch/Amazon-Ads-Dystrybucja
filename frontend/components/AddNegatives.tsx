"use client";

import clsx from "clsx";
import { Ban, X } from "lucide-react";
import { useState } from "react";
import { BrandPicker } from "@/components/BrandPicker";
import { Button } from "@/components/ui";
import type { AdGroupRow } from "@/lib/api";
import { count } from "@/lib/format";

/**
 * Dodawanie wykluczen wprost z zakladki kampanii.
 *
 * Wczesniej wykluczenia dalo sie dodac tylko z Analizy albo z alertu, wiec
 * przy liscie, ktora ma juz kilkaset pozycji, nie bylo jak dopisac kolejnej
 * frazy bez opuszczania ekranu.
 *
 * Amazon trzyma dwa rozne rodzaje wykluczen i to nie jest kosmetyka:
 * frazy siedza na poziomie kampanii albo grupy, a wykluczenia produktowe
 * - pojedyncze ASIN-y i cale marki - **tylko** na poziomie grupy. Dlatego
 * wpisy rozdzielamy po ksztalcie i mowimy o tym wprost, zamiast pozwolic
 * Amazonowi odrzucic polowe zadania.
 *
 * Marki maja wlasna wyszukiwarke, bo Amazon przyjmuje je jako liczbowy
 * identyfikator, nie nazwe - z wpisanego "Bosch" nie da sie zrobic zadania.
 */

const ASIN = /^B[0-9A-Z]{9}$/i;

type Poziom = "CAMPAIGN" | "AD_GROUP";

export function AddNegatives({
  campaignId,
  grupy,
  canPlan,
  onNegatives,
  onNegativeAsins,
  onNegativeBrands,
  profileId,
}: {
  campaignId: string;
  grupy: AdGroupRow[];
  canPlan: boolean;
  onNegatives: (
    items: Array<{
      campaignId: string;
      adGroupId?: string;
      keywordText: string;
      matchType: string;
    }>,
    level: Poziom,
  ) => void;
  onNegativeAsins: (
    items: Array<{ campaignId: string; adGroupId: string; asin: string }>,
  ) => void;
  onNegativeBrands: (
    brands: Array<{ campaignId: string; adGroupId: string; brandId: string; brandName?: string }>,
  ) => void;
  profileId: number | null;
}) {
  const [tekst, setTekst] = useState("");
  const [dopasowanie, setDopasowanie] = useState("NEGATIVE_PHRASE");
  const [poziom, setPoziom] = useState<Poziom>("AD_GROUP");
  const [grupaId, setGrupaId] = useState("");
  // Marki wybrane z wyszukiwarki, jeszcze niewyslane
  const [marki, setMarki] = useState<Array<{ id: string; nazwa: string }>>([]);

  const wpisy = tekst
    .split(String.fromCharCode(10))
    .map((w) => w.trim())
    .filter(Boolean);
  const asiny = wpisy.filter((w) => ASIN.test(w));
  const frazy = wpisy.filter((w) => !ASIN.test(w));

  const grupa = grupaId || grupy[0]?.ad_group_id || "";
  // ASIN-y i marki wymagaja grupy zawsze; frazy tylko przy poziomie grupy.
  const brakGrupy =
    (asiny.length > 0 || marki.length > 0 || poziom === "AD_GROUP") && !grupa;
  const pozycji = wpisy.length + marki.length;

  function wyslij() {
    if (frazy.length) {
      onNegatives(
        frazy.map((f) => ({
          campaignId,
          adGroupId: poziom === "AD_GROUP" ? grupa : undefined,
          keywordText: f,
          matchType: dopasowanie,
        })),
        poziom,
      );
    }
    if (asiny.length) {
      onNegativeAsins(
        asiny.map((a) => ({ campaignId, adGroupId: grupa, asin: a.toUpperCase() })),
      );
    }
    if (marki.length) {
      onNegativeBrands(
        marki.map((m) => ({
          campaignId,
          adGroupId: grupa,
          brandId: m.id,
          brandName: m.nazwa,
        })),
      );
    }
    setTekst("");
    setMarki([]);
  }

  return (
    <div className="mt-3 rounded-xl border border-border bg-surface p-3">
      <div className="flex items-center gap-1.5 text-sm font-medium">
        <Ban className="size-4 text-accent" />
        Dodaj wykluczenia
      </div>

      <div className="mt-2 space-y-2">
        <textarea
          value={tekst}
          onChange={(e) => setTekst(e.target.value)}
          disabled={!canPlan}
          rows={4}
          placeholder={`fraza do wykluczenia${String.fromCharCode(10)}druga fraza${String.fromCharCode(10)}B0XXXXXXXX`}
          className="w-full rounded-lg border border-border bg-bg px-2 py-1.5 font-mono text-xs outline-none focus:border-accent disabled:opacity-40"
        />
        <p className="text-[11px] text-muted">
          Po jednym w wierszu. ASIN-y rozpoznaję po formacie i dodaję jako wykluczenia
          produktowe — te istnieją tylko na poziomie grupy reklam.
        </p>

        <div className="space-y-1.5 border-t border-border pt-2">
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-xs font-medium">Wyklucz markę</span>
            <span className="text-[11px] text-muted">
              Jedna pozycja zamiast dziesiątek ASIN-ów — obejmie też nowe produkty tej marki.
            </span>
          </div>
          <BrandPicker
            profileId={profileId}
            wybrane={new Set(marki.map((m) => m.id))}
            onWybierz={(id, nazwa) =>
              setMarki((b) => (b.some((x) => x.id === id) ? b : [...b, { id, nazwa }]))
            }
          />
          {marki.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5">
              {marki.map((m) => (
                <button
                  key={m.id}
                  onClick={() => setMarki((b) => b.filter((x) => x.id !== m.id))}
                  title="Usuń z listy"
                  className="inline-flex items-center gap-1 rounded bg-accent-soft px-1.5 py-0.5 text-[11px] text-accent hover:opacity-80"
                >
                  {m.nazwa}
                  <X className="size-3" />
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-3 text-xs">
          <label className="flex items-center gap-1.5">
            <span className="text-muted">Dopasowanie</span>
            <select
              value={dopasowanie}
              onChange={(e) => setDopasowanie(e.target.value)}
              disabled={!canPlan}
              className="rounded-lg border border-border bg-bg px-2 py-1 outline-none focus:border-accent disabled:opacity-40"
            >
              <option value="NEGATIVE_PHRASE">frazowe</option>
              <option value="NEGATIVE_EXACT">dokładne</option>
            </select>
          </label>

          <div className="flex items-center gap-1.5">
            <span className="text-muted">Poziom</span>
            <div className="flex gap-0.5 rounded-lg border border-border p-0.5">
              {(
                [
                  ["AD_GROUP", "grupa reklam"],
                  ["CAMPAIGN", "cała kampania"],
                ] as Array<[Poziom, string]>
              ).map(([w, etykieta]) => (
                <button
                  key={w}
                  onClick={() => setPoziom(w)}
                  disabled={!canPlan}
                  className={clsx(
                    "rounded-md px-2 py-0.5 font-medium transition-colors disabled:opacity-40",
                    poziom === w ? "bg-accent text-white" : "text-muted hover:text-text",
                  )}
                >
                  {etykieta}
                </button>
              ))}
            </div>
          </div>

          {(poziom === "AD_GROUP" || asiny.length > 0 || marki.length > 0) && (
            <label className="flex items-center gap-1.5">
              <span className="text-muted">Grupa</span>
              <select
                value={grupa}
                onChange={(e) => setGrupaId(e.target.value)}
                disabled={!canPlan || !grupy.length}
                className="min-w-48 rounded-lg border border-border bg-bg px-2 py-1 outline-none focus:border-accent disabled:opacity-40"
              >
                {grupy.map((g) => (
                  <option key={g.ad_group_id} value={g.ad_group_id}>
                    {g.name ?? g.ad_group_id}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-2">
          <p className="text-[11px] text-muted">
            {!canPlan
              ? "Wybierz konkretny rynek, żeby dodawać wykluczenia."
              : brakGrupy
                ? "Wybierz grupę reklam."
                : pozycji
                  ? [
                      frazy.length &&
                        `${count(frazy.length, "fraza", "frazy", "fraz")} (${poziom === "CAMPAIGN" ? "kampania" : "grupa"})`,
                      asiny.length &&
                        `${count(asiny.length, "ASIN", "ASIN-y", "ASIN-ów")} (grupa)`,
                      marki.length &&
                        `${count(marki.length, "marka", "marki", "marek")} (grupa)`,
                    ]
                      .filter(Boolean)
                      .join(" · ") + " — trafi do zatwierdzenia."
                  : "Wpisz frazy, ASIN-y albo wybierz markę do wykluczenia."}
          </p>
          <Button
            size="sm"
            variant="primary"
            disabled={!canPlan || !pozycji || brakGrupy}
            onClick={wyslij}
          >
            Wyślij do zatwierdzenia
          </Button>
        </div>
      </div>
    </div>
  );
}
