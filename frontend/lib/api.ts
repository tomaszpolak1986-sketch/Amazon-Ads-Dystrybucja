const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/* ------------------------------------------------------------------ */
/* Typy odpowiedzi backendu                                            */
/* ------------------------------------------------------------------ */

export interface Suggestion {
  code: string;
  level: "good" | "warn" | "bad" | "info";
  title: string;
  detail: string;
  action?: SuggestionAction | null;
  /** Proba, na ktorej oparta jest sugestia - wyswietlane pod uzasadnieniem */
  basis?: string | null;
}

export type SuggestionActionType =
  | "SET_BID"
  | "SET_BUDGET"
  | "ADD_NEGATIVE"
  | "ADD_NEGATIVE_ASIN"
  | "SET_PLACEMENT"
  | "PAUSE_TARGET"
  | "HARVEST"
  | "PAUSE_PRODUCT";

export interface SuggestionAction {
  type: SuggestionActionType;
  asin?: string | null;
  ad_id?: string | null;
  target_id?: string | null;
  campaign_id?: string | null;
  ad_group_id?: string | null;
  keyword_text?: string | null;
  match_type?: string | null;
  placement?: string | null;
  kind?: string | null;
  value?: number | null;
  from?: number | null;
  /** Uzasadnienie sugestii - wedruje do kolejki, zeby bylo widac powod zmiany. */
  reason?: string | null;
}

export interface SuggestionRow {
  scope: "CAMPAIGN" | "TARGET" | "PLACEMENT" | "SEARCH_TERM" | "PRODUCT";
  entity_id: string;
  entity_name: string | null;
  campaign_id: string | null;
  campaign_name: string | null;
  /** Portfolio kampanii; 0 oznacza kampanie bez portfolio. */
  portfolio_id: number;
  portfolio_name: string;
  country: string | null;
  currency: string | null;
  cost: number | null;
  sales: number | null;
  impressions: number | null;
  clicks: number | null;
  ctr: number | null;
  cvr: number | null;
  orders: number | null;
  acos: number | null;
  code: string;
  level: "good" | "warn" | "bad" | "info";
  title: string;
  detail: string;
  action: SuggestionAction | null;
  basis: string | null;
  /** Widełki rekomendowane przez Amazona; null = jeszcze nie pobrane. */
  amazon_bid: { low: number | null; median: number | null; high: number | null } | null;
  /** Powtarzające się słowo w niesprzedających frazach — kandydat na wyrażenie. */
  pattern: { token: string; terms: number; cost: number } | null;
  /** Czy stawka przekracza prog automatycznego kolejkowania */
  critical: boolean;
}

export interface SuggestionsResponse {
  /** Prog kosztu, powyzej ktorego regula w tle tworzy plan sama */
  threshold: number;
  items: SuggestionRow[];
}

export interface Metrics {
  impressions: number;
  clicks: number;
  cost: number;
  orders: number;
  sales: number;
  units: number;
  ctr: number;
  cpc: number;
  cvr: number;
  acos: number | null;
  roas: number | null;
  cpa: number | null;
  /** Udział wyświetleń na górze wyników (0–100). null = Amazon nie podał. */
  tos_share: number | null;
  /** Sprzedaż całkowita (reklamowa + organiczna). null = brak importu. */
  total_sales?: number | null;
  /** Wydatek / sprzedaż całkowita. null = brak danych o sprzedaży. */
  tacos?: number | null;
  /** Sprzedaż całkowita / wydatek — odwrotność TACOS. null = brak danych. */
  troas?: number | null;
  /** Jaka część całego przychodu pochodzi z reklam. */
  ad_share?: number | null;
  /** Ile dni okresu ma dane o sprzedaży całkowitej. */
  tacos_days?: number;
  /** Ile dni liczy cały wybrany okres. */
  range_days?: number;
  /** Wydatek policzony wyłącznie z dni mających sprzedaż całkowitą. */
  tacos_cost?: number;
  /** Sprzedaż reklamowa z tych samych dni. */
  tacos_ad_sales?: number;
}

/** Jedna kampania w rozbiciu frazy albo produktu. */
export interface OverlapPart {
  /** Czy kampania ma realny udzial w ruchu, czy zlapala tylko okruchy. */
  significant: boolean;
  campaign_id: string;
  campaign_name: string | null;
  targeting_type: string | null;
  clicks: number;
  cost: number;
  orders: number;
  sales: number;
  acos: number | null;
  cvr: number;
}

export interface OverlapRow extends Metrics {
  search_term: string;
  /** Kampanie z realnym udzialem. */
  campaign_count: number;
  /** Wszystkie kampanie, ktore fraze zlapaly - zwykle duzo wiecej. */
  campaign_count_all: number;
  /** Udzial najwiekszej kampanii w klikniecach (0-100). */
  top_share: number | null;
  best_campaign_id: string | null;
  best_campaign_name: string | null;
  breakdown: OverlapPart[];
}

/** Dane katalogowe produktu: tytuł i miniatura z SP-API. */
export interface CatalogInfo {
  title: string | null;
  brand: string | null;
  image: string | null;
  image_full: string | null;
}

