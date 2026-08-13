# EOD Billing & Analytics Agent

Ingests a clinic's daily billing log and produces three things: a deterministic
end-of-day reconciliation, analytics, and an LLM-written narrative summary in
which **every figure is traceable back to the report**.

```
/backend    Python REST API (FastAPI + SQLite)
/frontend   React app (Vite + TypeScript)
```

---

## Quick start

```bash
# Backend — http://localhost:8000  (docs at /docs)
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --reload

# Frontend — http://localhost:5173
cd frontend
npm install
npm run dev
```

The backend seeds itself from `backend/seed_data/` on first start, so both the
API and the UI have the three sample clinic-days loaded immediately.

```bash
cd backend && .venv/bin/python -m pytest -q     # 233 tests
```

**No API key is required to run any of this.** Without `OPENROUTER_API_KEY` the
narrative endpoint still returns a complete, fully grounded summary, labelled
`source: "fallback"`. Configuration lives in `backend/.env.example`.

---

## The one idea worth reading

The hard requirement is *"every figure that appears in the narrative must trace
back to the report — zero invented numbers."* Prompting a model to be careful
with numbers does not achieve that; it only makes violations rarer.

So the model is never given the ability to write a number.

The deterministic report is compiled into a **figure registry** — every quotable
value, with the token that stands for it and the report field it came from:

```python
"total_billed": {
    "token": "{{total_billed}}",
    "display": "₹3,190",
    "field_path": "reconciliation.total_billed_paise",
}
```

The model receives that list as its entire numeric vocabulary and is required to
write `{{total_billed}}` rather than `₹3,190`. The service then substitutes the
real values. A hallucinated figure is therefore not a wrong number that has to be
caught by a checker — it is a token that fails to resolve.

That same registry is also the substitution table *and* the payload behind the UI's
**Traced Figures** panel. One structure, three uses, so the sentence, the number,
and the audit trail cannot drift apart.

### Four gates

Every model response passes all four or is discarded whole:

| # | Gate | Rejects |
|---|---|---|
| 1 | **Parse** | non-JSON, truncated JSON, prose (markdown fences are unwrapped, not rejected) |
| 2 | **Schema** | missing/wrong-typed keys, empty body |
| 3 | **Pre-substitution audit** | *any* digit outside a token, unknown tokens, and profit/margin/cost claims outside the caveat |
| 4 | **Post-substitution audit** | any digit in the rendered text that did not come from a registry substitution |

Gate 3 rejects a correct number typed literally, not just a wrong one — accepting
it would mean verifying arithmetic instead of provenance, and would leave the
Traced Figures panel with nothing to point at.

Gate 4 re-scans the finished text and requires every digit to fall inside a span
that substitution produced. It is the audit an automated grader would run, run on
ourselves before responding.

On any failure: one corrective retry, then a **deterministic fallback** assembled
directly from the registry — grounded by construction, and put through the same
four gates rather than trusted for being ours. The response always reports
`source` and, when it fell back, `fallback_reason`.

**A model failure never becomes a request failure.** No API key, a timeout, a
gateway error, junk JSON, or two bad drafts all produce a valid 200.

### "It can't be computed" is a first-class answer

Cost price is absent from the billing schema, so profit is unanswerable — not
merely hard. The prompt names the unavailable metrics, the model is told to say so
plainly in a dedicated `caveat` field, and gate 3 blocks profit/margin/markup
claims anywhere else in the text.

---

## The deterministic layer

`app/core/` is the ground truth. It never calls an LLM, never touches the network,
and holds no state — `reconcile(visits)` and `compute_analytics(visits)` are pure
functions. A test walks the module ASTs to assert no networking or narrative
import ever appears there, so the rule cannot rot.

Money is `int` paise end to end. A float in a paise field is rejected, not
coerced. Rupees exist only at the display edge, and
`frontend/src/lib/money.ts` is a character-for-character port of
`backend/app/core/money.py` — the Traced Figures panel compares rendered strings,
so a formatting mismatch would look like a grounding failure.

### Semantics pinned deliberately

The brief leaves these open; the narrative quotes them verbatim, so they are fixed
here, tested, and documented rather than left to chance.

