import { useEffect, useState } from "react";

// ---------------------------------------------------------------------------
// "The set of generated reports has changed."
//
// Authoring a rule creates, changes or removes a report, but the Reports menu
// is rendered by a different part of the tree — the sidebar — which had no way
// to know. A rule you created and activated therefore did not appear until the
// page was reloaded, which reads as "the report was never created".
//
// A module-level counter rather than a context: the publisher (the rules API
// client) and the subscriber (the sidebar's catalog hook) have no common
// ancestor to hang a provider from, and a shared parent would exist only to
// carry this one signal.
// ---------------------------------------------------------------------------

let version = 0;
const listeners = new Set<() => void>();

/** Call after any mutation that can add, change or remove a report. */
export function notifyReportsChanged(): void {
  version += 1;
  for (const listener of listeners) listener();
}

/**
 * Re-renders the caller whenever the report set changes.
 *
 * Returns the current version so it can be used as an effect dependency — the
 * value itself means nothing beyond "this differs from last time".
 */
export function useReportsVersion(): number {
  const [current, setCurrent] = useState(version);
  useEffect(() => {
    const listener = () => setCurrent(version);
    listeners.add(listener);
    // A mutation between first render and subscribing would otherwise be
    // missed, so re-read on mount.
    listener();
    return () => {
      listeners.delete(listener);
    };
  }, []);
  return current;
}
