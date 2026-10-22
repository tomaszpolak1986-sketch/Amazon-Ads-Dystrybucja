"use client";

/* eslint-disable @typescript-eslint/no-explicit-any */

import { acosTone, money, num, pct, ratio } from "@/lib/format";

/**
 * Kolumny metryk sa identyczne na wszystkich ekranach, wiec definiujemy je raz.
 * Uzywamy zwyklych obiektow columnDef zamiast createColumnHelper - helper daje
 * tylko wnioskowanie typow, a tabela i tak przyjmuje luzno typowane definicje.
 */

const right = { align: "right" as const };

export interface MetricColumnOptions {
  currency: string;
  /** Prog ACOS, wzgledem ktorego kolorujemy wartosci. */
  acosTarget?: number;
  /** Ktore kolumny pominac. */
  omit?: string[];
}

export function metricColumns({ currency, acosTarget = 30, omit = [] }: MetricColumnOptions): any[] {
  const all: any[] = [
    {
      accessorKey: "impressions",
      header: "Wyśw.",
      meta: right,
      cell: (c: any) => <span className="text-muted">{num(c.getValue())}</span>,
    },
    {
      accessorKey: "clicks",
      header: "Kliknięcia",
      meta: right,
      cell: (c: any) => num(c.getValue()),
    },
    {
      accessorKey: "ctr",
      header: "CTR",
      meta: right,
      cell: (c: any) => <span className="text-muted">{pct(c.getValue(), 2)}</span>,
    },
    {
      accessorKey: "cost",
      header: "Wydatek",
      meta: right,
      cell: (c: any) => <span className="font-medium">{money(c.getValue(), currency)}</span>,
    },
    {
      accessorKey: "cpc",
      header: "CPC",
      meta: right,
      cell: (c: any) => money(c.getValue(), currency),
    },
    {
      accessorKey: "orders",
      header: "Zam.",
      meta: right,
      cell: (c: any) => num(c.getValue()),
    },
    {
      accessorKey: "cvr",
      header: "CVR",
      meta: right,
      cell: (c: any) => <span className="text-muted">{pct(c.getValue())}</span>,
    },
    {
      accessorKey: "sales",
      header: "Sprzedaż",
      meta: right,
      cell: (c: any) => <span className="font-medium">{money(c.getValue(), currency)}</span>,
    },
    {
      accessorKey: "acos",
      header: "ACOS",
      meta: right,
      // null (brak sprzedazy) sortujemy na koniec, nie jako zero
      sortUndefined: "last",
      cell: (c: any) => (
        <span className={`font-medium ${acosTone(c.getValue(), acosTarget)}`}>
          {pct(c.getValue())}
        </span>
      ),
    },
    {
      accessorKey: "roas",
      header: "ROAS",
      meta: right,
      cell: (c: any) => ratio(c.getValue()),
    },
  ];

  return all.filter((col) => !omit.includes(col.accessorKey));
}

/**
 * Udzial wyswietlen na gorze wynikow wyszukiwania.
 *
 * Amazon podaje go tylko dla kampanii, placementow i targetow - przy frazach
 * i produktach kolumna bylaby samymi kreskami, dlatego wlacza sie ja jawnie,
 * a nie przez `omit`. Sama liczba niewiele mowi, wiec pod nia idzie pasek:
 * niski udzial przy dobrym ACOS to miejsce, gdzie zostaje niewykorzystany
 * ruch - i to widac szybciej z ksztaltu niz z cyfr.
 */
export function tosShareColumn(): any {
  return {
    accessorKey: "tos_share",
    header: "Góra wyników",
    meta: { align: "right" as const, width: "7.5rem" },
    sortUndefined: "last",
    cell: (c: any) => {
      const v = c.getValue() as number | null | undefined;
      if (v === null || v === undefined) {
        return <span className="text-muted/60" title="Amazon nie podał tej metryki">—</span>;
      }
      const clamped = Math.max(0, Math.min(100, v));
      return (
        <div className="flex flex-col items-end gap-1">
          <span className="tnum text-xs font-medium">{pct(v)}</span>
          <span className="h-1 w-full max-w-[4.5rem] overflow-hidden rounded-full bg-border">
            <span
              className="block h-full rounded-full bg-accent"
              style={{ width: `${clamped}%` }}
            />
          </span>
        </div>
      );
    },
  };
}

/**
 * Ile ze stawki maksymalnej faktycznie placimy.
 *
 * Aukcja Amazona rozlicza ponizej stawki maksymalnej, wiec CPC znacznie nizszy
 * od stawki znaczy, ze nikt nas nie dociska. Sama liczba nie przesadza o niczym
 * - dopiero zestawiona z udzialem na gorze wynikow (kolumna obok) pokazuje, czy
 * da sie zejsc ze stawka bez utraty pozycji. Dlatego tu jest sam fakt, a rada
 * zostaje w kolumnie sugestii.
 *
 * Uwaga na dwie rzeczy, przez ktore stosunek potrafi przekroczyc 100%: mnoznik
 * placementu podnosi realna cene ponad stawke bazowa, a sama stawka jest
 * wartoscia BIEZACA, podczas gdy CPC to srednia z wybranego okresu. Jesli
 * stawka zmieniala sie w trakcie, ten stosunek nie opisuje niczego sensownego
 * - dlatego kolumna podaje fakt z zastrzezeniem, a nie zalecenie.
 */