| Rule | Choice | Why |
|---|---|---|
| **Outstanding** | `Σ max(0, billed − paid)` **per visit** | Summing at day level lets an overpaid visit silently cancel an unpaid one. On the sample edge day that reports ₹145 instead of the true ₹170. |
| **Collected** | gross of refunds; `refunds` and `net_collected` reported separately | Keeps `billed − collected = outstanding` readable on the dashboard. |
| **Refund rows** | contribute 0 to billed/collected/outstanding; only to `refunds` | A refund is not negative billing. |
| **Refund sign** | either sign accepted, normalised to a positive magnitude | The dataset uses negative; mixing both raises a day-level warning. |
| **Billed per visit** | `max(0, Σ(qty × price) − discount)` | A discount larger than the line total clamps to zero and warns rather than going negative. |
| **Drug rankings** | exclude refunded visits | A returned drug did not move. |
| **Drug revenue** | pre-discount line value | Visit-level discounts cannot be fairly attributed across line items; the basis is returned in the response as `revenue_basis` rather than silently assumed. |
| **Ties** | broken alphabetically | Same input, same output, every time. |
| **Collection rate** | `null` when nothing was billed | "0%" reads as a catastrophic day rather than an empty one. |
| **Peak hour** | `null` when no hour took in revenue | Naming a "busiest hour" on an all-refund day would be meaningless. |
| **Hourly revenue** | collected, with refunds subtracting from their own hour | That is what "which hour did the most business" means. |
| **Timestamps** | UTC offset required; naive rejected | Guessing the zone puts the peak hour in the wrong bucket. |

`reconcile()` closes by asserting the payment-mode columns sum to the headline
totals. A mismatch raises rather than serving a report whose table disagrees with
its own stat cards.

---

## Handling the sample dataset

The provided dataset's README says *"not every row in every file is guaranteed to
be well-formed — handle that however you think a production ingestion endpoint
should."* Each of the three days is awkward in a different way, and all three run
through the same pipeline unchanged.

**27 Jul — 18 of 19 rows.** One row has no `payment_mode`. The default policy is
**quarantine, don't discard and don't reject the day**: the good rows are ingested,
the bad row is stored beside them, and it is echoed in *every* report response for
that day so the UI can show "18 of 19 rows are included in these figures". Rejecting
the whole day would cost the clinic seventeen good visits; dropping the row silently
would understate the totals with nothing on screen to say so. `?strict=true` gives
all-or-nothing instead.

This day also contains `PARACETMOL` alongside `PARACETAMOL`. They are **not**
merged — auto-correcting would move revenue between line items on a guess. They
are counted separately and a warning names the pair, because the split understates
both and the fix belongs to whoever owns the data.

**25 Jul — every row is a refund.** Nothing billed, so the collection rate is
`null` and there is no peak hour. Both rankings are empty. The hourly chart renders
those hours as labelled bars *below* a zero baseline.

**26 Jul — an empty file.** A real day on which nobody came in, but it carries no
`clinic_id` and no timestamp to file it under. Rather than guess, the endpoint
accepts them explicitly and says so in the error when they are missing:

```bash
curl -X POST "$API/api/v1/billing-logs?clinic_id=CLN-KNP-014&business_date=2026-07-26" \
     -H 'Content-Type: application/json' --data @billing_log_2026-07-26.json
```

Supplied values are cross-checked against the rows when rows exist; a disagreement
is a filing error, not something to override.

---

## REST API

