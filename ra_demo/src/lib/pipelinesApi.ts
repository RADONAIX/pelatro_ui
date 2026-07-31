// API client for the pipelines backend.
// Configurable via VITE_PIPELINES_API_BASE / VITE_PIPELINES_API_TOKEN.
// NOTE: backend must allow CORS for the browser origin serving this app.
import { useEffect, useState } from "react";
import { losslessParse } from "@/lib/api";
import type { BatchLog } from "@/lib/airBatches";
import type { FileLog } from "@/lib/airFiles";

const API_BASE = (import.meta as any).env?.VITE_PIPELINES_API_BASE ?? "http://localhost:8000";

function getToken(): string {
  if (typeof window !== "undefined") {
    const t = window.sessionStorage.getItem("radonaix_token") || window.sessionStorage.getItem("pipelines_api_token");
    if (t) return t;
  }
  return "";
}

async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: {
      accept: "application/json",
      Authorization: `Bearer ${getToken()}`,
    },
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  // Parse losslessly so 16+ digit sequence numbers / IDs don't get rounded by
  // the browser's JSON number handling (same guard as the axios `api` client).
  return losslessParse(await res.text()) as T;
}

// Backends sometimes wrap the array — normalize common shapes.
function unwrapArray<T>(payload: any): T[] {
  if (Array.isArray(payload)) return payload as T[];
  if (Array.isArray(payload?.data)) return payload.data as T[];
  if (Array.isArray(payload?.batches)) return payload.batches as T[];
  if (Array.isArray(payload?.files)) return payload.files as T[];
  if (Array.isArray(payload?.items)) return payload.items as T[];
  if (Array.isArray(payload?.results)) return payload.results as T[];
  return [];
}

export async function fetchBatches(hours: number): Promise<BatchLog[]> {
  const raw = await apiGet<any>(`/api/pipelines/batches?hours=${hours}`);

  const candidates = Array.isArray(raw) ? raw
    : Array.isArray(raw?.data) ? raw.data
      : Array.isArray(raw?.batches) ? raw.batches
        : Array.isArray(raw?.items) ? raw.items
          : Array.isArray(raw?.results) ? raw.results
            : [];

  const rows: BatchLog[] = Array.isArray(candidates) && candidates.every((item) => item?.rows && Array.isArray(item.rows))
    ? candidates.flatMap((item) => item.rows)
    : unwrapArray<BatchLog>(raw);

  console.log("[pipelinesApi] fetchBatches normalized rows:", rows);
  return rows;
}

export async function fetchBatchFiles(batchId: string): Promise<FileLog[]> {
  const raw = await apiGet<any>(
    `/api/pipelines/batches/${encodeURIComponent(batchId)}/files`,
  );
  return unwrapArray<FileLog>(raw);
}

type AsyncState<T> = { data: T | null; loading: boolean; error: string | null };

export function useBatches(hours: number): AsyncState<BatchLog[]> & { refetch: () => void } {
  const [state, setState] = useState<AsyncState<BatchLog[]>>({
    data: null,
    loading: true,
    error: null,
  });
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: null }));
    fetchBatches(hours)
      .then((data) => {
        if (!cancelled) setState({ data, loading: false, error: null });
      })
      .catch((err) => {
        if (!cancelled)
          setState({ data: null, loading: false, error: err?.message ?? "Failed to load batches" });
      });
    return () => {
      cancelled = true;
    };
  }, [hours, tick]);

  return { ...state, refetch: () => setTick((t) => t + 1) };
}

export function useBatchFiles(batchId: string | null): AsyncState<FileLog[]> {
  const [state, setState] = useState<AsyncState<FileLog[]>>({
    data: null,
    loading: false,
    error: null,
  });
  useEffect(() => {
    if (!batchId) {
      setState({ data: null, loading: false, error: null });
      return;
    }
    let cancelled = false;
    setState({ data: null, loading: true, error: null });
    fetchBatchFiles(batchId)
      .then((data) => {
        if (!cancelled) setState({ data, loading: false, error: null });
      })
      .catch((err) => {
        if (!cancelled)
          setState({ data: null, loading: false, error: err?.message ?? "Failed to load files" });
      });
    return () => {
      cancelled = true;
    };
  }, [batchId]);
  return state;
}
