"use client";

import { useId } from "react";
import type { TimePoint } from "@/lib/api";

/**
 * Miniaturowy przebieg wartosci w komorce tabeli.
 *
 * Swiadomie bez osi, siatki i podpisow: w wierszu tabeli liczy sie ksztalt -
 * czy rosnie, czy spada, czy skacze - a dokladne wartosci sa w sasiednich
 * kolumnach.
 *
 * Rysujemy w jednostkach 0-100 i rozciagamy przez viewBox, wiec komponent nie
 * musi znac swojej szerokosci w pikselach. Cena tego rozciagania: ksztalty
 * inne niz linia sa zniekształcane w pionie, bo skala X i Y sa rozne (przy
 * 26 px wysokosci kolo zamienia sie w plaska kreske). Dlatego zadnych kropek
 * ani znacznikow - sama linia, ktorej grubosc trzyma `non-scaling-stroke`.
 * Skrajne wartosci dostaja margines, inaczej szczyt i dolek obcinaja sie
 * o krawedz kadru.
 */
export function Sparkline({
  points,
  tone = "var(--accent)",
  height = 26,
  label,
}: {
  points: number[];
  tone?: string;
  height?: number;
  label?: string;
}) {
  const gradientId = useId();

  if (points.length < 2) {
    return <span className="text-[10px] text-muted/60">—</span>;
  }

  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;

  const PAD = 6; // margines w jednostkach viewBox, zeby linia nie dotykala krawedzi
  const coords = points.map((v, i) => {
    const x = (i / (points.length - 1)) * 100;
    // 0 na gorze w SVG, wiec odwracamy - inaczej wykres bylby do gory nogami
    const y = 100 - PAD - ((v - min) / span) * (100 - 2 * PAD);
    return [x, y] as const;
  });

  const line = coords.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
  const area = `${line} L100,100 L0,100 Z`;

  return (
    <svg
      viewBox="0 0 100 100"
      preserveAspectRatio="none"
      height={height}
      className="w-full"
      role="img"
      aria-label={label ?? "przebieg w czasie"}
    >
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={tone} stopOpacity={0.25} />
          <stop offset="100%" stopColor={tone} stopOpacity={0} />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${gradientId})`} />
      <path
        d={line}
        fill="none"
        stroke={tone}
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

/** Wyciaga jedna metryke z szeregu, zastepujac braki zerem. */
export function seriesOf(points: TimePoint[] | undefined, key: keyof TimePoint): number[] {
  if (!points) return [];
  return points.map((p) => Number(p[key] ?? 0));
}
