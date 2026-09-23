/** Indian number conventions, and units that are always stated. */
export const inr = (v: number | null | undefined, d = 0) =>
  v == null || !Number.isFinite(v) ? '—'
    : `₹${Math.abs(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d })}`
      .replace('₹', v < 0 ? '−₹' : '₹');

export const crore = (v: number | null | undefined, d = 2) =>
  v == null || !Number.isFinite(v) ? '—' : `${v < 0 ? '−' : ''}₹${(Math.abs(v) / 1e7).toFixed(d)} Cr`;

export const num = (v: number | null | undefined, d = 2) =>
  v == null || !Number.isFinite(v) ? '—'
    : v.toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });

export const mw = (v: number | null | undefined, d = 2) => `${num(v, d)} MW`;
export const mwh = (v: number | null | undefined, d = 1) => `${num(v, d)} MWh`;
export const pct = (v: number | null | undefined, d = 1) =>
  v == null || !Number.isFinite(v) ? '—' : `${(v * 100).toFixed(d)}%`;

/** Local wall-clock label for a UTC interval start. */
export const istLabel = (iso: string) =>
  new Date(iso).toLocaleString('en-IN', {
    timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short',
    hour: '2-digit', minute: '2-digit', hour12: false,
  });

export const istDate = (iso: string) =>
  new Date(iso).toLocaleDateString('en-IN', {
    timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short', year: 'numeric' });
