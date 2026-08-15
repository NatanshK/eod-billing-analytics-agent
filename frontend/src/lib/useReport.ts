/** Fetch-on-day-change hook shared by the three screens. */

import { useEffect, useState } from "react";

import { ApiError } from "../api/client";

export interface Async<T> {
  data: T | null;
  loading: boolean;
  error: ApiError | null;
}

/**
 * Runs `fetcher` whenever the day changes, discarding superseded results.
 * Stepping quickly through dates must not let an earlier response overwrite a
 * later one.
 */
export function useAsync<T>(
  fetcher: (() => Promise<T>) | null,
  deps: unknown[],
): Async<T> & { reload: () => void } {
  const [state, setState] = useState<Async<T>>({
    data: null,
    loading: true,
    error: null,
  });
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    if (!fetcher) {
      setState({ data: null, loading: true, error: null });
      return;
    }

    let cancelled = false;
    setState((prev) => ({ ...prev, loading: true, error: null }));

    fetcher()
      .then((data) => {
        if (!cancelled) setState({ data, loading: false, error: null });
      })
      .catch((error: ApiError) => {
        if (!cancelled) setState({ data: null, loading: false, error });
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  return { ...state, reload: () => setNonce((n) => n + 1) };
}
