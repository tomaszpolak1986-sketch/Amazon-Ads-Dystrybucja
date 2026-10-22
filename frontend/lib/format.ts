const LOCALE = "pl-PL";

export function money(value: number | null | undefined, currency = "EUR"): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat(LOCALE, {
    style: "currency",
    currency,
    maximumFractionDigits: value >= 1000 ? 0 : 2,
  }).format(value);
}

export function num(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat(LOCALE, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
}

export function pct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "—";
  return `${new Intl.NumberFormat(LOCALE, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value)}%`;
}

export function ratio(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat(LOCALE, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
}

export function shortDate(iso: string): string {
  const d = new Date(iso);
  return new Intl.DateTimeFormat(LOCALE, { day: "2-digit", month: "short" }).format(d);
}

export function dateTime(iso: string | null): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat(LOCALE, {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(iso));
}

/** Klasa koloru dla ACOS wzgledem progu docelowego. */
export function acosTone(acos: number | null | undefined, target = 30): string {
  if (acos === null || acos === undefined) return "text-muted";
  if (acos <= target * 0.75) return "text-good";
  if (acos <= target) return "text-text";
  if (acos <= target * 1.5) return "text-warn";
  return "text-bad";
}

/** Kierunek zmiany: dla kosztu i ACOS spadek jest dobry. */
export function deltaTone(value: number | null | undefined, lowerIsBetter = false): string {
  if (value === null || value === undefined || value === 0) return "text-muted";
  const positive = value > 0;
  const good = lowerIsBetter ? !positive : positive;
  return good ? "text-good" : "text-bad";
}

export function deltaLabel(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 1 }).format(value)}%`;
}

export function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

/**
 * Odmiana rzeczownika przez liczbę po polsku.
 *
 * Polski ma trzy formy, nie dwie: 1 wpis, 2 wpisy, 5 wpisów - a do tego
 * nastki (12, 13, 14) idą z formą mnogą dopełniaczową mimo końcówki 2-4.
 * Bez tego w interfejsie wychodzi "1 wpisów", co od razu widać.
 */
export function plural(n: number, jeden: string, dwa: string, piec: string): string {
  const abs = Math.abs(n);
  if (abs === 1) return jeden;
  const ostatnia = abs % 10;
  const dwieOstatnie = abs % 100;
  if (ostatnia >= 2 && ostatnia <= 4 && !(dwieOstatnie >= 12 && dwieOstatnie <= 14)) {
    return dwa;
  }
  return piec;
}

/** Liczba z odmienionym rzeczownikiem, np. „3 wpisy". */
export function count(n: number, jeden: string, dwa: string, piec: string): string {
  return `${n} ${plural(n, jeden, dwa, piec)}`;
}


/**
 * Czytelny powod odrzucenia zmiany przez Amazona.
 *
 * Sam licznik "bledow: 1" nie pozwala nic zrobic - nie wiadomo, czy to
 * duplikat, zle dopasowanie, czy brak uprawnien. Amazon zwraca blad
 * w kilku ksztaltach, wiec rozpakowujemy najczestsze.
 */
export function opisBledu(errors: unknown): string {
  if (!errors) return "";
  const lista = Array.isArray(errors) ? errors : Object.values(errors as object);
  const pierwszy = lista[0];
  if (!pierwszy) return "";
  if (typeof pierwszy === "string") return pierwszy.slice(0, 220);
  const o = pierwszy as Record<string, unknown>;
  const zagniezdzone = Array.isArray(o.errors) ? o.errors[0] : null;
  const kandydaci = [
    (zagniezdzone as Record<string, unknown> | null)?.errorValue,
    (zagniezdzone as Record<string, unknown> | null)?.message,
    o.message,
    o.details,
    o.errorValue,
  ];
  // Bierzemy pierwsza wartosc, ktora jest tekstem. Wczesniej pole `details`
  // bywalo obiektem i String() robil z niego "[object Object]".
  const tekst = kandydaci.find((k) => typeof k === "string" && k.trim());
  return String(tekst ?? JSON.stringify(pierwszy)).slice(0, 220);
}