Base path `/api/v1`. Money fields carry both `_paise` (integer, authoritative) and
`_display` (formatted), so the UI never re-derives formatting.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/billing-logs` | Ingest a day. JSON body or `multipart/form-data`. Query: `strict`, `clinic_id`, `business_date` |
| `GET` | `/clinics` | Clinics with data |
| `GET` | `/clinics/{clinic_id}/days` | Ingested dates, newest first — drives the date picker |
| `GET` | `/reports/{clinic_id}/{date}/reconciliation` | Screen 1 |
| `GET` | `/reports/{clinic_id}/{date}/analytics` | Screen 2. Query: `limit` (default 5) |
| `GET` | `/reports/{clinic_id}/{date}` | Both, in one round trip |
| `POST` | `/reports/{clinic_id}/{date}/narrative` | Generate (or return cached). Query: `force` |
| `GET` | `/reports/{clinic_id}/{date}/narrative` | Cached only; 404 if none is current |
| `DELETE` | `/reports/{clinic_id}/{date}` | Remove a day and everything derived from it |
| `GET` | `/api/v1/health` | Liveness, and whether a narrative would come from the model or the fallback |

Interactive docs at `/docs`.

### Why it is shaped this way

The resource is **a clinic-day** (`{clinic_id}/{business_date}`), not a visit.
That is the unit the clinic owner reasons about, the unit the reconciliation is
defined over, and the unit that gets corrected and re-uploaded — so it is the unit
of both the URL and the write.

Reconciliation and analytics are separate GETs because the two screens need them
separately, with `/reports/{clinic}/{date}` as a combined read for the shell.
The narrative is a `POST` because generating it has a cost and a side effect
(it is cached); the `GET` is the pure read of that cache.

### Errors

Every failure returns a structured body with a stable `error` code — never a bare
framework traceback, and never a 500 for anything the caller did. Malformed rows
come back with the offending row index, the exact field, a machine-readable code,
and a hint:

```json
{
  "error": "invalid_billing_log",
  "valid_rows": 18,
  "rejected_rows": 1,
  "errors": [{
    "row_index": 18,
    "visit_id": "V-20260727-019",
    "field": "payment_mode",
    "code": "missing_field",
    "message": "payment_mode is required but was not present"
  }]
}
```

Codes include `non_integer_paise`, `non_positive_quantity`, `missing_timezone`,
`invalid_payment_mode`, `duplicate_visit_id`, `negative_payment`, `empty_refund`,
`negative_discount`, `clinic_id_mismatch`, `multiple_business_dates`,
`unattributable_empty_log` and `malformed_json`. Errors are **collected, not
raised on first failure** — one upload returns every problem in the file.

---

## Data consistency on update

The store holds **only raw ingested rows**. Reconciliation and analytics are
recomputed from them on every read and are never written to a table, so there is
no second copy of the truth that can drift from the first. `load_day()` replays
the stored rows through the *same* parser the ingest endpoint used, so a report
served from the database is identical to one served straight after upload — a
test asserts exactly that.

Four guarantees, in `app/storage/repository.py`:

1. **A day is replaced, never merged.** Re-ingesting `(clinic_id, business_date)`
   deletes the old rows and inserts the new ones inside a single
   `BEGIN IMMEDIATE` transaction. A reader sees the whole old day or the whole new
   one — never a mixture, and never orphan rows from a longer previous upload.
2. **Validation completes before the transaction opens.** A payload with a bad row
   never reaches the database; `save_day()` refuses a `ParsedDay` that still
   carries errors.
3. **Derived figures are never cached.** Totals cannot go stale because they are
   not stored.
4. **The narrative — the one expensive derived artefact — is keyed by a SHA-256
   of the rows it described**, covering the rejected rows as well as the accepted
   ones. Correcting a day changes that hash, and the cached summary stops being
   served in the same transaction that replaced the rows. A stale narrative cannot
   survive a data correction. Re-uploading an *identical* file produces the same
   hash (keys are canonicalised before hashing), so it does not burn a model call.

Concurrency: one process-wide SQLite connection behind a re-entrant lock. A
thread-local connection is the usual choice but is wrong for `:memory:`, where the
database is scoped to its connection — each request thread would silently get an
empty database of its own.

---

## Frontend

Vite + React + TypeScript, three routes behind a persistent icon rail. The
selected clinic-day lives in a context above the router, so switching screens
keeps the day — the three screens are three views of one report.

- **`/reconciliation`** — four stat cards and the payment-mode table. The refunds
  column appears only on a day that had refunds.
- **`/analytics`** — revenue by hour with the peak called out, and the two rankings
  kept deliberately apart, because the drug that moves the most units is usually
  not the one that earns the most.
- **`/narrative`** — the WhatsApp-shaped summary beside the Traced Figures panel
  that maps each number to its `field_path`.

A day with quarantined rows shows a disclosure banner on **all three** screens: a
stat card reading "₹3,190 billed" is misleading on its own when a row was dropped
on the way in.

The chart is hand-rolled SVG/CSS — ten buckets does not warrant a charting
dependency. It is a single series, so there is no legend; the peak is both the
darkest step *and* directly labelled, and negative (refund-heavy) hours are
labelled with their values, so nothing is carried by colour alone. A "Show figures"
toggle exposes the same numbers as a table.

---

## Tests

233 tests, `cd backend && .venv/bin/python -m pytest -q`.

| File | Covers |
|---|---|
| `test_reconciliation.py` | Golden totals; the overpayment-vs-shortfall case; clamped discounts; empty days |
| `test_analytics.py` | Hour bucketing, negative hours, peak selection, both rankings, tie-breaking |
| `test_parsing.py` | Every rejection code, located to row *and* field; tolerant input; day-level invariants |
| `test_grounding.py` | The four gates against a model that returns prose, truncated JSON, unknown tokens, a smuggled literal, a profit claim, or times out |
| `test_storage.py` | Atomic replace, hash stability, narrative invalidation |
| `test_api.py` | Every endpoint; that no bad input yields a 500 |
| `test_real_dataset.py` | The three provided days, including all-refund and empty |
| `test_determinism.py` | AST scan proving the core imports no LLM or network; order-independence |

Expected values are hand-computed literals. Asserting against a value re-derived
by the code under test would only prove the code agrees with itself.

---

## Deployment

Frontend on Vercel (`frontend/vercel.json`), backend on Render
(`backend/render.yaml`).

- Set `VITE_API_BASE_URL` on Vercel to the Render URL.
- Set `ALLOWED_ORIGINS` on Render to the Vercel origin.
- Set `OPENROUTER_API_KEY` on Render for model-written narratives; without it the
  deployment still works end to end on the fallback.
- Render's free tier sleeps: the first request after idle can take ~30s.
- `DB_PATH=:memory:` there, because the free tier's filesystem is ephemeral
  anyway; the sample days re-seed at startup.