export interface SkuOverlapRow extends Metrics {
  sku: string;
  asin: string | null;
  catalog?: CatalogInfo | null;
  campaign_count: number;
  campaign_count_all: number;
  /** Roznica miedzy najwyzszym a najnizszym ACOS wsrod znaczacych kampanii. */
  acos_spread: number | null;
  breakdown: OverlapPart[];
}

/** Sumy i wskazniki z okna przed albo po zmianie. */
export interface EffPeriod {
  days: number;
  cost: number;
  clicks: number;
  sales: number;
  orders: number;
  impressions: number;
  daily_cost: number;
  daily_sales: number;
  cpc: number | null;
  acos: number | null;
  roas: number | null;
}

export interface EffectivenessItem {
  id: number;
  entity_type: string;
  entity_id: string;
  entity_name: string | null;
  /** Kampania, w ktorej siedzi obiekt. */
  campaign?: string | null;
  field: string;
  field_label: string;
  old: string | null;
  new: string | null;
  old_num: number | null;
  new_num: number | null;
  changed_on: string;
  source: string;
  before: EffPeriod;
  after: EffPeriod;
  delta: {
    daily_cost: number | null;
    daily_sales: number | null;
    acos: number | null;
    cpc: number | null;
    roas: number | null;
  };
  /** W oknie pomiaru doszla kolejna zmiana - wynik dotyczy ich obu. */
  confounded: boolean;
  other_changes: number;
}

export interface EffectivenessWaiting {
  id: number;
  entity_type: string;
  entity_id: string;
  entity_name: string | null;
  campaign?: string | null;
  field: string;
  field_label: string;
  old: string | null;
  new: string | null;
  changed_on: string;
  ready_on: string;
  days_left: number;
  source: string;
}

export interface EffectivenessResult {
  min_days_after: number;
  window_days: number;
  data_through: string | null;
  items: EffectivenessItem[];
  waiting: EffectivenessWaiting[];
  waiting_total?: number;
  summary: Array<{
    field_label: string;
    count: number;
    median_acos_delta: number | null;
    median_cost_delta: number | null;
  }>;
}

/** Nagla zmiana w kampanii wzgledem jej wlasnej normy. */
export interface Anomaly {
  campaign_id: string;
  campaign_name: string | null;
  portfolio_id: number | null;
  portfolio_name: string | null;
  targeting_type: string | null;
  metric: string;
  code: string;
  title: string;
  detail: string;
  level: "good" | "warn" | "bad" | "info";
  /** Czy sygnal liczony jest z opoznieniem na atrybucje Amazona. */
  delayed: boolean;
  /** Mediana z okresu odniesienia. */
  baseline: number;
  /** Srednia z ostatnich dni. */
  current: number;
  change_pct: number;
  /** Zmodyfikowany wynik Z. 0 = norma nie miala zadnego rozrzutu. */
  z: number;
  window: { from: string; to: string };
  recent_cost: number;
  recent_sales: number;
  recent_acos: number | null;
  /** Sprzedaz z tych dni jest jeszcze niepelna - nie czytaj jej jak koncowej. */
  sales_incomplete: boolean;
}

/** Fraza, ktora napedzila zmiane w kampanii. */
export interface AnomalyContributor {
  search_term: string;
  ad_group_id: string | null;
  daily_now: number;
  daily_before: number;
  daily_change: number;
  /** Fraza, ktorej wczesniej nie bylo w ogole. */
  is_new: boolean;
  cost: number;
  clicks: number;
  orders: number;
  sales: number;
  acos: number | null;
}

export interface AnomalyContributors {
  window: {
    recent_from: string;
    recent_to: string;
    baseline_from: string;
    baseline_to: string;
  } | null;
  items: AnomalyContributor[];
  total_terms: number;
  /** O ile dziennie wzrosl wydatek na frazach, ktore rosna. */
  daily_increase: number;
  /** Ile z tego przyrostu poszlo na frazy bez ani jednego zamowienia. */
  daily_increase_no_sales: number;
}

export interface AnomaliesResult {
  window: {
    end: string;
    recent_days: number;
    baseline_days: number;
    attribution_lag_days: number;
  } | null;
  items: Anomaly[];
  total: number;
}

/** Gdzie wystepuje n-gram: jedna grupa reklam. */
export interface NgramGroup extends Metrics {
  campaign_id: string;
  campaign_name: string | null;
  ad_group_id: string | null;
  ad_group_name: string | null;
  /** Ile roznych fraz z tym fragmentem zlapala ta grupa. */
  terms: number;
  examples: Array<{
    search_term: string;
    clicks: number;
    cost: number;
    orders: number;
  }>;
}

export interface NgramBreakdown {
  ngram: string;
  items: NgramGroup[];
  total_groups?: number;
  /** Suma po wszystkich frazach z tym fragmentem - cena ewentualnej blokady. */
  totals: (Metrics & { terms: number; ad_groups: number }) | null;
}

