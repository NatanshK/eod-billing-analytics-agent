/** The persistent frame: icon rail on the left, routed screen on the right. */

import { NavLink, Outlet } from "react-router-dom";

import { useDay } from "../state/day-context";

const NAV = [
  { to: "/reconciliation", label: "EOD Reconciliation", icon: ReconciliationIcon },
  { to: "/analytics", label: "Analytics", icon: AnalyticsIcon },
  { to: "/narrative", label: "AI Narrative Summary", icon: NarrativeIcon },
];

export function AppShell() {
  return (
    <div className="shell">
      <nav className="rail" aria-label="Sections">
        <div className="rail__mark" aria-hidden="true">
          S
        </div>
        {NAV.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            title={label}
            aria-label={label}
            className={({ isActive }) =>
              `rail__link${isActive ? " rail__link--active" : ""}`
            }
          >
            <Icon />
          </NavLink>
        ))}
        <div className="rail__spacer" />
      </nav>

      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}

/** Title block plus the date stepper, shared by all three screens. */
export function PageHead({ title, subtitle }: { title: string; subtitle?: string }) {
  const { date, days, step, clinicName, clinicLocation } = useDay();

  const index = days.findIndex((d) => d.business_date === date);
  const current = index === -1 ? null : days[index];

  return (
    <header className="page-head">
      <div>
        <h1 className="page-head__title">{title}</h1>
        <p className="page-head__subtitle">
          {subtitle ?? [clinicName, clinicLocation].filter(Boolean).join(" — ")}
        </p>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <button
          className="btn"
          onClick={() => step(1)}
          disabled={index === -1 || index >= days.length - 1}
          aria-label="Previous day"
          style={{ padding: "6px 9px" }}
        >
          ‹
        </button>
        <span className="date-picker" aria-live="polite">
          {current?.business_date_display ?? "—"}
        </span>
        <button
          className="btn"
          onClick={() => step(-1)}
          disabled={index <= 0}
          aria-label="Next day"
          style={{ padding: "6px 9px" }}
        >
          ›
        </button>
      </div>
    </header>
  );
}

/* Inline SVGs — three icons do not warrant an icon dependency. */

function ReconciliationIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <rect x="2" y="2.5" width="12" height="11" rx="1.6" stroke="currentColor" strokeWidth="1.4" />
      <path d="M5 6.5h6M5 9.5h3.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}

function AnalyticsIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M3 13V8.5M7 13V3.5M11 13v-6"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
      />
    </svg>
  );
}

function NarrativeIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M13.5 8c0 2.9-2.5 5.2-5.5 5.2-.8 0-1.6-.15-2.3-.45L2.5 13.5l.8-2.9A5 5 0 0 1 2.5 8c0-2.9 2.5-5.2 5.5-5.2S13.5 5.1 13.5 8Z"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
    </svg>
  );
}
