/**
 * Revenue by hour of day.
 *
 * Single series, so there is no legend — the card title names it. The busiest
 * hour is the darkest step *and* carries a direct label, so the highlight never
 * depends on colour alone. Bar fills sit below 3:1 against the surface, so the
 * card ships a table view of the same figures as the required relief.
 *
 * Hours can go negative: a refund subtracts from the hour it was issued in. That
 * is a polarity change, not a smaller magnitude, so those bars grow downward
 * from a zero baseline in a warm hue and are always labelled.
 */

import { useId, useState } from "react";

import type { HourBucket } from "../api/types";

const PLOT_HEIGHT = 150;
const MIN_BAR = 2;

export function HourChart({ buckets, peak }: { buckets: HourBucket[]; peak: HourBucket | null }) {
  const [showTable, setShowTable] = useState(false);
  const tableId = useId();

  if (buckets.length === 0) {
    return <div className="chart-empty">No visits were logged on this day.</div>;
  }

  const values = buckets.map((b) => b.revenue_paise);
  const maxUp = Math.max(0, ...values);
  const maxDown = Math.abs(Math.min(0, ...values));
  const span = maxUp + maxDown || 1;

  // Split the plot between the up and down halves in proportion to the data, so
  // a day with one small refund does not give half the chart to it.
  const upHeight = Math.round((maxUp / span) * PLOT_HEIGHT);
  const downHeight = PLOT_HEIGHT - upHeight;

  return (
    <>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 12,
          marginTop: -8,
        }}
      >
        <span style={{ fontSize: 11.5, color: "var(--text-muted)" }}>
          {maxDown > 0
            ? "Bars below the line are hours where refunds exceeded takings."
            : "Revenue collected in each hour."}
        </span>
        <button
          className="btn"
          style={{ padding: "4px 9px", fontSize: 11.5 }}
          aria-expanded={showTable}
          aria-controls={tableId}
          onClick={() => setShowTable((v) => !v)}
        >
          {showTable ? "Hide figures" : "Show figures"}
        </button>
      </div>

      <div className="chart-scroll">
        <div>
          <div className="chart" style={{ height: PLOT_HEIGHT }} role="img" aria-label={ariaLabel(buckets, peak)}>
            {buckets.map((bucket) => {
              const up = Math.max(0, bucket.revenue_paise);
              const down = Math.abs(Math.min(0, bucket.revenue_paise));

              return (
                <div className="chart__col" key={bucket.hour}>
                  <div className="chart__pos" style={{ height: upHeight }}>
                    {up > 0 && (
                      <div
                        className={`chart__bar${bucket.is_peak ? " chart__bar--peak" : ""}`}
                        style={{ height: Math.max(MIN_BAR, (up / maxUp) * upHeight) }}
                        title={`${bucket.label} — ${bucket.revenue_display}`}
                      >
                        {bucket.is_peak && (
                          <span className="chart__callout">
                            Peak: {bucket.label} — {bucket.revenue_display}
                          </span>
                        )}
                      </div>
                    )}
                  </div>

                  <div className="chart__baseline" />

                  <div className="chart__neg" style={{ height: downHeight }}>
                    {down > 0 && (
                      <div
                        className="chart__bar chart__bar--down"
                        style={{ height: Math.max(MIN_BAR, (down / (maxDown || 1)) * downHeight) }}
                        title={`${bucket.label} — ${bucket.revenue_display} (net of refunds)`}
                      >
                        {/* Labelled, so "money went out this hour" never rests on
                            the colour change alone. */}
                        <span className="chart__downlabel">{bucket.revenue_display}</span>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Negative bars hang their value labels below the plot; the axis has
              to step down or the two collide. */}
          <div className={`chart__axis${maxDown > 0 ? " chart__axis--spaced" : ""}`}>
            {buckets.map((bucket) => (
              <div
                key={bucket.hour}
                className={`chart__tick${bucket.is_peak ? " chart__tick--peak" : ""}`}
              >
                {bucket.short_label}
              </div>
            ))}
          </div>
        </div>
      </div>

      {showTable && (
        <table className="table" id={tableId} style={{ marginTop: 16 }}>
          <thead>
            <tr>
              <th>Hour</th>
              <th>Revenue</th>
            </tr>
          </thead>
          <tbody>
            {buckets.map((bucket) => (
              <tr key={bucket.hour}>
                <td>
                  {bucket.label}
                  {bucket.is_peak && " (peak)"}
                </td>
                <td className={bucket.revenue_paise === 0 ? "table__zero" : undefined}>
                  {bucket.revenue_display}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

function ariaLabel(buckets: HourBucket[], peak: HourBucket | null): string {
  const range = `${buckets[0].short_label} to ${buckets[buckets.length - 1].short_label}`;
  return peak
    ? `Revenue by hour from ${range}. Busiest hour ${peak.label} at ${peak.revenue_display}.`
    : `Revenue by hour from ${range}. No hour took in revenue.`;
}
