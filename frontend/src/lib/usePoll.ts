"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { isAbort } from "./api";

interface Options<T> {
  /** Changing the key restarts the loop (new filters, new document id). */
  key: string;
  intervalMs?: number;
  /** Called with each fresh result; return false to stop polling. refresh() restarts it. */
  continueWhile?: (data: T) => boolean;
}

const ERROR_BACKOFF_MS = 5000;

/**
 * Polls fn on a timeout chain (never setInterval, so requests cannot overlap).
 * One loop per effect run; cleanup cancels the timer and aborts the in-flight request,
 * and a cancelled run never writes state, so StrictMode double-mounts, key changes and
 * unmounts leave no stale or duplicate timers.
 */
export function usePoll<T>(fn: (signal: AbortSignal) => Promise<T>, opts: Options<T>) {
  const { key, intervalMs = 2000 } = opts;
  const fnRef = useRef(fn);
  const contRef = useRef(opts.continueWhile);
  fnRef.current = fn;
  contRef.current = opts.continueWhile;

  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const refreshRef = useRef<() => void>(() => {});

  useEffect(() => {
    let cancelled = false;
    let running = false;
    let rerun = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let ac: AbortController | undefined;
    let last: T | null = null;

    setLoading(true);
    setError(null);

    const schedule = (ms: number) => {
      if (timer !== undefined) clearTimeout(timer);
      timer = setTimeout(tick, ms);
    };

    async function tick() {
      timer = undefined;
      if (cancelled || running) return;
      if (typeof document !== "undefined" && document.hidden) {
        schedule(intervalMs); // paused while the tab is hidden
        return;
      }
      running = true;
      ac = new AbortController();
      let failed = false;
      try {
        const result = await fnRef.current(ac.signal);
        if (cancelled) return;
        last = result;
        setData(result);
        setError(null);
      } catch (e) {
        if (cancelled || isAbort(e)) return;
        failed = true;
        setError(e as Error);
      } finally {
        running = false;
        if (!cancelled) setLoading(false);
      }
      if (cancelled) return;
      if (rerun) {
        rerun = false;
        schedule(0);
      } else if (failed) {
        if (last === null || contRef.current?.(last) !== false) schedule(ERROR_BACKOFF_MS);
      } else if (last !== null && contRef.current?.(last) === false) {
        // terminal: stop until refresh()
      } else {
        schedule(intervalMs);
      }
    }

    refreshRef.current = () => {
      if (cancelled) return;
      if (running) rerun = true;
      else schedule(0);
    };

    tick();

    return () => {
      cancelled = true;
      if (timer !== undefined) clearTimeout(timer);
      ac?.abort();
    };
  }, [key, intervalMs]);

  const refresh = useCallback(() => refreshRef.current(), []);
  return { data, error, loading, refresh, mutate: setData };
}
