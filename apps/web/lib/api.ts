/** Typed client for the solver service. Nothing here computes energy or money: every
 *  number the twin shows comes from a solved, certified run. */
export type RunSummary = {
  run_id: string; mode: string; status: string;
  validation_status?: string; coverage?: string;
  objective_inr_year: number | null; best_bound: number | null; gap: number | null;
  wall_seconds: number; variables: number; constraints: number; binaries: number;
  model_version: string; input_fingerprint: string; baseline_run_id?: string | null;
  fixed_capacities?: Record<string, number> | null;
  capacities: Record<string, number>;
  ledger: Record<string, number>;
  monthly?: MonthlyRow[];
  validation?: ValidationReport;
};
export type MonthlyRow = {
  month: string; billing_demand_mw: number; demand_charge_inr: number;
  utility_energy_mwh: number; utility_energy_inr: number; market_energy_mwh: number;
};
export type ValidationReport = {
  status: string; blocks_checked: number; blocks_expected: number;
  checks_run: string[]; issues: { check: string; severity: string; detail: string;
    timestamp_utc: string | null; residual: number; tolerance: number; units: string }[];
  max_residual: Record<string, number>; tolerances: Record<string, number>;
  input_hash: string; model_version: string; solver_version: string;
};
export type Block = Record<string, number | string>;
export type DispatchWindow = {
  run_id: string; n_blocks: number; start: number; count: number;
  columns: string[]; rows: Block[];
};
export type DayRow = {
  day: string; load_mwh: number; renewable_mwh: number; import_utility_mwh: number;
  import_market_mwh: number; discharge_mwh: number; charge_mwh: number;
  curtailed_mwh: number; peak_import_mw: number; soc_min_mwh: number; soc_max_mwh: number;
};
export type Job = {
  job_id: string; status: string; mode: string; run_id: string | null;
  message: string; input_fingerprint: string | null; baseline_run_id: string | null;
};
export type ProjectMeta = {
  project_id: string; name: string; timezone: string; currency: string;
  provenance: string; illustrative_only: boolean;
  site: Record<string, number>;
  options: Record<string, { min_mw: number; max_mw: number; technology: string; route: string }>;
  battery: { min_power_mw: number; max_power_mw: number; min_energy_mwh: number;
    max_energy_mwh: number; max_c_rate: number };
  tariff: { contract_demand_mw: number; demand_basis: string; duty_frac: number };
  finance: Record<string, unknown>;
};

const base = '/api';
async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const r = await fetch(`${base}${path}`, { signal, cache: 'no-store' });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} on ${path}`);
  return r.json() as Promise<T>;
}

export const api = {
  health: () => get<{ ok: boolean; runs: number }>('/health'),
  project: (year = 2026) => get<ProjectMeta>(`/project?year=${year}`),
  runs: () => get<RunSummary[]>('/runs'),
  run: (id: string) => get<RunSummary>(`/runs/${id}`),
  dispatch: (id: string, start: number, limit: number, signal?: AbortSignal) =>
    get<DispatchWindow>(`/runs/${id}/dispatch?start=${start}&limit=${limit}`, signal),
  daily: (id: string) => get<{ run_id: string; days: DayRow[] }>(`/runs/${id}/daily`),
  job: (id: string) => get<Job>(`/jobs/${id}`),
  submit: async (body: { mode: string; capacities?: Record<string, number>;
                         baseline_run_id?: string | null; year?: number }) => {
    const r = await fetch(`${base}/scenarios`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ year: 2026, ...body }),
    });
    if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
    return r.json() as Promise<{ job_id: string; status: string }>;
  },
  cancel: (id: string) => fetch(`${base}/jobs/${id}/cancel`, { method: 'POST' }),
};
