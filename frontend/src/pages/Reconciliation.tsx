/** Screen 1 — EOD Reconciliation: four stat cards and the mode breakdown. */

import { api } from "../api/client";
import type { ModeTotals, ReconciliationResponse } from "../api/types";
import { Card, DataQualityNotice, ErrorState, Loading } from "../components/common";
import { PageHead } from "../layout/AppShell";
import { useAsync } from "../lib/useReport";
import { plural } from "../lib/money";
import { useDay } from "../state/day-context";

export function ReconciliationPage() {
  const { clinicId, date, error: dayError } = useDay();

  const { data, loading, error, reload } = useAsync<ReconciliationResponse>(
    clinicId && date ? () => api.reconciliation(clinicId, date) : null,
    [clinicId, date],
  );

  if (dayError) return <ErrorState error={dayError} />;

  return (
    <div className="page">
      <PageHead title="EOD Reconciliation" />

      {loading && <Loading label="Reconciling the day…" />}
      {error && <ErrorState error={error} onRetry={reload} />}

      {data && (
        <div className="stack">
          <DataQualityNotice
            rejected={data.rejected_rows}
            warnings={data.warnings}
            rowsReceived={data.rows_received}
            rowCount={data.row_count}
          />

          <StatCards report={data} />

          <Card title="Payment Mode Breakdown">
            <ModeTable rows={data.reconciliation.by_mode} />
          </Card>
        </div>
      )}
    </div>
  );
}

function StatCards({ report }: { report: ReconciliationResponse }) {
  const r = report.reconciliation;

  return (
    <div className="stat-grid">
      <Stat
        label="Total Billed"
        value={r.total_billed_display}
        note={plural(r.billable_visit_count, "visit")}
      />
      <Stat
        label="Total Collected"
        value={r.total_collected_display}
        // "0% of billed" would read as a disastrous day rather than an empty one.
        note={
          r.collection_rate_pct === null
            ? "nothing billed today"
            : `${r.collection_rate_pct}% of billed`
        }
        tone="blue"
      />
      <Stat
        label="Outstanding"
        value={r.outstanding_display}
        note={
          r.pending_visit_count > 0
            ? plural(r.pending_visit_count, "pending visit")
            : // "Fully collected" would be an odd boast on a day that billed nothing.
              r.total_billed_paise === 0
              ? "nothing billed"
              : "fully collected"
        }
        tone={r.outstanding_paise > 0 ? "amber" : undefined}
      />
      <Stat
        label="Refunds"
        value={r.refunds_display}
        note={r.refund_count === 0 ? "none today" : plural(r.refund_count, "refund")}
        tone={r.refunds_paise > 0 ? "red" : undefined}
      />
    </div>
  );
}

function Stat({
  label,
  value,
  note,
  tone,
}: {
  label: string;
  value: string;
  note: string;
  tone?: "amber" | "red" | "blue";
}) {
  return (
    <div className="stat">
      <div className="stat__label">{label}</div>
      <div className="stat__value">{value}</div>
      <div className={`stat__note${tone ? ` stat__note--${tone}` : ""}`}>{note}</div>
    </div>
  );
}

/** UPI is an initialism; title-casing it to "Upi" looks like a bug. */
const MODE_LABELS: Record<string, string> = { cash: "Cash", card: "Card", upi: "UPI" };

function ModeTable({ rows }: { rows: ModeTotals[] }) {
  const anyRefunds = rows.some((row) => row.refunds_paise !== 0);

  return (
    <table className="table">
      <thead>
        <tr>
          <th>Mode</th>
          <th>Billed</th>
          <th>Collected</th>
          <th>Outstanding</th>
          {/* The refunds column only earns its space on a day that had some. */}
          {anyRefunds && <th>Refunds</th>}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.mode}>
            <td>{MODE_LABELS[row.mode] ?? row.mode}</td>
            <Amount paise={row.billed_paise} display={row.billed_display} />
            <Amount paise={row.collected_paise} display={row.collected_display} />
            <Amount paise={row.outstanding_paise} display={row.outstanding_display} />
            {anyRefunds && (
              <Amount paise={row.refunds_paise} display={row.refunds_display} />
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Zeroes are dimmed so the eye lands on the rows that actually moved money. */
function Amount({ paise, display }: { paise: number; display: string }) {
  return <td className={paise === 0 ? "table__zero" : undefined}>{display}</td>;
}
