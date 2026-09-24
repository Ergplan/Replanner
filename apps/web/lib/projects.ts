/** Client for the project store: the inputs a user owns and the series they upload. */

export type ProjectListItem = { project_id: string; name: string; sample: boolean };
export type SeriesStatus = {
  column: string; label: string; source: string; units: string[];
  annual_mwh?: number; peak_mw?: number; annual_cf?: number; outage_hours?: number;
  mean?: number; min?: number; max?: number;
};
// The full ProjectInputs document, edited in place and sent back whole. The backend
// schema is the authority on what is valid, so it is not re-typed field by field here.
export type Inputs = Record<string, any>;           // eslint-disable-line @typescript-eslint/no-explicit-any
export type ProjectDoc = {
  project_id: string; sample: boolean; year: number;
  inputs: Inputs; problems: string[]; series: SeriesStatus[];
};
export type FieldError = { field: string; message: string };
export type UploadSummary = {
  ok: boolean; column: string; errors: string[]; warnings: string[]; resolution_min: number | null;
  rows_read: number; rows_used: number; time_column: string; value_column: string;
  blocks?: number; annual_mwh?: number; peak_mw?: number; mean_mw?: number;
  annual_cf?: number; outage_hours?: number; mean?: number; min?: number; max?: number;
};

export class ApiError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
}

const base = '/api';
async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${base}${path}`, { cache: 'no-store', ...init });
  const body = await r.json().catch(() => null);
  if (!r.ok) throw new ApiError(r.status, body?.detail ?? body ?? r.statusText);
  return body as T;
}
const json = (method: string, data: unknown): RequestInit => ({
  method, headers: { 'content-type': 'application/json' }, body: JSON.stringify(data),
});

export const projects = {
  list: () => call<ProjectListItem[]>('/projects'),
  create: (name: string) => call<{ project_id: string }>('/projects', json('POST', { name })),
  get: (pid: string) => call<ProjectDoc>(`/projects/${pid}`),
  save: (pid: string, inputs: Inputs) => call<ProjectDoc>(`/projects/${pid}/inputs`, json('PUT', inputs)),
  uploadSeries: (pid: string, column: string, file: File, unit: string) => {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('unit', unit);
    return call<UploadSummary>(`/projects/${pid}/series/${column}`, { method: 'POST', body: fd });
  },
  useSample: (pid: string, column: string) =>
    call<ProjectDoc>(`/projects/${pid}/series/${column}`, { method: 'DELETE' }),
};

/** The project a page is about, from ?project=, else the sample. */
export function projectFromUrl(): string {
  if (typeof window === 'undefined') return SAMPLE_ID;
  return new URLSearchParams(window.location.search).get('project') || SAMPLE_ID;
}
export const SAMPLE_ID = 'seed-industrial-mh';

/** "18-21, 23" -> [18,19,20,21,23]; "22-5" wraps past midnight. */
export function parseRanges(text: string, lo: number, hi: number): number[] | null {
  const out = new Set<number>();
  for (const part of text.split(',').map((s) => s.trim()).filter(Boolean)) {
    const m = part.match(/^(\d+)\s*(?:-\s*(\d+))?$/);
    if (!m) return null;
    const a = Number(m[1]); const b = m[2] === undefined ? a : Number(m[2]);
    if (a < lo || a > hi || b < lo || b > hi) return null;
    if (a <= b) for (let x = a; x <= b; x++) out.add(x);
    else { for (let x = a; x <= hi; x++) out.add(x); for (let x = lo; x <= b; x++) out.add(x); }
  }
  return [...out].sort((x, y) => x - y);
}

/** [18,19,20,21,23] -> "18-21, 23"; the inverse of parseRanges for display. */
export function formatRanges(xs: number[]): string {
  const s = [...xs].sort((a, b) => a - b);
  const parts: string[] = [];
  for (let i = 0; i < s.length; i++) {
    let j = i;
    while (j + 1 < s.length && s[j + 1] === s[j] + 1) j++;
    parts.push(i === j ? `${s[i]}` : `${s[i]}-${s[j]}`);
    i = j;
  }
  return parts.join(', ');
}
