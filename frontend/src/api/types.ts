/** Response shapes from the EOD Billing API. Mirrors the FastAPI payloads. */

export type PaymentMode = "cash" | "card" | "upi";

/** A row the ingest endpoint refused, kept with the day and shown in the UI. */
export interface RejectedRow {
  row_index: number;
  code: string;
  message: string;
  field?: string;
  visit_id?: string;
  hint?: string;
}

/** Something odd but legal — clamped discounts, overpayments, name typos. */
export interface DayWarning {
  row_index: number;
  code: string;
  message: string;
  visit_id?: string;
}

/** Identity and provenance, repeated on every report response. */
export interface DayHeader {
  clinic_id: string;
  clinic_name: string;
  clinic_location: string;
  clinic_owner: string;
  business_date: string;
  business_date_display: string;
  ingested_at: string;
  payload_hash: string;
  row_count: number;
  rows_received: number;
  warnings: DayWarning[];
  rejected_rows: RejectedRow[];
}

export interface ModeTotals {
  mode: PaymentMode;
  visit_count: number;
  billed_paise: number;
  billed_display: string;
  collected_paise: number;
  collected_display: string;
  outstanding_paise: number;
  outstanding_display: string;
  refunds_paise: number;
  refunds_display: string;
}

export interface Reconciliation {
  total_billed_paise: number;
  total_billed_display: string;
  total_collected_paise: number;
  total_collected_display: string;
  outstanding_paise: number;
  outstanding_display: string;
  refunds_paise: number;
  refunds_display: string;
  net_collected_paise: number;
  net_collected_display: string;
  overpayments_paise: number;
  overpayments_display: string;
  visit_count: number;
  billable_visit_count: number;
  pending_visit_count: number;
  refund_count: number;
  /** null when nothing was billed — undefined, not zero. */
  collection_rate_pct: number | null;
  by_mode: ModeTotals[];
}

export interface HourBucket {
  hour: number;
  label: string;
  short_label: string;
  revenue_paise: number;
  revenue_display: string;
  is_peak: boolean;
}

export interface DrugRank {
  rank: number;
  drug_name: string;
  qty: number;
  revenue_paise: number;
  revenue_display: string;
}

export interface Analytics {
  revenue_by_hour: HourBucket[];
  /** null on a day with no positive revenue — there is no busiest hour. */
  peak_hour: HourBucket | null;
  top_by_qty: DrugRank[];
  top_by_revenue: DrugRank[];
  distinct_drug_count: number;
  revenue_basis: string;
}

/** One number in the narrative, with the report field it came from. */
export interface TracedFigure {
  key: string;
  display: string;
  field_path: string;
  value_paise?: number;
  value_number?: number;
}

export interface Narrative extends DayHeader {
  narrative_lines: string[];
  caveat: string;
  /** "llm" when the model wrote it; "fallback" when the deterministic template did. */
  source: "llm" | "fallback";
  fallback_reason: string | null;
  generated_at: string | null;
  traced_figures: TracedFigure[];
  cached: boolean;
}

export type ReconciliationResponse = DayHeader & { reconciliation: Reconciliation };
export type AnalyticsResponse = DayHeader & { analytics: Analytics };
export type FullReportResponse = DayHeader & {
  reconciliation: Reconciliation;
  analytics: Analytics;
};

export interface DaySummary {
  business_date: string;
  business_date_display: string;
  row_count: number;
  ingested_at: string;
  has_warnings: boolean;
}

export interface ClinicDays {
  clinic_id: string;
  name: string;
  location: string;
  owner: string;
  days: DaySummary[];
}

export interface Clinic {
  clinic_id: string;
  name: string;
  location: string;
  owner: string;
  days: number;
}

export interface Health {
  status: string;
  llm_configured: boolean;
  llm_model: string | null;
  narrative_source_if_asked_now: "llm" | "fallback";
  clinics: number;
}
