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
  if (!s || !s.measured) {
    // Bez pomiaru kafelek kiedys znikal calkiem - i nikt nie wiedzial, ze
    // istnieje ani kiedy sie pojawi. Teraz mowi wprost, na co czeka.
    const czeka = data?.waiting_total ?? data?.waiting?.length ?? 0;
    const najblizsza = data?.waiting?.length
      ? Math.min(...data.waiting.map((w) => w.days_left))
      : null;
    const poDniach = data?.min_days_after ?? 7;
    return (
      <Card title="Jak wypadły Twoje zmiany">
        <p className="text-sm text-muted">
          {czeka > 0 ? (
            <>
              <span className="tnum font-semibold text-text">{czeka}</span>{" "}
              {czeka === 1 ? "zmiana czeka" : "zmian czeka"} na pomiar.{" "}
              {najblizsza !== null &&
                (najblizsza <= 0
                  ? "Pierwszy wynik pojawi się po najbliższym pobraniu danych."
                  : `Pierwszy wynik za ${najblizsza} ${najblizsza === 1 ? "dzień" : "dni"}.`)}
            </>
          ) : (
            <>
              Nie ma jeszcze zatwierdzonych zmian do oceny. Gdy zatwierdzisz zmianę w{" "}
              <Link href="/approvals" className="text-accent hover:underline">
                Do zatwierdzenia
              </Link>
              , panel porówna wyniki przed i po niej – pierwsza ocena pojawi się tutaj{" "}
              {poDniach} dni po wysłaniu zmiany do Amazona.
            </>
          )}
        </p>
      </Card>
    );
  }

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