/** Kampania, ktora regularnie wyczerpuje dzienny budzet. */
export interface BudgetCappedRow extends Metrics {
  campaign_id: string;
  name: string | null;
  targeting_type: string | null;
  portfolio_id: number | null;
  portfolio_name: string | null;
  budget: number;
  /** Dni z danymi w oknie. */
  days: number;
  /** Z ilu z nich kampania domknela budzet. */
  days_capped: number;
  capped_share: number;
  /** Sredni ACOS wszystkich aktywnych kampanii z budzetem — punkt odniesienia. */
  acos_ref: number;
  /** Czy zarabia lepiej niz srednia konta. */
  beats_average: boolean;
  /** Ostrozny krok w gore. null = nie proponujemy, bo ACOS powyzej sredniej. */
  suggested_budget: number | null;
  daily_cost: number;
}

export interface BudgetCappedResult {
  window: { start: string; end: string; days: number };
  acos_ref: number;
  cap_ratio: number;
  items: BudgetCappedRow[];
}

/** Produkt z bazy ofert - do wyboru przy tworzeniu kampanii. */
export interface CatalogProduct {
  sku: string | null;
  asin: string | null;
  title: string | null;
  image: string | null;
  price: number | null;
  quantity: number | null;
  fulfillment: string | null;
  /** Zero sztuk przy wysylce wlasnej. Przy FBA stanu nie znamy. */
  out_of_stock: boolean;
  /** W ilu kampaniach juz jest reklamowany. */
  campaigns: number;
}

/** Opis nowej kampanii wysylany do policzenia planu. */
export interface CampaignSpec {
  name: string;
  daily_budget: number;
  targeting_type: "AUTO" | "MANUAL";
  bidding_strategy: "LEGACY_FOR_SALES" | "AUTO_FOR_SALES" | "MANUAL";
  /** PAUSED albo ENABLED — status po udanym utworzeniu całości. */
  state: "PAUSED" | "ENABLED";
  portfolio_id?: string | null;
  start_date?: string | null;
  ad_group: { name: string; default_bid: number };
  skus: string[];
  asins: string[];
  keywords: Array<{ text: string; match_type: string; bid?: number | null }>;
  product_targets: Array<{ asin: string; bid?: number | null }>;
  auto_groups?: string[];
  negative_keywords: Array<{ text: string; match_type: string }>;
}

export interface HomeStatus {
  freshness: {
    sources: Array<{
      label: string;
      through: string | null;
      lag_days: number | null;
      /** Dni bez danych albo z polowa rynkow w ostatnich 30 dniach. */
      gap_days?: number;
      gap_from?: string | null;
      gap_to?: string | null;
    }>;
    last_run: string | null;
    last_status: string | null;
    expected_through: string;
  };
  attention: {
    plans: {
      count: number;
      items: number;
      by_kind: Array<{ kind: string; plans: number; items: number }>;
    };
    suggestions: {
      total: number;
      critical: number;
      threshold: number;
      by_portfolio: Array<{
        portfolio_id: number;
        portfolio_name: string;
        count: number;
        cost: number;
      }>;
    };
  };
}

export interface MonthlyRow {
  month: string;
  cost: number;
  ad_sales: number;
  orders: number;
  total_sales: number | null;
  acos: number | null;
  roas: number | null;
  tacos: number | null;
  ad_share: number | null;
  days: number;
  days_in_month: number;
  days_with_total: number;
  /** Czy miesiąc ma komplet dni danych reklamowych. */
  complete: boolean;
  /** Czy sprzedaż całkowita pokrywa cały miesiąc. */
  total_complete: boolean;
}

export interface SalesPreview {
  delimiter: string;
  headers: string[];
  mapping: Record<string, string | null>;
  sample: Array<Record<string, string>>;
  missing_required: string[];
  fields: Array<{ key: string; label: string; required: boolean }>;
}

export interface SalesImportResult {
  stored: number;
  replaced: number;
  start: string | null;
  end: string | null;
  skipped_no_date?: number;
  skipped_no_amount?: number;
  total_sales?: number;
  date_format?: string | null;
}

export interface SalesCoverage {
  profile_id: number;
  start: string | null;
  end: string | null;
  days: number;
  sales: number;
  asins: number;
}

export interface ManualSalesEntry {
  date: string;
  sales: number;
  units?: number;
  orders?: number;
}

export interface Profile {
  profile_id: number;
  country_code: string | null;
  currency_code: string | null;
  account_name: string | null;
  account_type: string | null;
  marketplace_id: string | null;
}

export interface Kpis {
  range: { start: string; end: string; days: number };
  current: Metrics;
  previous: Metrics;
  delta_pct: Record<string, number | null>;
}

export interface TimePoint extends Metrics {
  date: string;
}

export interface PlacementRow extends Metrics {
  /** Amazon nie raportuje tego placementu osobno - metryki sa puste, nie zerowe. */
  no_report?: boolean;
  placement: string;
}

/** Podział sprzedaży na sposób wysyłki. */
export interface FbaShare {
  /** "orders" = cała sprzedaż z raportu zamówień; "ads" = zastępczo, tylko sprzedaż z reklam. */
  source: "orders" | "ads";
  /** Waluta grupy, z której policzono udział. null przy źródle "ads". */
  currency: string | null;
  /** Ile rynków obejmuje ta grupa walutowa. */
  markets: number;
  /** Ile różnych walut jest w okresie — powyżej 1 kafelek pokazuje tylko największą. */
  /** Czy kwoty przeliczono na walute raportu (widok zbiorczy). */
  converted?: boolean;
  fba_sales: number;
  merchant_sales: number;
  unknown_sales: number;
  total: number;
  /** Udział FBA. null = brak danych. */
  fba_pct: number | null;
  unknown_pct: number | null;
}

