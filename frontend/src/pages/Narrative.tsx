/**
 * Screen 3 — AI Narrative Summary.
 *
 * The left card is what the owner would receive on WhatsApp. The right card is
 * the evidence: every figure in that text, beside the report field it was taken
 * from. The panel is not decoration — it is the visible form of the grounding
 * guarantee, and it is built from the same registry the backend used to render
 * the sentences, so the two cannot disagree.
 */

import { useEffect, useState } from "react";

import { ApiError, api } from "../api/client";
import type { Narrative, TracedFigure } from "../api/types";
import { Card, DataQualityNotice, ErrorState, Loading } from "../components/common";
import { PageHead } from "../layout/AppShell";
import { useDay } from "../state/DayContext";

export function NarrativePage() {
  const { clinicId, date, clinicName, clinicOwner, error: dayError } = useDay();

  const [data, setData] = useState<Narrative | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    if (!clinicId || !date) return;

    let cancelled = false;
    setLoading(true);
    setError(null);

    (async () => {
      try {
        // Prefer the cached summary; generating one costs a model call, so it
        // happens on request rather than on every visit to the screen.
        const cached = await api.cachedNarrative(clinicId, date);
        if (!cancelled) setData(cached);
      } catch (caught) {
        const apiError = caught as ApiError;
        if (cancelled) return;
        if (apiError.code === "narrative_not_current") {
          setData(null); // not an error: nothing generated yet
        } else {
          setError(apiError);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [clinicId, date]);

  async function generate(force = false) {
    if (!clinicId || !date) return;
    setBusy(true);
    setError(null);
    try {
      setData(await api.generateNarrative(clinicId, date, force));
    } catch (caught) {
      setError(caught as ApiError);
    } finally {
      setBusy(false);
    }
  }

  if (dayError) return <ErrorState error={dayError} />;

  return (
    <div className="page">
      <PageHead
        title="AI Narrative Summary"
        subtitle={`Generated from today's reconciliation — ${clinicName}`}
      />

      {loading && <Loading label="Looking for today's summary…" />}
      {error && <ErrorState error={error} onRetry={() => generate()} />}

      {!loading && !error && !data && (
        <Card>
          <div className="state">
            <div>
              <div className="state__title">No summary generated yet</div>
              <p className="state__body">
                The reconciliation is ready. Generate the owner-facing summary when you want
                it — every figure will be checked against the report before it is shown.
              </p>
              <button
                className="btn btn--primary"
                style={{ marginTop: 14 }}
                onClick={() => generate()}
                disabled={busy}
              >
                {busy ? "Generating…" : "Generate summary"}
              </button>
            </div>
          </div>
        </Card>
      )}

      {data && (
        <div className="stack">
          <DataQualityNotice
            rejected={data.rejected_rows}
            warnings={data.warnings}
            rowsReceived={data.rows_received}
            rowCount={data.row_count}
          />

          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <SourceBadge narrative={data} />
            <button className="btn" onClick={() => generate(true)} disabled={busy}>
              {busy ? "Regenerating…" : "Regenerate"}
            </button>
          </div>

          <div className="narrative-grid">
            <Card>
              <div className="wa-head">
                Sent to {clinicOwner || "the clinic owner"} · WhatsApp
              </div>

              {data.narrative_lines.map((line, i) => (
                <p className="wa-line" key={i}>
                  {line}
                </p>
              ))}

              {data.caveat && <p className="wa-caveat">{data.caveat}</p>}

              <div style={{ marginTop: 16 }}>
                <span className="badge badge--success">Delivered</span>
              </div>
            </Card>

            <Card
              title="Traced Figures"
              subtitle="Every number above maps to the deterministic report — this is what gets auto-checked."
            >
              {data.traced_figures.map((figure) => (
                <TracedRow key={figure.key} figure={figure} />
              ))}
            </Card>
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * Says plainly where the words came from.
 *
 * A fallback summary is still fully grounded, but it was assembled from a
 * template rather than written by a model — labelling it as AI output would be
 * a small lie, and the reason is worth showing.
 */
function SourceBadge({ narrative }: { narrative: Narrative }) {
  if (narrative.source === "llm") {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span className="badge badge--ai">AI Suggested</span>
        {narrative.cached && (
          <span style={{ fontSize: 11.5, color: "var(--text-faint)" }}>
            cached — regenerate for a fresh wording
          </span>
        )}
      </div>
    );
  }

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
      <span className="badge badge--muted">Generated offline</span>
      <span style={{ fontSize: 11.5, color: "var(--text-faint)" }}>
        {narrative.fallback_reason
          ? `Model unavailable (${narrative.fallback_reason}) — built directly from the report instead.`
          : "Built directly from the report."}
      </span>
    </div>
  );
}

function TracedRow({ figure }: { figure: TracedFigure }) {
  return (
    <div className="traced-row">
      <span className="traced-row__value">{figure.display}</span>
      <span className="traced-row__path">{figure.field_path}</span>
    </div>
  );
}
