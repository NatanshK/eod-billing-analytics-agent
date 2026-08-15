/** Small presentational pieces shared by all three screens. */

import type { ReactNode } from "react";

import type { ApiError } from "../api/client";
import type { DayWarning, RejectedRow } from "../api/types";

export function Card({
  title,
  subtitle,
  action,
  children,
}: {
  title?: string;
  subtitle?: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="card">
      {(title || action) && (
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          {title && <h2 className="card__title">{title}</h2>}
          {action}
        </div>
      )}
      {subtitle && <p className="card__subtitle">{subtitle}</p>}
      {children}
    </section>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="state">
      <div>
        <div className="skeleton" style={{ width: 220, height: 13, margin: "0 auto 10px" }} />
        <div className="state__body">{label}</div>
      </div>
    </div>
  );
}

/**
 * Renders an API failure using the server's own words.
 *
 * A 404 is usually "this day was never ingested" — an empty state rather than an
 * error — so it gets calmer framing and the endpoint hint.
 */
export function ErrorState({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  const isEmpty = error.isNotFound;

  return (
    <div className="state">
      <div>
        <div className="state__title">{isEmpty ? "Nothing to show yet" : "Couldn't load this"}</div>
        <p className="state__body">{error.message}</p>
        {error.hint && <p className="state__hint">{error.hint}</p>}
        {error.rows.length > 0 && (
          <ul className="notice__list" style={{ textAlign: "left", marginTop: 12 }}>
            {error.rows.slice(0, 5).map((row, i) => (
              <li key={i} style={{ fontSize: 12, color: "var(--text-muted)" }}>
                Row {row.row_index}: {row.message}
              </li>
            ))}
          </ul>
        )}
        {onRetry && !isEmpty && (
          <button className="btn" style={{ marginTop: 14 }} onClick={onRetry}>
            Try again
          </button>
        )}
      </div>
    </div>
  );
}

/**
 * Data-quality disclosure, collapsed to one line.
 *
 * Two things are in tension. The screens are specified down to their stat cards,
 * and a permanent banner pushes that layout down the page on every load. But a
 * card reading "₹3,190 billed" is misleading on its own when a row was dropped
 * on the way in.
 *
 * A disclosure settles both: the layout matches, and the caveat still sits above
 * the figures, reachable before the number is read. Native <details>, so it is
 * keyboard-operable and open to in-page search with no state to manage.
 */
export function DataQualityNotice({
  rejected,
  warnings,
  rowsReceived,
  rowCount,
}: {
  rejected: RejectedRow[];
  warnings: DayWarning[];
  rowsReceived: number;
  rowCount: number;
}) {
  if (rejected.length === 0 && warnings.length === 0) return null;

  // Only a rejection changes the figures; a warning just annotates them. The
  // summary leads with whichever is the stronger claim.
  const excluded = rejected.length > 0;
  const summary = [
    excluded ? `${rowCount} of ${rowsReceived} rows included` : null,
    warnings.length > 0
      ? `${warnings.length} data note${warnings.length === 1 ? "" : "s"}`
      : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <details className="disclosure">
      <summary className="disclosure__summary">
        <span
          className={`disclosure__dot${excluded ? " disclosure__dot--warn" : ""}`}
          aria-hidden="true"
        />
        <span className="disclosure__label">{summary}</span>
        <span className="disclosure__chevron" aria-hidden="true">
          ›
        </span>
      </summary>

      <div className="stack disclosure__body">
        {rejected.length > 0 && (
        <div className="notice notice--warn">
          <div className="notice__title">
            {rowCount} of {rowsReceived} rows are included in these figures
          </div>
          <div>
            {rejected.length === 1 ? "One row was" : `${rejected.length} rows were`} rejected during
            ingest and {rejected.length === 1 ? "is" : "are"} not counted anywhere below.
          </div>
          <ul className="notice__list">
            {rejected.map((row, i) => (
              <li key={i}>
                <code>{row.visit_id ?? `row ${row.row_index}`}</code> — {row.message}
                {row.hint && <> {row.hint}</>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {warnings.length > 0 && (
        <div className="notice notice--info">
          <div className="notice__title">
            {warnings.length === 1 ? "1 data note" : `${warnings.length} data notes`}
          </div>
          <ul className="notice__list">
            {warnings.map((warning, i) => (
              <li key={i}>
                {warning.visit_id && <code>{warning.visit_id}</code>} {warning.message}
              </li>
            ))}
          </ul>
        </div>
      )}
      </div>
    </details>
  );
}
