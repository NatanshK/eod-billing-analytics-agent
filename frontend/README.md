# Frontend — EOD Billing & Analytics

Vite + React + TypeScript. Three screens over the reconciliation API, behind a
persistent icon rail. See the [root README](../README.md) for the project as a
whole and for the API contracts these screens consume.

```bash
npm install
npm run dev      # http://localhost:5173
npm run build    # tsc -b && vite build  →  dist/
npm run lint     # oxlint
```

The dev server expects the backend on `http://localhost:8000`. Point it
elsewhere with `VITE_API_BASE_URL` (see `.env.example`); there is no proxy, the
browser calls the API directly, so the backend's `ALLOWED_ORIGINS` has to
include this origin.

## Layout

```
src/
  api/          client.ts — typed fetch layer; types.ts — response shapes
  components/   HourChart (hand-rolled CSS), common.tsx (Card, states, banner)
  layout/       AppShell — icon rail + PageHead with the date stepper
  lib/          money.ts — port of the backend formatter; useReport.ts — async hook
  pages/        Reconciliation, Analytics, Narrative — one per screen
  state/        day-context.ts (context + useDay), DayContext.tsx (provider)
```

## Three things worth knowing

**The selected clinic-day lives above the router.** `DayProvider` wraps the
route tree in `main.tsx`, so moving between screens keeps the day — the three
screens are three views of one report, not three independent pages.

**`lib/money.ts` is a character-for-character port of `backend/app/core/money.py`.**
The Traced Figures panel compares rendered strings against the backend's own
formatting, so a drift between the two formatters would look to a reviewer like
a grounding failure rather than a display bug. Money arrives as integer paise
with a `_display` string alongside it; the UI prefers the server's string and
never re-derives formatting from the integer.

**The chart is hand-rolled CSS.** Ten buckets does not justify a charting
dependency. It is one series, so there is no legend; the peak hour is both the
darkest step and directly labelled, and refund-heavy hours render below the
baseline with their values written out — nothing is carried by colour alone. A
"Show figures" toggle exposes the same numbers as a table.

## Deploying

`vercel.json` sets the Vite preset and rewrites unknown paths to `index.html`,
so a hard refresh on `/analytics` or `/narrative` still serves the app shell.
Set `VITE_API_BASE_URL` in the Vercel project to the deployed backend URL — it
is read at build time, so changing it needs a redeploy, not just a restart.