export interface Overview {
  kpis: Kpis;
  timeseries: TimePoint[];
  placements: PlacementRow[];
  fba: FbaShare;
}

export interface MarketRow extends Metrics {
  profile_id: number;
  country: string | null;
  currency: string | null;
  account_name: string | null;
}

export interface PortfolioRow extends Metrics {
  portfolio_id: number;
  name: string | null;
  state: string | null;
  budget: number | null;
  budget_policy: string | null;
  campaigns: number;
  country: string | null;
  currency: string | null;
}

export interface CampaignRow extends Metrics {
  campaign_id: string;
  name: string | null;
  state: string | null;
  /** SP | SB | SD - trzy rozne produkty reklamowe w jednej tabeli. */
  ad_product?: string | null;
  targeting_type: string | null;
  budget: number | null;
  bidding_strategy: string | null;
  portfolio_id: number | null;
  portfolio_name: string | null;
  country: string | null;
  currency: string | null;
  suggestions?: Suggestion[];
}

export interface TargetRow extends Metrics {
  match_label?: string | null;
  target_id: string;
  target_kind: "KEYWORD" | "TARGET";
  target_text: string | null;
  match_type: string | null;
  campaign_id: string;
  ad_group_id: string | null;
  campaign_name: string | null;
  ad_group_name: string | null;
  bid: number | null;
  state: string | null;
  suggestions?: Suggestion[];
}

export interface SearchTermRow extends Metrics {
  search_term: string;
  campaign_id: string;
  ad_group_id: string | null;
  target_id: string | null;
  target_text: string | null;
  match_type: string | null;
  campaign_name: string | null;
  ad_group_name: string | null;
  suggested_bid?: number | null;
  /** Gdy fraza jest ASIN-em konkurencji - jego dane katalogowe. */
  asin?: string | null;
  catalog?: CatalogInfo | null;
}

export interface CampaignDetail extends Metrics {
  campaign_id: string;
  name: string | null;
  state: string | null;
  targeting_type: string | null;
  budget: number | null;
  budget_type: string | null;
  bidding_strategy: string | null;
  start_date: string | null;
  end_date: string | null;
  profile_id: number;
  portfolio_id: number | null;
  portfolio_name: string | null;
  country: string | null;
  currency: string | null;
}

export interface AdGroupRow extends Metrics {
  ad_group_id: string;
  name: string | null;
  state: string | null;
  default_bid: number | null;
  campaign_id: string;
  campaign_name: string | null;
}

export interface PlacementDetailRow extends Metrics {
  campaign_id: string;
  campaign_name: string | null;
  campaign_state: string | null;
  placement: string;
  modifier: number;
  country: string | null;
  currency: string | null;
  suggestions?: Suggestion[];
}

export interface NegativeKeywordRow {
  keyword_id: string;
  keyword_text: string | null;
  match_type: string | null;
  level: string;
  state: string | null;
  campaign_id: string;
  ad_group_id: string | null;
  campaign_name: string | null;
  ad_group_name: string | null;
  country: string | null;
}

export interface NegativeTargetRow {
  target_id: string;
  value: string | null;
  expression_type: string | null;
  level: string;
  state: string | null;
  campaign_id: string;
  ad_group_id: string | null;
  campaign_name: string | null;
  ad_group_name: string | null;
  country: string | null;
  label?: string | null;
  /** Wykluczony ASIN i jego dane katalogowe. */
  asin?: string | null;
  catalog?: CatalogInfo | null;
}

export interface NgramResult {
  account: { acos: number; cvr: number; min_clicks: number };
  /** Od jakiego ACOS fragment trafia na liste. */
  threshold_acos: number;
  items: NgramRow[];
  total: number;
  /** Ile i dlaczego odfiltrowano - zeby nic nie znikalo po cichu. */
  hidden: { below_threshold: number; too_small: number; not_usable: number };
}

export interface NgramRow extends Metrics {
  ngram: string;
  /** Dlugosc fragmentu w slowach. */
  n?: number;
  /** Wydatek ponad to, co usprawiedliwia sprzedaz przy sredniej konta. */
  waste?: number;
  /** Ile razy gorszy ACOS niz srednia konta. */
  acos_ratio?: number | null;
  /** Dluzsze fragmenty zawierajace ten - ten sam ruch, jedna decyzja. */
  children?: NgramRow[];
  /** W ktorych kampaniach fragment wystepuje, wg kosztu malejaco. */
  campaigns?: Array<{
    campaign_id: string;
    campaign_name: string;
    cost: number;
    orders: number;
  }>;
  campaign_count?: number;
  /** W ilu kampaniach fragment nie sprzedal nic - tam wykluczenie nic nie kosztuje. */
  campaigns_no_sales?: number;
  /** Ile te kampanie razem wydaly. */
  cost_no_sales?: number;
  terms: number;
}

