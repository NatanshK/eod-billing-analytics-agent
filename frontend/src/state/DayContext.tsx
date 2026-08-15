/**
 * The clinic and business date every screen is looking at.
 *
 * Held above the router, so switching screens keeps the selected day. The three
 * screens are three views of one report, not three independent pages.
 */

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import { api, ApiError } from "../api/client";
import type { DaySummary } from "../api/types";
import { DayContext, type DayContextValue } from "./day-context";

export function DayProvider({ children }: { children: ReactNode }) {
  const [clinicId, setClinicId] = useState<string | null>(null);
  const [clinic, setClinic] = useState({ name: "", location: "", owner: "" });
  const [days, setDays] = useState<DaySummary[]>([]);
  const [date, setDate] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const { clinics } = await api.clinics();
        if (cancelled) return;

        if (clinics.length === 0) {
          setError(
            new ApiError(
              404,
              "no_clinics",
              "No billing logs have been ingested yet",
              "POST a day's log to /api/v1/billing-logs to get started.",
            ),
          );
          return;
        }

        const first = clinics[0];
        const detail = await api.days(first.clinic_id);
        if (cancelled) return;

        setClinicId(detail.clinic_id);
        setClinic({ name: detail.name, location: detail.location, owner: detail.owner });
        setDays(detail.days);
        setDate(detail.days[0]?.business_date ?? null);
      } catch (caught) {
        if (!cancelled) setError(caught as ApiError);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  const step = useCallback(
    (delta: number) => {
      setDate((current) => {
        const index = days.findIndex((d) => d.business_date === current);
        const next = index + delta;
        if (index === -1 || next < 0 || next >= days.length) return current;
        return days[next].business_date;
      });
    },
    [days],
  );

  const value = useMemo<DayContextValue>(
    () => ({
      clinicId,
      clinicName: clinic.name,
      clinicLocation: clinic.location,
      clinicOwner: clinic.owner,
      date,
      days,
      loading,
      error,
      setDate,
      step,
    }),
    [clinicId, clinic, date, days, loading, error, step],
  );

  return <DayContext.Provider value={value}>{children}</DayContext.Provider>;
}
