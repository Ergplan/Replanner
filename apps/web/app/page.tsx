'use client';
/**
 * The twin.
 *
 * One rule governs this file: every number on screen comes from a solved run, addressed
 * by run id and block index. Nothing is computed here that the optimiser should have
 * computed, and the capacity sliders do not estimate anything — they submit a scenario
 * and wait for the backend to solve it.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import IsoTwin, { Flows, Sizes } from '@/components/IsoTwin';
import { CostBars, DayChart, MonthlyChart, SocChart, YearChart } from '@/components/Charts';
import { api, Block, DayRow, Job, ProjectMeta, RunSummary } from '@/lib/api';
import { crore, inr, istDate, istLabel, mw, num, pct } from '@/lib/format';

const BLOCKS_PER_DAY = 96;
const CAP_KEYS = ['solar_onsite_mw', 'solar_remote_mw', 'wind_remote_mw',
                  'bess_power_mw', 'bess_energy_mwh'] as const;
type CapKey = (typeof CAP_KEYS)[number];
const SPEEDS = [1, 2, 4, 8, 16];

const num0 = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : 0);

export default function Page() {
  const [meta, setMeta] = useState<ProjectMeta | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [baseline, setBaseline] = useState<RunSummary | null>(null);   // the Mode A optimum
  const [shown, setShown] = useState<RunSummary | null>(null);         // what the twin is showing
  const [mode, setMode] = useState<'find_optimum' | 'manual'>('find_optimum');
  const [caps, setCaps] = useState<Record<CapKey, number> | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [stale, setStale] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const [day, setDay] = useState(0);
  const [cursor, setCursor] = useState(52);
  const [rows, setRows] = useState<Block[]>([]);
  const [days, setDays] = useState<DayRow[]>([]);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(4);
  const [showTable, setShowTable] = useState(false);

  const submitted = useRef<string | null>(null);   // the job we are currently willing to accept

  // ---- discover the certified optimum ----------------------------------------
  useEffect(() => {
    (async () => {
      try {
        const [m, list] = await Promise.all([api.project(), api.runs()]);
        setMeta(m); setRuns(list);
        const opt = list.find((r) => r.mode === 'find_optimum'
          && r.validation_status?.startsWith('certified'));
        if (opt) {
          const full = await api.run(opt.run_id);
          setBaseline(full); setShown(full);
          setCaps(Object.fromEntries(
            CAP_KEYS.map((k) => [k, num0(full.capacities[k])])) as Record<CapKey, number>);
        }
      } catch (e) { setErr(String(e)); }
    })();
  }, []);

  // ---- windowed dispatch for the selected day --------------------------------
  useEffect(() => {
    if (!shown) return;
    const ac = new AbortController();
    api.dispatch(shown.run_id, day * BLOCKS_PER_DAY, BLOCKS_PER_DAY, ac.signal)
      .then((w) => setRows(w.rows)).catch(() => {});
    return () => ac.abort();
  }, [shown, day]);

  useEffect(() => {
    if (!shown) return;
    api.daily(shown.run_id).then((d) => setDays(d.days)).catch(() => {});
  }, [shown]);

  // ---- playback: a step selects a real solved record -------------------------
  useEffect(() => {
    if (!playing) return;
    const id = setInterval(() => {
      setCursor((c) => {
        if (c + 1 < BLOCKS_PER_DAY) return c + 1;
        setDay((d) => (d + 1) % Math.max(1, days.length || 365));
        return 0;
      });
    }, 1000 / speed);
    return () => clearInterval(id);
  }, [playing, speed, days.length]);

  // ---- scenario submission ----------------------------------------------------
  const submit = useCallback(async (next: Record<CapKey, number>) => {
    try {
      setErr(null);
      const { job_id } = await api.submit({
        mode: 'manual', capacities: next, baseline_run_id: baseline?.run_id ?? null });
      submitted.current = job_id;                 // anything older is now obsolete
      setJob({ job_id, status: 'queued', mode: 'manual', run_id: null, message: '',
               input_fingerprint: null, baseline_run_id: baseline?.run_id ?? null });
      setStale(true);
    } catch (e) { setErr(String(e)); }
  }, [baseline]);

  // Debounce the sliders, and supersede rather than queue a run per keystroke.
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onCap = (k: CapKey, v: number) => {
    if (!caps) return;
    const next = { ...caps, [k]: v };
    setCaps(next);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => submit(next), 700);
  };

  // ---- poll the active job ----------------------------------------------------
  useEffect(() => {
    if (!job || ['succeeded', 'failed', 'cancelled'].includes(job.status)) return;
    const id = setInterval(async () => {
      try {
        const j = await api.job(job.job_id);
        if (submitted.current !== j.job_id) return;      // a newer scenario has replaced this
        setJob(j);
        if (j.status === 'succeeded' && j.run_id) {
          const full = await api.run(j.run_id);
          // Only adopt a result whose inputs match the baseline it will be compared to.
          if (baseline && full.input_fingerprint !== baseline.input_fingerprint) {
            setErr('inputs changed since the baseline was solved; rerun Find optimum');
            return;
          }
          setShown(full); setStale(false);
          setRuns(await api.runs());
        }
        if (j.status === 'failed') {
          // An infeasible scenario has something useful to say; fetch the diagnostic.
          let why = j.message || 'solve failed';
          try {
            if (j.run_id) {
              const r = await api.run(j.run_id);
              const dg = (r as unknown as { diagnostic?: {
                unserved_mwh: number; unserved_blocks: number; worst_unserved_mw: number;
                windows: { from_utc: string; hours: number }[] } }).diagnostic;
              if (dg) {
                const w = dg.windows?.[0];
                why = `This configuration cannot serve the site. `
                  + `${dg.unserved_blocks.toLocaleString('en-IN')} blocks fall short, `
                  + `${dg.unserved_mwh.toFixed(1)} MWh in total, worst `
                  + `${dg.worst_unserved_mw.toFixed(2)} MW`
                  + (w ? `, first from ${istLabel(w.from_utc)} for ${w.hours} h` : '')
                  + '. Nothing was added to make it work.';
              }
            }
          } catch { /* fall back to the raw message */ }
          setErr(why);
          setStale(false);
        }
      } catch { /* the poll simply retries */ }
    }, 1200);
    return () => clearInterval(id);
  }, [job, baseline]);

  // ---- derived ------------------------------------------------------------------
  const block = rows[cursor];
  const flows: Flows = useMemo(() => {
    const v = (k: string) => num0(block?.[k]);
    const useCols = block ? Object.keys(block).filter((k) => k.startsWith('use_')) : [];
    const curtCols = block ? Object.keys(block).filter((k) => k.startsWith('curtail_')) : [];
    const roof = v('use_solar_roof_mw');
    return {
      loadMw: v('load_mw'), roofMw: roof,
      oaSolarMw: v('use_solar_oa_mw'), oaWindMw: v('use_wind_oa_mw'),
      importUtilityMw: v('import_utility_mw'), importMarketMw: v('import_market_mw'),
      exportMw: v('export_utility_mw') + v('export_market_mw'),
      chargeMw: v('battery_charge_mw'), dischargeMw: v('battery_discharge_mw'),
      socFrac: null, curtailedMw: curtCols.reduce((s, k) => s + v(k), 0),
      ...(useCols.length ? {} : {}),
    };
  }, [block]);

  const sizes: Sizes = useMemo(() => {
    const c = shown?.capacities ?? {};
    return {
      roofMwp: num0(c.solar_onsite_mw) + num0(c.existing_solar_onsite_mw),
      oaSolarMw: num0(c.solar_remote_mw), oaWindMw: num0(c.wind_remote_mw),
      bessMw: num0(c.bess_power_mw) + num0(c.existing_bess_power_mw),
      bessMwh: num0(c.bess_energy_mwh) + num0(c.existing_bess_energy_mwh),
    };
  }, [shown]);

  const socFrac = sizes.bessMwh > 0 && block ? num0(block['soc_end_mwh']) / sizes.bessMwh : null;
  const flowsWithSoc = { ...flows, socFrac };
  const duration = sizes.bessMw > 1e-9 ? sizes.bessMwh / sizes.bessMw : null;

  // Premium is only meaningful against a certified baseline solved on identical inputs.
  const optimum = baseline?.objective_inr_year ?? null;
  const manual = shown && shown.mode === 'manual' ? shown.objective_inr_year : null;
  const premium = optimum != null && manual != null ? manual - optimum : null;
  const premiumPct = premium != null && optimum != null && optimum > 1
    ? premium / optimum : null;
  const comparable = !!baseline && !!shown
    && shown.input_fingerprint === baseline.input_fingerprint;

  const vstat = shown?.validation_status ?? shown?.validation?.status;
  const badge = vstat === 'certified' ? 'ok'
    : vstat === 'certified_incumbent' ? 'warn' : vstat ? 'bad' : 'info';

  const stamp = block ? istLabel(String(block['timestamp_utc'])) : '—';
  const resetToOptimum = () => {
    if (!baseline) return;
    setCaps(Object.fromEntries(CAP_KEYS.map((k) => [k, num0(baseline.capacities[k])])) as Record<CapKey, number>);
    setShown(baseline); setStale(false); setJob(null); setErr(null);
    submitted.current = null;     // a message about the scenario we just left is noise
  };

  // The backend refuses a manual size outside an option's range or off its unit grid, so
  // the slider offers exactly the sizes it will accept. Without a unit size the step is
  // only slider resolution.
  const optBounds = (id: string, fallbackMax: number, resolution: number):
      [number, number, number] => {
    const o = meta?.options?.[id];
    if (o && !o.enabled) return [0, 0, resolution];
    const step = o?.step_mw ?? resolution;
    const lo = o?.step_mw ? Math.ceil(o.min_mw / step - 1e-9) * step : o?.min_mw ?? 0;
    const hi = o?.step_mw ? Math.floor(o.max_mw / step + 1e-9) * step : o?.max_mw ?? fallbackMax;
    return [lo, hi, step];
  };
  const capBounds: Record<CapKey, [number, number, number]> = {
    solar_onsite_mw: optBounds('solar_roof', 8, 0.25),
    solar_remote_mw: optBounds('solar_oa', 60, 0.5),
    wind_remote_mw: optBounds('wind_oa', 60, 0.5),
    bess_power_mw: [meta?.battery?.min_power_mw ?? 0, meta?.battery?.max_power_mw ?? 25, 0.25],
    bess_energy_mwh: [meta?.battery?.min_energy_mwh ?? 0, meta?.battery?.max_energy_mwh ?? 120, 1],
  };
  const capLabel: Record<CapKey, string> = {
    solar_onsite_mw: 'Rooftop solar, behind the meter',
    solar_remote_mw: 'Open-access solar',
    wind_remote_mw: 'Open-access wind',
    bess_power_mw: 'Storage power',
    bess_energy_mwh: 'Storage energy',
  };
  const capUnit: Record<CapKey, string> = {
    solar_onsite_mw: 'MWp', solar_remote_mw: 'MW', wind_remote_mw: 'MW',
    bess_power_mw: 'MW', bess_energy_mwh: 'MWh',
  };

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand"><b>joule</b>Wise</div>
        <div className="eyebrow">ergOS · least-cost planning and dispatch</div>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
          <span className="badge info">planning &amp; simulation twin</span>
          {meta?.illustrative_only && <span className="badge warn">synthetic data</span>}
        </div>
      </header>

      <p className="note" style={{ marginTop: 14 }}>
        This is a planning and simulation digital twin. Playback steps through solved
        15-minute records; it is not live plant telemetry and it does not control equipment.
        The seeded dataset is illustrative — not metered load, not a vendor quotation, and
        not a statement of any statutory charge.
      </p>

      <div className="tabs" role="tablist">
        <button className="tab" role="tab" aria-selected={mode === 'find_optimum'}
                onClick={() => { setMode('find_optimum'); if (baseline) setShown(baseline); }}>
          Find optimum
        </button>
        <button className="tab" role="tab" aria-selected={mode === 'manual'}
                onClick={() => setMode('manual')}>
          Manual scenario
        </button>
      </div>

      {err && <div className="card" style={{ borderColor: 'var(--red-500)' }}>
        <strong style={{ fontSize: 12 }}>Problem:</strong>{' '}
        <span style={{ fontSize: 12 }}>{err}</span>
      </div>}

      <div className="grid">
        {/* ---------------- left: capacities ---------------- */}
        <div>
          <div className="card">
            <h3>Capacities</h3>
            {mode === 'find_optimum' ? (
              <p className="hint">
                Solved jointly with dispatch to minimise total annualised cost. Switch to
                Manual scenario to move them and pay the difference.
              </p>
            ) : (
              <p className="hint">
                Fixed on the backend and dispatch re-optimised against them. Capital and
                fixed O&amp;M for what you choose stay in the total.
              </p>
            )}
            {caps && CAP_KEYS.map((k) => {
              const [lo, hi, step] = capBounds[k];
              return (
                <div className="slider" key={k}>
                  <label htmlFor={k}>
                    <span>{capLabel[k]}</span>
                    <span className="val">{num(caps[k], 2)} {capUnit[k]}</span>
                  </label>
                  <input id={k} type="range" min={lo} max={hi} step={step} value={caps[k]}
                         disabled={mode !== 'manual'}
                         onChange={(e) => onCap(k, Number(e.target.value))} />
                  <div className="bounds"><span>{lo}</span><span>{hi} {capUnit[k]}</span></div>
                </div>
              );
            })}
            <div className="row">
              <span>Storage duration</span>
              <span className="n">{duration == null ? 'no storage' : `${num(duration, 2)} h`}</span>
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
              <button className="act ghost" onClick={resetToOptimum} disabled={!baseline}>
                Reset to optimum
              </button>
              {job && !['succeeded', 'failed', 'cancelled'].includes(job.status) && (
                <button className="act ghost" onClick={() => api.cancel(job.job_id)}>Cancel</button>
              )}
            </div>
          </div>

          <div className="card">
            <h3>Solve</h3>
            <div className="row"><span>Job</span>
              <span className="n">{job ? `${job.status}` : 'idle'}</span></div>
            <div className="row"><span>Solver</span>
              <span className="n">{shown?.status ?? '—'}</span></div>
            <div className="row"><span>Gap</span>
              <span className="n">{shown?.gap == null ? '—' : shown.gap.toExponential(1)}</span></div>
            <div className="row"><span>Wall time</span>
              <span className="n">{shown ? `${num(shown.wall_seconds, 1)} s` : '—'}</span></div>
            <div className="row"><span>Variables</span>
              <span className="n">{shown ? shown.variables.toLocaleString('en-IN') : '—'}</span></div>
            {stale && <p className="hint" style={{ color: 'var(--amber-600)' }}>
              Showing the previous result while a new scenario solves. No progress bar is
              shown because the solver does not report one.
            </p>}
          </div>
        </div>

        {/* ---------------- centre: the stage ---------------- */}
        <div>
          <IsoTwin flows={flowsWithSoc} sizes={sizes} stamp={stamp} mode={mode} />
          <div className="transport">
            <button className="act" onClick={() => setPlaying((p) => !p)}>
              {playing ? 'Pause' : 'Play'}
            </button>
            <button className="act ghost" onClick={() => setCursor((c) => Math.max(0, c - 1))}>
              ◀ block
            </button>
            <button className="act ghost"
                    onClick={() => setCursor((c) => Math.min(BLOCKS_PER_DAY - 1, c + 1))}>
              block ▶
            </button>
            <input type="range" min={0} max={BLOCKS_PER_DAY - 1} value={cursor}
                   aria-label="block within the day"
                   onChange={(e) => setCursor(Number(e.target.value))} />
            <span className="t">{stamp}</span>
            <label className="eyebrow" htmlFor="day">day</label>
            <input id="day" type="number" min={0} max={Math.max(0, days.length - 1)} value={day}
                   style={{ width: 70 }} onChange={(e) => setDay(Number(e.target.value))} />
            <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}
                    aria-label="playback speed">
              {SPEEDS.map((s) => <option key={s} value={s}>{s}×</option>)}
            </select>
            <button className="act ghost" onClick={() => setShowTable((s) => !s)}>
              {showTable ? 'Hide table' : 'Table view'}
            </button>
          </div>

          <div className="kpis" style={{ marginTop: 12 }}>
            <Kpi k="load" v={mw(flows.loadMw)} s="this block" />
            <Kpi k="renewable" v={mw(flows.roofMw + flows.oaSolarMw + flows.oaWindMw)}
                 s={`${pct(flows.loadMw > 0
                   ? Math.min(1, (flows.roofMw + flows.oaSolarMw + flows.oaWindMw) / flows.loadMw)
                   : 0, 0)} of load`} />
            <Kpi k="battery" v={flows.dischargeMw > 0.001 ? mw(flows.dischargeMw)
              : flows.chargeMw > 0.001 ? `−${mw(flows.chargeMw)}` : '0.00 MW'}
                 s={socFrac == null ? 'none installed' : `SOC ${pct(socFrac, 0)}`} />
            <Kpi k="grid import" v={mw(flows.importUtilityMw + flows.importMarketMw)}
                 s={`market ${mw(flows.importMarketMw)}`} />
            <Kpi k="interval energy cost" v={block
              ? inr(num0(block['import_utility_mw']) * 0.25 * num0(block['tariff_inr_per_mwh']), 0)
              : '—'} s="utility energy only" />
            <Kpi k="curtailed" v={mw(flows.curtailedMw)} s="renewable spilled" />
          </div>
          <p className="hint" style={{ marginTop: 8 }}>
            Interval cost is the utility energy charge for this block. Demand charges and
            capital annuities are annual quantities and are never spread across intervals
            as if they were marginal prices.
          </p>

          {showTable && block && (
            <div className="card" style={{ marginTop: 12 }}>
              <h3>Selected block, every solved quantity</h3>
              <table className="data">
                <thead><tr><th>quantity</th><th>value</th></tr></thead>
                <tbody>
                  {Object.entries(block).map(([k, v]) => (
                    <tr key={k}><td>{k}</td>
                      <td>{typeof v === 'number' ? num(v, 4) : String(v)}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* ---------------- right: economics ---------------- */}
        <div>
          <div className="card">
            <h3>Certification</h3>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
              <span className={`badge ${badge}`}>{vstat ?? 'not run'}</span>
              {shown?.coverage && <span className="badge info">{shown.coverage}</span>}
            </div>
            <div className="row"><span>Model</span>
              <span className="n">{shown?.model_version ?? '—'}</span></div>
            <div className="row"><span>Input fingerprint</span>
              <span className="n">{shown?.input_fingerprint?.slice(0, 12) ?? '—'}</span></div>
            <div className="row"><span>Baseline</span>
              <span className="n">{baseline?.run_id?.slice(0, 18) ?? 'none'}</span></div>
            {!comparable && shown && baseline && (
              <p className="hint" style={{ color: 'var(--amber-600)' }}>
                Inputs differ from the baseline, so the comparison is stale. Rerun Find
                optimum before reading any premium.
              </p>
            )}
          </div>

          <div className="card">
            <h3>Annual cost</h3>
            <div className="row"><span>Total, this scenario</span>
              <span className="n">{crore(shown?.objective_inr_year ?? null)}</span></div>
            <div className="row"><span>Certified optimum</span>
              <span className="n">{crore(optimum)}</span></div>
            <div className="row"><span>Premium</span>
              <span className="n" style={{ color: premium && premium > 0 ? 'var(--red-600)' : undefined }}>
                {premium == null ? '—' : crore(premium)}
              </span></div>
            <div className="row"><span>Premium, per cent</span>
              <span className="n">{premiumPct == null ? 'unavailable' : pct(premiumPct, 2)}</span></div>
            {premium != null && premium < -1 && (
              <p className="hint" style={{ color: 'var(--red-600)' }}>
                A manual scenario below a certified optimum means the two runs are not
                comparable. This is reported, not clamped to zero.
              </p>
            )}
          </div>

          <div className="card">
            <h3>Where the money goes</h3>
            {shown && <CostBars ledger={shown.ledger} />}
          </div>

          <div className="card">
            <h3>Capacities in this run</h3>
            {shown && CAP_KEYS.map((k) => (
              <div className="row" key={k}>
                <span>{capLabel[k]}</span>
                <span className="n">{num(num0(shown.capacities[k]), 2)} {capUnit[k]}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* ---------------- charts ---------------- */}
      <div className="card" style={{ marginTop: 16 }}>
        <h3>The selected day, every solved block</h3>
        <DayChart rows={rows} cursor={cursor} onPick={setCursor} />
        <SocChart rows={rows} cursor={cursor} capMwh={sizes.bessMwh} />
      </div>
      <div className="card">
        <h3>The whole year — {days.length ? istDate(`${days[0].day}T00:00:00Z`) : ''} onwards</h3>
        <YearChart days={days} selected={day} onPickDay={setDay} />
      </div>
      <div className="card">
        <h3>Monthly utility bill</h3>
        <MonthlyChart rows={shown?.monthly ?? []} />
      </div>
    </div>
  );
}

function Kpi({ k, v, s }: { k: string; v: string; s?: string }) {
  return (
    <div className="kpi">
      <div className="k">{k}</div>
      <div className="v">{v}</div>
      {s && <div className="s">{s}</div>}
    </div>
  );
}