export interface ProductAdRow extends Metrics {
  ad_id: string;
  sku: string | null;
  asin: string | null;
  state: string | null;
  campaign_id: string;
  ad_group_id: string | null;
  campaign_name: string | null;
  ad_group_name: string | null;
  country: string | null;
  currency: string | null;
  catalog?: CatalogInfo | null;
  suggestions?: Suggestion[];
}

export interface ProductRow extends Metrics {
  sku: string | null;
  asin: string | null;
  country: string | null;
  catalog?: CatalogInfo | null;
}

export interface ChangeRow {
  id: number;
  ts: string | null;
  profile_id: number;
  entity_type: string;
  entity_id: string;
  field: string;
  old_value: string | null;
  new_value: string | null;
  source: string;
  status: string;
  error: string | null;
}

export interface SyncRunRow {
  id: number;
  kind: string;
  profile_id: number | null;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  rows: number;
  detail: string | null;
}

export interface AuthState {
  authenticated: boolean;
  auth_disabled: boolean;
  configured: boolean;
}

export interface PlannedChange {
  id: string;
  kind?: string;
  entity_type?: string;
  field?: string;
  old: unknown;
  new: unknown;
  /** Nazwa obiektu rozwiazana przy odczycie - kampania, keyword, grupa reklam */
  entity_name?: string | null;
  /**
   * Kampania, w ktorej siedzi obiekt. Sama nazwa nie wystarcza: "Ścisłe
   * powiązane" istnieje w kazdej kampanii automatycznej.
   */
  context?: string | null;
  /** Dlaczego ta zmiana trafila do kolejki - tytul sugestii albo reguly. */
  reason?: string | null;
  /** Czytelna nazwa zmienianego pola, np. "stawka" zamiast "bid" */
  field_label?: string | null;
  old_label?: string | null;
  new_label?: string | null;
}

/**
 * Odpowiedz na akcje zapisu. Endpointy akcji NIGDY nie wykonuja zmian -
 * zwracaja policzony plan z `plan_id`, ktory trzeba osobno zatwierdzic
 * przez `applyPlan`. `requires_confirmation` jest wtedy true.
 */
export interface ActionResult {
  requires_confirmation: boolean;
  plan_id: string;
  kind: string;
  summary: string;
  planned: PlannedChange[];
  /** Pozycje, ktorych nie da sie wykonac - z powodem. */
  skipped?: { keyword: string; reason: string }[];
  item_count: number;
  expires_at: string;
  applied: number;
  failed: number;
  errors?: unknown;
  /** Ile pozycji zostalo w kolejce po czesciowym zatwierdzeniu */
  remaining?: number;
}

export interface PendingPlan {
  plan_id: string;
  profile_id: number;
  kind: string;
  summary: string | null;
  item_count: number;
  created_at: string | null;
  /** Zawsze false — plany nie wygasają. Pole zostaje dla zgodności. */
  expired: boolean;
  /** Ile minut plan czeka w kolejce. */
  age_minutes: number;
  source: string;
  planned: PlannedChange[];
}


/* ------------------------------------------------------------------ */
/* Reguly                                                              */
/* ------------------------------------------------------------------ */

export type RuleScope = "TARGET" | "SEARCH_TERM" | "CAMPAIGN" | "PLACEMENT" | "PRODUCT";

export interface Condition {
  metric: string;
  op: string;
  value: number;
}

export interface RuleAction {
  type: string;
  percent?: number;
  target_acos?: number;
  match_type?: string;
  step?: number;
  max_percent?: number;
}

export interface RuleLimits {
  max_items?: number;
  min_bid?: number;
  max_bid?: number;
  max_change_pct?: number;
  min_budget?: number;
  max_budget?: number;
  min_percent?: number;
}

export interface RuleFilters {
  campaign_name_contains?: string;
  campaign_name_excludes?: string;
  match_type?: string[];
  state?: string[];
  target_kind?: string[];
}

export interface RuleInput {
  name: string;
  description?: string | null;
  enabled: boolean;
  profile_id?: number | null;
  scope: RuleScope;
  lookback_days: number;
  filters?: RuleFilters | null;
  conditions: { all?: Condition[]; any?: Condition[] };
  action: RuleAction;
  limits?: RuleLimits | null;
  schedule_hours?: number | null;
}

export interface RuleRunInfo {
  ts: string | null;
  evaluated: number;
  matched: number;
  status: string;
  plan_id: string | null;
  detail: string | null;
}

export interface Rule extends RuleInput {
  id: number;
  last_run_at: string | null;
  summary: string;
  last_run: RuleRunInfo | null;
}

export interface RuleTemplate extends Omit<RuleInput, "enabled"> {
  key: string;
}

export interface RulesMeta {
  scopes: Record<string, string>;
  metrics: string[];
  operators: string[];
  scope_actions: Record<string, string[]>;
  templates: RuleTemplate[];
}

export interface RulePreview {
  evaluated: number;
  matched: number;
  limited_to: number;
  range: { start: string; end: string };
  sample: Array<Record<string, unknown>>;
}

export interface RuleRunResult {
  rule_id: number;
  name: string;
  results: Array<{
    profile_id: number;
    evaluated?: number;
    matched?: number;
    status: string;
    plan_id?: string | null;
    summary?: string;
    error?: string;
  }>;
}

