"use client";

import Link from "next/link";
import { Card } from "@/components/ui";
import type { EffectivenessResult } from "@/lib/api";

/**
 * Rozliczenie panelu z wlasnych rekomendacji.
 *
 * Panel konczyl prace w chwili zatwierdzenia: zmiana szla do Amazona i znikala
 * z widoku. Skutki mierzyl modul skutecznosci, ale wylacznie na osobnym
 * ekranie, do ktorego nikt nie zaglada - wiec kazda kolejna decyzja byla tak
 * samo niepewna jak pierwsza.
 *
 * Dwa zastrzezenia sa czescia tresci, nie drobnym drukiem:
 *
 * 1. To nastepstwo w czasie, nie dowod przyczyny. Rownolegle zmienia sie
 *    sezon, konkurencja i ceny. Dlatego wszedzie pada "po zmianie", a nigdy
 *    "dzieki zmianie".
 * 2. Wynik zalezy od kierunku: podniesienie stawki ma z definicji podniesc
 *    ACOS, bo dokupuje ruch drozej. Ocena bierze to pod uwage, inaczej kazda
 *    decyzja o wzroscie wygladalaby na blad.
 */
export function Scorecard({
  data,
  loading,
}: {
  data?: EffectivenessResult | null;
  loading: boolean;
}) {
  const s = data?.scorecard;
  if (loading && !data) return null;
  if (!s || !s.measured) return null;

  const pozycje = [
    { etykieta: "lepiej po zmianie", ile: s.better, klasa: "text-good" },
    { etykieta: "gorzej po zmianie", ile: s.worse, klasa: "text-bad" },
    { etykieta: "bez wyraźnej różnicy", ile: s.flat, klasa: "text-muted" },
  ];

  return (
    <Card
      title="Jak wypadły Twoje zmiany"
      subtitle={`${s.measured} zmian z pełnym pomiarem - ${data?.window_days} dni przed i po, co najmniej ${data?.min_days_after} dni po zmianie`}
    >
      <div className="flex flex-wrap items-end gap-6">
        {pozycje.map((p) => (
          <div key={p.etykieta}>
            <div className={`tnum text-2xl font-semibold ${p.klasa}`}>{p.ile}</div>
            <div className="mt-0.5 text-xs text-muted">{p.etykieta}</div>
          </div>
        ))}
        {!!s.waiting && (
          <div>
            <div className="tnum text-2xl font-semibold text-muted">{s.waiting}</div>
            <div className="mt-0.5 text-xs text-muted">czeka na pomiar</div>
          </div>
        )}
      </div>

      <p className="mt-3 text-xs leading-relaxed text-muted">
        To następstwo w czasie, nie dowód przyczyny - równolegle zmienia się sezon,
        konkurencja i ceny. Różnice poniżej {s.noise_band}% liczymy jako brak zmiany.
        Ocena zależy od kierunku: podniesienie stawki ma podnieść ACOS, bo dokupuje
        ruch drożej, więc nie jest za to karane.{" "}
        <Link href="/changes" className="text-accent hover:underline">
          Zobacz zmianę po zmianie
        </Link>
        .
      </p>
    </Card>
  );
}
