'use client';
/** Small SVG charts. They plot solved values only, and say so where downsampled. */
import { DayRow, Block, MonthlyRow } from '@/lib/api';
import { crore, num } from '@/lib/format';

const INK = 'var(--text-strong)', MUTED = 'var(--text-muted)', RULE = 'var(--border-subtle)';
const GOLD = '#CEAC10', BLUE = '#5B8DC4', GREEN = '#3E8E5A', LEAD = '#A79F89', RED = '#C0453B';

function Frame({ w, h, children, pad = 30 }: {
  w: number; h: number; pad?: number; children: React.ReactNode;
}) {
  return (
    <svg viewBox={`0 0 ${w} ${h}`} style={{ width: '100%', height: 'auto' }}>
      <line x1={pad} y1={h - 22} x2={w - 6} y2={h - 22} stroke={RULE} />
      {children}
    </svg>
  );
}

/** The selected day at full 15-minute resolution — no downsampling here. */
export function DayChart({ rows, cursor, onPick }: {
  rows: Block[]; cursor: number; onPick: (i: number) => void;
}) {
  const w = 720, h = 190, pad = 34, bot = h - 22, top = 10;
  if (!rows.length) return <div className="hint">no blocks loaded</div>;
  const val = (r: Block, k: string) => Number(r[k] ?? 0);
  const useCols = Object.keys(rows[0]).filter((k) => k.startsWith('use_'));
  const series = rows.map((r) => ({
    load: val(r, 'load_mw'),
    re: useCols.reduce((s, k) => s + val(r, k), 0),
    imp: val(r, 'import_utility_mw') + val(r, 'import_market_mw'),
    dis: val(r, 'battery_discharge_mw'),
    ch: val(r, 'battery_charge_mw'),
  }));
  const max = Math.max(1, ...series.map((s) => Math.max(s.load, s.re, s.imp + s.dis)));
  const X = (i: number) => pad + ((w - pad - 6) * i) / Math.max(1, rows.length - 1);
  const Y = (v: number) => bot - ((bot - top) * v) / max;
  const line = (key: keyof (typeof series)[0]) =>
    series.map((s, i) => `${i ? 'L' : 'M'}${X(i)},${Y(s[key] as number)}`).join('');
  return (
    <Frame w={w} h={h}>
      {[0, 0.5, 1].map((f) => (
        <g key={f}>
          <line x1={pad} y1={Y(max * f)} x2={w - 6} y2={Y(max * f)} stroke={RULE}
                strokeDasharray="2 3" />
          <text x={pad - 5} y={Y(max * f) + 3} textAnchor="end" fontSize="8"
                fontFamily="var(--font-mono)" fill={MUTED}>{num(max * f, 1)}</text>
        </g>
      ))}
      <path d={line('re')} fill="none" stroke={GOLD} strokeWidth="1.6" />
      <path d={line('imp')} fill="none" stroke={BLUE} strokeWidth="1.4" />
      <path d={line('dis')} fill="none" stroke={GREEN} strokeWidth="1.2" />
      <path d={line('ch')} fill="none" stroke={LEAD} strokeWidth="1.2" strokeDasharray="3 2" />
      <path d={line('load')} fill="none" stroke={INK} strokeWidth="1.8" />
      <line x1={X(cursor)} y1={top} x2={X(cursor)} y2={bot} stroke={GOLD} strokeWidth="1.4" />
      <circle cx={X(cursor)} cy={Y(series[cursor]?.load ?? 0)} r="3.2" fill={GOLD} />
      {rows.map((_, i) => (
        <rect key={i} x={X(i) - 3} y={top} width="6" height={bot - top} fill="transparent"
              style={{ cursor: 'pointer' }} onClick={() => onPick(i)} />
      ))}
      <text x={pad} y={h - 8} fontSize="8" fontFamily="var(--font-mono)" fill={MUTED}>
        LOAD · RENEWABLE · IMPORT · DISCHARGE · CHARGE — 96 SOLVED BLOCKS
      </text>
    </Frame>
  );
}

/** State of charge across the selected day. */
export function SocChart({ rows, cursor, capMwh }: {
  rows: Block[]; cursor: number; capMwh: number;
}) {
  const w = 720, h = 120, pad = 34, bot = h - 22, top = 8;
  if (!rows.length || capMwh <= 0) {
    return <div className="hint">no storage in this scenario, so there is no state of charge to plot.</div>;
  }
  const soc = rows.map((r) => Number(r['soc_end_mwh'] ?? 0));
  const X = (i: number) => pad + ((w - pad - 6) * i) / Math.max(1, rows.length - 1);
  const Y = (v: number) => bot - ((bot - top) * v) / capMwh;
  return (
    <Frame w={w} h={h}>
      <path d={soc.map((v, i) => `${i ? 'L' : 'M'}${X(i)},${Y(v)}`).join('')
            + `L${X(soc.length - 1)},${bot}L${X(0)},${bot}Z`} fill={GOLD} fillOpacity="0.16" />
      <path d={soc.map((v, i) => `${i ? 'L' : 'M'}${X(i)},${Y(v)}`).join('')} fill="none"
            stroke={GOLD} strokeWidth="1.7" />
      <line x1={X(cursor)} y1={top} x2={X(cursor)} y2={bot} stroke={INK} strokeWidth="1" />
      <text x={pad - 5} y={Y(capMwh) + 3} textAnchor="end" fontSize="8"
            fontFamily="var(--font-mono)" fill={MUTED}>{num(capMwh, 0)}</text>
      <text x={pad} y={h - 8} fontSize="8" fontFamily="var(--font-mono)" fill={MUTED}>
        STATE OF CHARGE, MWh — CARRIED ACROSS MIDNIGHT, NEVER RESET
      </text>
    </Frame>
  );
}