/* ------------------------------------------------------------------ */
/* Klient                                                              */
/* ------------------------------------------------------------------ */

export type KontoAmazon = {
  ads: {
    client_id: string | null;
    client_secret: string | null;
    polaczone: boolean;
    region: string;
    redirect_uri: string;
    profile: { kraj: string | null; konto: string | null }[];
    konta: string[];
  };
  sp: {
    client_id: string | null;
    client_secret: string | null;
    refresh_token: string | null;
    region: string;
  };
  test?: { ok: boolean; opis: string };
};

export type AdminStan = { ustawione: boolean; odblokowane: boolean };

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

type Params = Record<string, string | number | boolean | null | undefined>;

function qs(params?: Params): string {
  if (!params) return "";
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== null && v !== undefined && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export async function apiGet<T>(path: string, params?: Params): Promise<T> {
  const res = await fetch(`${BASE}${path}${qs(params)}`, {
    cache: "no-store",
    credentials: "include",
  });
  if (!res.ok) {
    throw new ApiError(res.status, `${res.status} ${await res.text().catch(() => "")}`.slice(0, 300));
  }
  return res.json() as Promise<T>;
}

export async function apiPost<T>(path: string, body: unknown, params?: Params): Promise<T> {
  const res = await fetch(`${BASE}${path}${qs(params)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new ApiError(res.status, `${res.status} ${await res.text().catch(() => "")}`.slice(0, 300));
  }
  return res.json() as Promise<T>;
}

export async function apiPut<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new ApiError(res.status, `${res.status} ${await res.text().catch(() => "")}`.slice(0, 300));
  }
  return res.json() as Promise<T>;
}

export async function apiDelete<T>(path: string, params?: Params): Promise<T> {
  const res = await fetch(`${BASE}${path}${qs(params)}`, {
    method: "DELETE",
    credentials: "include",
  });
  if (!res.ok) {
    throw new ApiError(res.status, `${res.status} ${await res.text().catch(() => "")}`.slice(0, 300));
  }
  return res.json() as Promise<T>;
}

/* ------------------------------------------------------------------ */
/* Wygodne opakowania                                                  */
/* ------------------------------------------------------------------ */

export interface RangeParams extends Params {
  profile_id?: number | null;
  start?: string;
  end?: string;
}

export const api = {
  health: () =>
    apiGet<{
      status: string;
      wersja: string;
      credentials_configured: boolean;
      region: string;
      licencja: { ok: boolean; powod: string; klient: string | null; wazna_do: string | null };
    }>("/api/health"),

  // --- dostep do panelu ---
  me: () => apiGet<AuthState>("/api/auth/me"),
  kontoAmazon: () => apiGet<KontoAmazon>("/api/ustawienia/amazon"),
  zapiszAds: (body: { client_id: string; client_secret: string; region: string; redirect_uri: string }) =>
    apiPut<KontoAmazon>("/api/ustawienia/amazon/ads", body),
  linkAds: () => apiGet<{ url: string }>("/api/ustawienia/amazon/ads/link"),
  polaczAds: (adres: string) => apiPost<KontoAmazon>("/api/ustawienia/amazon/ads/polacz", { adres }),
  testAds: () => apiPost<KontoAmazon>("/api/ustawienia/amazon/ads/test", {}),
  zapiszSp: (body: { client_id: string; client_secret: string; refresh_token: string; region: string }) =>
    apiPut<KontoAmazon>("/api/ustawienia/amazon/sp", body),
  testSp: () => apiPost<KontoAmazon>("/api/ustawienia/amazon/sp/test", {}),
  adminStan: () => apiGet<AdminStan>("/api/ustawienia/admin"),
  adminOdblokuj: (haslo: string) => apiPost<AdminStan>("/api/ustawienia/admin/odblokuj", { haslo }),
  adminZablokuj: () => apiPost<AdminStan>("/api/ustawienia/admin/zablokuj", {}),
  adminHaslo: (obecne: string, nowe: string) => apiPut<AdminStan>("/api/ustawienia/admin/haslo", { obecne, nowe }),
  login: (password: string) => apiPost<{ authenticated: boolean }>("/api/auth/login", { password }),
  logout: () => apiPost<{ authenticated: boolean }>("/api/auth/logout", null),
  profiles: () => apiGet<Profile[]>("/api/profiles"),
  overview: (p: RangeParams) => apiGet<Overview>("/api/overview", p),
  markets: (p: RangeParams) => apiGet<MarketRow[]>("/api/markets", p),
  portfolios: (p: RangeParams) => apiGet<PortfolioRow[]>("/api/portfolios", p),
  campaigns: (p: RangeParams) => apiGet<CampaignRow[]>("/api/campaigns", p),
  campaignDetail: (id: string, p: RangeParams) =>
    apiGet<CampaignDetail>(`/api/campaigns/${id}`, p),
  adGroups: (p: RangeParams) => apiGet<AdGroupRow[]>("/api/ad-groups", p),
  targets: (p: RangeParams) => apiGet<TargetRow[]>("/api/targets", p),
  searchTerms: (p: RangeParams) => apiGet<SearchTermRow[]>("/api/search-terms", p),
  products: (p: RangeParams) => apiGet<ProductRow[]>("/api/products", p),
  productAds: (p: RangeParams) => apiGet<ProductAdRow[]>("/api/product-ads", p),
  planProductAds: (profile_id: number, ad_ids: string[], state: "ENABLED" | "PAUSED") =>
    apiPost<ActionResult>("/api/actions/product-ads", { profile_id, ad_ids, state }),
  placements: (p: RangeParams) => apiGet<PlacementRow[]>("/api/placements", p),
  placementsByCampaign: (p: RangeParams) =>
    apiGet<PlacementDetailRow[]>("/api/placements/by-campaign", p),
  negativeKeywords: (p?: Params) => apiGet<NegativeKeywordRow[]>("/api/negatives/keywords", p),
  negativeTargets: (p?: Params) => apiGet<NegativeTargetRow[]>("/api/negatives/targets", p),
  negatives: (p: RangeParams) => apiGet<SearchTermRow[]>("/api/opportunities/negatives", p),
  harvest: (p: RangeParams) => apiGet<SearchTermRow[]>("/api/opportunities/harvest", p),
  ngrams: (p: RangeParams) => apiGet<NgramResult>("/api/ngrams", p),
  homeStatus: (p: RangeParams) => apiGet<HomeStatus>("/api/home/status", p),
  monthly: (profile_id?: number | null, months = 6) =>
    apiGet<MonthlyRow[]>("/api/monthly", { profile_id: profile_id ?? undefined, months }),
  salesPreview: (content: string) =>
    apiPost<SalesPreview>("/api/total-sales/preview", { content }),
  salesImport: (body: {
    profile_id: number;
    content: string;
    mapping: Record<string, string | null>;
    delimiter: string;
  }) => apiPost<SalesImportResult>("/api/total-sales/import", body),
  salesManual: (profile_id: number, entries: ManualSalesEntry[]) =>
    apiPost<SalesImportResult>("/api/total-sales/manual", { profile_id, entries }),
  salesCoverage: (profile_id?: number | null) =>
    apiGet<SalesCoverage[]>("/api/total-sales/coverage", { profile_id: profile_id ?? undefined }),
  salesDelete: (profile_id: number, start: string, end: string) =>
    apiDelete<{ deleted: number }>("/api/total-sales", { profile_id, start, end }),
  createCampaign: (profile_id: number, spec: CampaignSpec) =>
    apiPost<ActionResult>("/api/actions/create-campaign", { profile_id, spec }),
  portfolioTrends: (p: RangeParams) =>
    apiGet<Record<string, TimePoint[]>>("/api/portfolios/timeseries", p),
  overlap: (p: RangeParams) => apiGet<OverlapRow[]>("/api/opportunities/overlap", p),
  skuOverlap: (p: RangeParams) => apiGet<SkuOverlapRow[]>("/api/opportunities/sku-overlap", p),
  catalogSearch: (profile_id: number, q?: string) =>
    apiGet<CatalogProduct[]>("/api/catalog/search", { profile_id, q, limit: 60 }),
  ngramBreakdown: (p: RangeParams & { ngram: string }) =>
    apiGet<NgramBreakdown>("/api/opportunities/ngram-breakdown", p),
  budgetCapped: (p: RangeParams) =>
    apiGet<BudgetCappedResult>("/api/opportunities/budget-capped", p),
  anomalies: (profile_id: number | null) =>
    apiGet<AnomaliesResult>("/api/anomalies", { profile_id }),
  anomalyContributors: (campaign_id: string) =>
    apiGet<AnomalyContributors>("/api/anomalies/contributors", { campaign_id }),
  effectiveness: (profile_id: number | null) =>
    apiGet<EffectivenessResult>("/api/effectiveness", { profile_id }),
  changes: (p?: Params) => apiGet<ChangeRow[]>("/api/changes", p),
  suggestions: (p: RangeParams) => apiGet<SuggestionsResponse>("/api/suggestions", p),
  queueSuggestions: (profile_id: number, actions: SuggestionAction[], p?: RangeParams) =>
    apiPost<{
      queued: number;
      items: number;
      // `plan_id` bywa puste: wpis bez planu niesie same powody pominiecia.
      plans: Array<{
        plan_id: string | null;
        kind: string;
        item_count: number;
        summary?: string;
        skipped?: { keyword: string; reason: string }[];
      }>;
    }>(
      "/api/suggestions/queue",
      { profile_id, actions },
      p,
    ),
  syncRuns: () => apiGet<SyncRunRow[]>("/api/sync/runs"),

  // --- krok 1: policz plan (nic nie idzie do Amazona) ---
  planBids: (profile_id: number, changes: Array<{ id: string; kind: string; bid: number }>) =>
    apiPost<ActionResult>("/api/actions/bids", { profile_id, changes }),
  planState: (profile_id: number, entity_type: string, ids: string[], state: string) =>
    apiPost<ActionResult>("/api/actions/state", { profile_id, entity_type, ids, state }),
  planBudgets: (profile_id: number, changes: Array<{ campaign_id: string; budget: number }>) =>
    apiPost<ActionResult>("/api/actions/budgets", { profile_id, changes }),
  planAddProducts: (
    profile_id: number,
    ad_group_id: string,
    skus: string[],
    asins: string[],
  ) =>
    apiPost<ActionResult>("/api/actions/add-products", {
      profile_id,
      ad_group_id,
      skus,
      asins,
    }),
  planPlacements: (
    profile_id: number,
    changes: Array<{
      campaign_id: string;
      strategy: string | null;
      placements: Array<{ placement: string; old: number; new: number }>;
    }>,
  ) => apiPost<ActionResult>("/api/actions/placements", { profile_id, changes }),
  planRemoveNegatives: (
    profile_id: number,
    keyword_ids: string[],
    target_ids: string[],
  ) =>
    apiPost<ActionResult>("/api/actions/remove-negatives", {
      profile_id,
      keyword_ids,
      target_ids,
    }),
  planNegativeAsins: (
    profile_id: number,
    items: Array<{ campaignId: string; adGroupId: string; asin: string }>,
  ) => apiPost<ActionResult>("/api/actions/negative-targets", { profile_id, items }),
  /** Wyklucza cale marki - jedna pozycja zamiast dziesiatek ASIN-ow. */
  planNegativeBrands: (
    profile_id: number,
    brands: Array<{ campaignId: string; adGroupId: string; brandId: string; brandName?: string }>,
  ) => apiPost<ActionResult>("/api/actions/negative-targets", { profile_id, brands }),
  brands: (profile_id: number, q?: string) =>
    apiGet<BrandRow[]>("/api/catalog/brands", { profile_id, q }),
  refreshBrands: (profile_id: number) =>
    apiPost<{ categories: number; brands: number }>("/api/catalog/brands/refresh", null, { profile_id }),
  planNegatives: (
    profile_id: number,
    items: Array<{ campaignId: string; adGroupId?: string; keywordText: string; matchType: string }>,
    /** CAMPAIGN wyklucza w calej kampanii, AD_GROUP tylko we wskazanych grupach. */
    level: "AD_GROUP" | "CAMPAIGN" = "AD_GROUP",
  ) => apiPost<ActionResult>("/api/actions/negatives", { profile_id, items, level }),
  planKeywords: (
    profile_id: number,
    items: Array<{ campaignId: string; adGroupId: string; keywordText: string; matchType: string; bid?: number }>,
  ) => apiPost<ActionResult>("/api/actions/harvest", { profile_id, items }),

  // --- krok 2: zatwierdzenie (dopiero to wysyla zmiany) ---
  pendingPlans: (profile_id?: number | null) =>
    apiGet<PendingPlan[]>("/api/plans", { profile_id: profile_id ?? undefined }),
  applyPlan: (plan_id: string, indexes?: number[]) =>
    apiPost<ActionResult>("/api/plans/apply", { plan_id, indexes }),
  cancelPlan: (plan_id: string) =>
    apiPost<{ plan_id: string; status: string }>("/api/plans/cancel", { plan_id }),

  // --- reguly: zawsze produkuja plany do zatwierdzenia, nigdy nie wykonuja zmian ---
  rulesMeta: () => apiGet<RulesMeta>("/api/rules/meta"),
  rules: () => apiGet<Rule[]>("/api/rules"),
  createRule: (rule: RuleInput) => apiPost<Rule>("/api/rules", rule),
  updateRule: (id: number, rule: RuleInput) => apiPut<Rule>(`/api/rules/${id}`, rule),
  deleteRule: (id: number) => apiDelete<{ deleted: number }>(`/api/rules/${id}`),
  previewRule: (id: number, profile_id: number) =>
    apiPost<RulePreview>(`/api/rules/${id}/preview`, null, { profile_id }),
  runRule: (id: number, profile_id?: number | null) =>
    apiPost<RuleRunResult>(`/api/rules/${id}/run`, null, {
      profile_id: profile_id ?? undefined,
    }),

  syncAll: (lookback_days: number) => apiPost<{ status: string }>("/api/sync/all", null, { lookback_days }),
  syncStatus: () => apiGet<{ running: boolean; what: string | null }>("/api/sync/status"),
  /** Odrzuca sugestie: days = liczba dni, brak = na stale. */
  dismissSuggestions: (
    profile_id: number,
    items: Array<{ scope: string; entity_id: string; code: string; title?: string; entity_name?: string | null }>,
    days: number | null,
  ) => apiPost<{ dismissed: number; until: string | null }>("/api/suggestions/dismiss", { profile_id, items, days }),
  dismissedSuggestions: (profile_id?: number | null) =>
    apiGet<DismissedSuggestion[]>("/api/suggestions/dismissed", { profile_id: profile_id ?? undefined }),
  restoreSuggestion: (id: number) => apiDelete<{ restored: number }>(`/api/suggestions/dismissed/${id}`),
};

export interface BrandRow {
  brand_id: string;
  name: string;
  /** Kategoria, w ktorej marka wystepuje - null, gdy nazwy kategorii nie znamy. */
  category: string | null;
}

export interface DismissedSuggestion {
  id: number;
  profile_id: number;
  scope: string;
  entity_id: string;
  code: string;
  title: string | null;
  entity_name: string | null;
  dismissed_at: string;
  until: string | null;
}