export function bidGapColumn(): any {
  return {
    id: "bid_gap",
    header: "CPC / stawka",
    meta: { align: "right" as const, width: "7rem" },
    accessorFn: (row: any) =>
      row.bid && row.cpc ? row.cpc / row.bid : null,
    sortUndefined: "last",
    cell: (c: any) => {
      const v = c.getValue() as number | null;
      if (v === null || !Number.isFinite(v)) {
        return <span className="text-muted/60">—</span>;
      }
      const share = v * 100;
      // Duzo ponizej stawki = zapas; blisko stawki albo wyzej = pod korek.
      const tone =
        share <= 70 ? "text-good" : share >= 95 ? "text-warn" : "text-text";
      const hint =
        share > 100
          ? `Realny CPC przewyższa stawkę bazową o ${(share - 100).toFixed(0)}%. ` +
            "Zwykle robi to mnożnik placementu — albo stawka zmieniła się " +
            "w trakcie okresu (kolumna stawki pokazuje wartość bieżącą, " +
            "a CPC jest średnią z zakresu dat)."
          : `Płacisz ${share.toFixed(0)}% swojej stawki maksymalnej. ` +
            "Porównanie ma sens tylko wtedy, gdy stawka nie zmieniała się w tym okresie.";
      return (
        <span className={`tnum text-xs font-medium ${tone}`} title={hint}>
          {pct(share, 0)}
        </span>
      );
    },
  };
}

/**
 * TACOS - wydatek na reklame wobec CALEGO przychodu, takze organicznego.
 *
 * Kreska nie znaczy zera, tylko brak danych o sprzedazy calkowitej dla tej
 * pozycji: Ads API zna wylacznie sprzedaz z reklam, reszta wchodzi importem
 * z raportu biznesowego. Pokazanie zamiast tego ACOS bylo by wygodne i mylace
 * naraz - TACOS zawsze wychodzi nizszy, wiec falszywie uspokaja.
 */
export function tacosColumn({ acosTarget = 30 }: { acosTarget?: number } = {}): any {
  return {
    accessorKey: "tacos",
    header: "TACOS",
    meta: right,
    sortUndefined: "last",
    cell: (c: any) => {
      const v = c.getValue() as number | null | undefined;
      if (v === null || v === undefined) {
        return (
          <span className="text-muted/60" title="Brak danych o sprzedaży całkowitej">
            —
          </span>
        );
      }
      const r = c.row.original;
      const tytul =
        r.ad_share != null
          ? `Reklamy odpowiadają za ${pct(r.ad_share)} przychodu tego produktu`
          : undefined;
      return (
        <span className={`font-medium ${acosTone(v, acosTarget)}`} title={tytul}>
          {pct(v)}
        </span>
      );
    },
  };
}

/** Kolumna tekstowa z nazwa i podpisem pod spodem. */
/**
 * Kolumna nazwy z miniatura produktu.
 *
 * Uzywana tam, gdzie wiersz moze dotyczyc konkretnego produktu: frazy
 * wyszukiwania bywaja ASIN-ami konkurencji, a wykluczenia produktowe to
 * wprost ASIN. `b093zsryyw` bez zdjecia nie mowi nic, a wlasnie na tej
 * podstawie decyduje sie o wykluczeniu.
 */
export function nameColumnWithThumb(config: {
  id: string;
  header: string;
  primary: (row: any) => string;
  secondary?: (row: any) => string | null | undefined;
  width?: string;
}): any {
  return {
    id: config.id,
    header: config.header,
    accessorFn: (row: any) => config.primary(row),
    meta: { width: config.width },
    cell: (c: any) => {
      const r = c.row.original;
      const sub = config.secondary?.(r);
      const tytul = r.catalog?.title as string | undefined;
      return (
        <div className="flex min-w-0 items-center gap-2">
          {r.catalog?.image && (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={r.catalog.image}
              alt=""
              width={32}
              height={32}
              loading="lazy"
              decoding="async"
              className="size-8 shrink-0 rounded border border-border bg-surface-2 object-contain"
            />
          )}
          <div className="min-w-0">
            <div className="truncate font-medium" title={config.primary(r)}>
              {config.primary(r) || "—"}
            </div>
            {tytul && (
              <div className="truncate text-[11px] text-text/70" title={tytul}>
                {tytul}
              </div>
            )}
            {sub && (
              <div className="truncate text-[11px] text-muted" title={sub}>
                {sub}
              </div>
            )}
          </div>
        </div>
      );
    },
  };
}


export function nameColumn(config: {
  id: string;
  header: string;
  primary: (row: any) => string;
  secondary?: (row: any) => string | null | undefined;
  width?: string;
}): any {
  return {
    id: config.id,
    header: config.header,
    accessorFn: (row: any) => config.primary(row),
    meta: { width: config.width },
    cell: (c: any) => {
      const sub = config.secondary?.(c.row.original);
      return (
        <div className="min-w-0">
          <div className="truncate font-medium" title={config.primary(c.row.original)}>
            {config.primary(c.row.original) || "—"}
          </div>
          {sub && (
            <div className="truncate text-[11px] text-muted" title={sub}>
              {sub}
            </div>
          )}
        </div>
      );
    },
  };
}
