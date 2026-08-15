/** Screen 2 — Analytics: revenue by hour, plus the two separate rankings. */

import { api } from "../api/client";
import type { AnalyticsResponse, DrugRank } from "../api/types";
import { Card, DataQualityNotice, ErrorState, Loading } from "../components/common";
import { HourChart } from "../components/HourChart";
import { PageHead } from "../layout/AppShell";
import { useAsync } from "../lib/useReport";
import { plural } from "../lib/money";
import { useDay } from "../state/day-context";

export function AnalyticsPage() {
  const { clinicId, date, clinicName, error: dayError } = useDay();

  const { data, loading, error, reload } = useAsync<AnalyticsResponse>(
    clinicId && date ? () => api.analytics(clinicId, date) : null,
    [clinicId, date],
  );

  if (dayError) return <ErrorState error={dayError} />;

  return (
    <div className="page">
      <PageHead
        title="Analytics"
        subtitle={
          data ? `${clinicName} — ${data.business_date_display}` : clinicName || undefined
        }
      />

      {loading && <Loading label="Crunching the day…" />}
      {error && <ErrorState error={error} onRetry={reload} />}

      {data && (
        <div className="stack">
          <DataQualityNotice
            rejected={data.rejected_rows}
            warnings={data.warnings}
            rowsReceived={data.rows_received}
            rowCount={data.row_count}
          />

          <Card title="Revenue by Hour of Day">
            <HourChart
              buckets={data.analytics.revenue_by_hour}
              peak={data.analytics.peak_hour}
            />
          </Card>

          {/* Kept apart on purpose: the drug that moves the most units is
              usually not the one that earns the most, and a merged list would
              hide whichever fact it did not sort by. */}
          <div className="two-col">
            <Card title="Top Medicines — by Quantity">
              <Ranking
                rows={data.analytics.top_by_qty}
                value={(d) => plural(d.qty, "unit")}
                emptyLabel="No medicines were dispensed on this day."
              />
            </Card>

            <Card title="Top Medicines — by Revenue">
              <Ranking
                rows={data.analytics.top_by_revenue}
                value={(d) => d.revenue_display}
                emptyLabel="No medicine revenue was recorded on this day."
              />
            </Card>
          </div>
        </div>
      )}
    </div>
  );
}

function Ranking({
  rows,
  value,
  emptyLabel,
}: {
  rows: DrugRank[];
  value: (drug: DrugRank) => string;
  emptyLabel: string;
}) {
  if (rows.length === 0) {
    return <p className="state__body" style={{ margin: "6px 0", textAlign: "left" }}>{emptyLabel}</p>;
  }

  return (
    <div>
      {rows.map((drug) => (
        <div className="rank-row" key={drug.drug_name}>
          <span className="rank-row__n">{drug.rank}</span>
          <span className="rank-row__name" title={drug.drug_name}>
            {drug.drug_name}
          </span>
          <span className="rank-row__value">{value(drug)}</span>
        </div>
      ))}
    </div>
  );
}
