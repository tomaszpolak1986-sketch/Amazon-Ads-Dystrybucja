/**
 * Etykiety kodow Amazona po polsku.
 *
 * Ads API zwraca kody techniczne (TARGETING_EXPRESSION_PREDEFINED, ENABLED),
 * a konsola reklamowa pokazuje nazwy opisowe. Zestawienie panelu z Amazonem
 * ma sens tylko wtedy, gdy nazywamy rzeczy tak samo.
 *
 * Odpowiednik po stronie backendu: backend/app/api/targeting_names.py
 * (MATCH_LABELS) oraz backend/app/api/actions.py (STATE_LABELS). Zmiana tam
 * powinna isc razem ze zmiana tutaj.
 */

export const MATCH_LABEL: Record<string, string> = {
  EXACT: "ścisłe",
  PHRASE: "wyrażenie",
  BROAD: "przybliżone",
  TARGETING_EXPRESSION: "produktowe",
  TARGETING_EXPRESSION_PREDEFINED: "automatyczne",
  NEGATIVE_EXACT: "ścisłe",
  NEGATIVE_PHRASE: "wyrażenie",
};

export const STATE_LABEL: Record<string, string> = {
  ENABLED: "aktywny",
  PAUSED: "wstrzymany",
  ARCHIVED: "zarchiwizowany",
};

/** Etykieta kodu; nieznany kod zostaje sobą, bo lepszy niż pusta komórka. */
export function matchLabel(code: string | null | undefined): string | null {
  if (!code) return null;
  return MATCH_LABEL[code] ?? code;
}

export function stateLabel(code: string | null | undefined): string | null {
  if (!code) return null;
  return STATE_LABEL[code] ?? code;
}

/** Rodzaje encji tak, jak nazywa je panel — nie kodami z API. */
export const ENTITY_LABEL: Record<string, string> = {
  CAMPAIGN: "Kampania",
  AD_GROUP: "Grupa reklam",
  PRODUCT_AD: "Produkt",
  KEYWORD: "Keyword",
  TARGET: "Target",
  NEGATIVE: "Wykluczenie",
  NEGATIVE_ASIN: "Wykluczony ASIN",
};

export function entityLabel(code: string | null | undefined): string | null {
  if (!code) return null;
  return ENTITY_LABEL[code] ?? code;
}

/** Rodzaje planów w kolejce zatwierdzeń. */
export const PLAN_KIND_LABEL: Record<string, string> = {
  bids: "Stawki",
  state: "Status",
  budgets: "Budżety",
  negatives: "Wykluczenia fraz",
  negative_targets: "Wykluczone ASIN-y",
  harvest: "Nowe keywordy",
  placements: "Placementy",
  product_ads: "Reklamy produktów",
  create_campaign: "Nowa kampania",
};

export function planKindLabel(kind: string): string {
  return PLAN_KIND_LABEL[kind] ?? kind;
}
