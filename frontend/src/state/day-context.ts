/**
 * The shape of the selected clinic-day, and the hook that reads it.
 *
 * Split from the provider so that file exports a component and nothing else. A
 * module mixing component and non-component exports opts out of React Fast
 * Refresh, which means a full reload on every edit to a screen.
 */

import { createContext, useContext } from "react";

import type { ApiError } from "../api/client";
import type { DaySummary } from "../api/types";

export interface DayContextValue {
  clinicId: string | null;
  clinicName: string;
  clinicLocation: string;
  clinicOwner: string;
  date: string | null;
  days: DaySummary[];
  loading: boolean;
  error: ApiError | null;
  setDate: (date: string) => void;
  /** Step through the ingested days; the picker is a stepper, not a calendar. */
  step: (delta: number) => void;
}

export const DayContext = createContext<DayContextValue | null>(null);

export function useDay(): DayContextValue {
  const value = useContext(DayContext);
  if (!value) throw new Error("useDay must be used inside a DayProvider");
  return value;
}