/** Monthly utility bill: energy and the demand charge it is billed alongside. */
export function MonthlyChart({ rows }: { rows: MonthlyRow[] }) {
  const w = 720, h = 170, pad = 46, bot = h - 26, top = 10;
  if (!rows.length) return <div className="hint">no monthly ledger</div>;
  const tot = rows.map((r) => r.utility_energy_inr + r.demand_charge_inr);
  const max = Math.max(1, ...tot);
  const bw = (w - pad - 8) / rows.length;
  const Y = (v: number) => bot - ((bot - top) * v) / max;
  return (
    <Frame w={w} h={h}>
      {rows.map((r, i) => {
        const x = pad + i * bw;
        const e = Y(r.utility_energy_inr), d = Y(r.utility_energy_inr + r.demand_charge_inr);
        return (
          <g key={r.month}>
            <rect x={x + 3} y={e} width={bw - 6} height={bot - e} fill={GOLD} />
            <rect x={x + 3} y={d} width={bw - 6} height={e - d} fill={LEAD} />
            <text x={x + bw / 2} y={h - 12} textAnchor="middle" fontSize="7.5"
                  fontFamily="var(--font-mono)" fill={MUTED}>{r.month.slice(5)}</text>
          </g>
        );
      })}
      <text x={pad - 6} y={Y(max) + 3} textAnchor="end" fontSize="8"
            fontFamily="var(--font-mono)" fill={MUTED}>{crore(max)}</text>
      <text x={pad} y={h - 2} fontSize="8" fontFamily="var(--font-mono)" fill={MUTED}>
        UTILITY ENERGY (GOLD) AND DEMAND CHARGE (GREY), PER BILLING MONTH
      </text>
    </Frame>
  );
}

/** Where the annual cost goes. */
export function CostBars({ ledger }: { ledger: Record<string, number> }) {
  const items = Object.entries(ledger)
    .filter(([k, v]) => k !== 'total_annual_cost' && Math.abs(v) > 1)
    .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
  const max = Math.max(...items.map(([, v]) => Math.abs(v)), 1);
  const label = (k: string) => k.replace(/_/g, ' ');
  return (
    <div>
      {items.map(([k, v]) => (
        <div key={k} style={{ margin: '7px 0' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11 }}>
            <span style={{ color: 'var(--text-body)' }}>{label(k)}</span>
            <span className="mono" style={{ color: v < 0 ? GREEN : INK }}>{crore(v)}</span>
          </div>
          <div style={{ height: 5, background: 'var(--surface-sunken)', borderRadius: 2 }}>
            <div style={{ height: '100%', width: `${(Math.abs(v) / max) * 100}%`,
                          background: v < 0 ? GREEN : GOLD, borderRadius: 2 }} />
          </div>
        </div>
      ))}
    </div>
  );
}

/** Whole-year context. Downsampled to days for plotting only. */
export function YearChart({ days, onPickDay, selected }: {
  days: DayRow[]; onPickDay: (i: number) => void; selected: number;
}) {
  const w = 720, h = 150, pad = 40, bot = h - 24, top = 8;
  if (!days.length) return <div className="hint">loading the year…</div>;
  const max = Math.max(1, ...days.map((d) => d.load_mwh));
  const X = (i: number) => pad + ((w - pad - 6) * i) / Math.max(1, days.length - 1);
  const Y = (v: number) => bot - ((bot - top) * v) / max;
  const line = (k: keyof DayRow, c: string, sw = 1.2) => (
    <path d={days.map((d, i) => `${i ? 'L' : 'M'}${X(i)},${Y(Number(d[k]))}`).join('')}
          fill="none" stroke={c} strokeWidth={sw} />
  );
  return (
    <Frame w={w} h={h}>
      {line('load_mwh', INK, 1.1)}
      {line('renewable_mwh', GOLD, 1.1)}
      {line('import_utility_mwh', BLUE, 1)}
      {line('curtailed_mwh', RED, 0.9)}
      <line x1={X(selected)} y1={top} x2={X(selected)} y2={bot} stroke={GOLD} strokeWidth="1.5" />
      {days.map((_, i) => (
        <rect key={i} x={X(i) - 1} y={top} width="2.4" height={bot - top} fill="transparent"
              style={{ cursor: 'pointer' }} onClick={() => onPickDay(i)} />
      ))}
      <text x={pad} y={h - 8} fontSize="8" fontFamily="var(--font-mono)" fill={MUTED}>
        DAILY ENERGY — LOAD · RENEWABLE · UTILITY IMPORT · CURTAILED (PLOT DOWNSAMPLE ONLY)
      </text>
    </Frame>
  );
}
