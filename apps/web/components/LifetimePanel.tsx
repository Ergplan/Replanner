'use client';
/**
 * The design over its life: what it costs year by year, when it pays back and at what
 * return, against staying on grid supply alone.
 *
 * Every number comes from the lifetime evaluation (energy_core/lifetime.py): certified
 * dispatch solves for the sampled years, straight-line interpolation between them, and
 * capital as cash flows. The chart marks which years were solved and which interpolated.
 */
import { useCallback, useEffect, useState } from 'react';
import { ACTIVE_JOB, api, Job, Lifetime, RunSummary } from '@/lib/api';
import { crore, pct } from '@/lib/format';

const GOLD = 'var(--gold-500)', BLUE = '#5B8DC4', MUTED = 'var(--text-muted)';

export default function LifetimePanel({ pid, run, runs, studyYears, onRuns }: {
  pid: string; run: RunSummary; runs: RunSummary[]; studyYears: number;
  onRuns: () => void;
}) {
  const [life, setLife] = useState<Lifetime | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  // An evaluation already made for this run is shown rather than redone.
  useEffect(() => {
    setLife(null); setErr(null);
    const done = runs.find((r) => r.mode === 'lifetime' && r.baseline_run_id === run.run_id);
    if (done) api.lifetime(done.run_id).then(setLife).catch(() => {});
  }, [run.run_id, runs]);

  const active = !!job && ACTIVE_JOB.includes(job.status);
  useEffect(() => {
    if (!active || !job) return;
    const id = setInterval(async () => {
      setNow(Date.now());
      try {
        const j = await api.job(job.job_id);
        setJob(j);
        if (j.status === 'succeeded' && j.run_id) { setLife(await api.lifetime(j.run_id)); onRuns(); }
        if (j.status === 'failed') setErr(j.message || 'the evaluation failed');
      } catch { /* retried on the next tick */ }
    }, 1500);
    return () => clearInterval(id);
  }, [active, job, onRuns]);

  const start = useCallback(async () => {
    try {
      setErr(null);
      const { job_id, status } = await api.submit({ project_id: pid, mode: 'lifetime',
                                                    baseline_run_id: run.run_id });
      setJob({ job_id, status, mode: 'lifetime', run_id: null, message: '',
               input_fingerprint: null, baseline_run_id: run.run_id, created_at: Date.now() / 1000 });
    } catch (e) { setErr(String(e)); }
  }, [pid, run.run_id]);

  const certified = run.validation_status?.startsWith('certified');
  const elapsed = job ? Math.max(0, now / 1000 - (job.started_at ?? job.created_at ?? now / 1000)) : 0;

  return (
    <div className="card">
      <h3>Over the life of the design</h3>
      {!life && !active && (
        <>
          <p className="hint">
            Runs this design through its {studyYears}-year study period: panels degrade,
            battery cells fade and are replaced, prices escalate. Sampled years are solved and
            certified, the rest interpolated. It takes several minutes, and manual scenarios
            wait behind it.
          </p>
          <button className="act" style={{ marginTop: 8 }} onClick={start} disabled={!certified}>
            Evaluate over its life</button>
          {!certified && <p className="hint">Only a certified run can be evaluated.</p>}
        </>
      )}
      {active && job && (
        <>
          <p className="hint">
            {job.status === 'queued' ? 'Queued behind other solves…'
              : `${job.message || 'Starting'} · ${Math.floor(elapsed / 60)}:${String(Math.floor(elapsed % 60)).padStart(2, '0')}`}
          </p>
          <button className="act ghost" style={{ marginTop: 8 }}
                  onClick={async () => setJob(await api.cancel(job.job_id))}>Cancel</button>
        </>
      )}
      {err && <p className="hint" style={{ color: 'var(--red-600)' }}>{err}</p>}
      {life && <Result life={life} />}
    </div>
  );
}

function Result({ life }: { life: Lifetime }) {
  const saving = life.npv_grid_only != null && life.npv_design != null
    ? life.npv_grid_only - life.npv_design : null;
  const perKwh = (v: number | null) => (v == null ? '—' : `₹${v.toFixed(2)}/kWh`);
  return (
    <>
      <div className="row"><span>Levelised cost, this design</span>
        <span className="n">{perKwh(life.levelised_design)}</span></div>
      <div className="row"><span>Levelised cost, grid only</span>
        <span className="n">{perKwh(life.levelised_grid_only)}</span></div>
      <div className="row"><span>Lifetime cost (NPV)</span>
        <span className="n">{crore(life.npv_design)}</span></div>
      <div className="row"><span>Saving over the life (NPV)</span>
        <span className="n" style={{ color: saving != null && saving > 0 ? 'var(--green-700)' : undefined }}>
          {crore(saving)}</span></div>
      <div className="row"><span>Pays back</span>
        <span className="n">{life.payback_year ? `in year ${life.payback_year}`
          : saving == null ? '—' : `not within ${life.study_years} years`}</span></div>
      <div className="row"><span>Return (IRR)</span>
        <span className="n">{life.irr == null ? '—' : pct(life.irr, 1)}</span></div>
      <div className="row"><span>Capital, year 0</span><span className="n">{crore(life.capex_year0)}</span></div>
      {life.replacements.map((r) => (
        <div className="row" key={`${r.year}-${r.item}`}>
          <span>Replace {r.item.replace(/_/g, ' ')}, year {r.year}</span>
          <span className="n">{crore(r.inr)}</span></div>
      ))}
      {life.salvage > 0 && (
        <div className="row"><span>Salvage, year {life.study_years}</span>
          <span className="n">−{crore(life.salvage)}</span></div>
      )}
      <YearBars life={life} />
      {life.problems.length > 0 && (
        <ul style={{ margin: '8px 0 0', paddingLeft: 18, fontSize: 11.5, color: 'var(--red-600)' }}>
          {life.problems.map((p) => <li key={p}>{p}</li>)}
        </ul>
      )}
      <p className="hint">
        {life.discount_rate * 100}% {life.basis} discount rate, prices escalating
        {' '}{(life.escalation * 100).toFixed(1)}% a year. Capacities are fixed at this design;
        this evaluates it over the years, it does not re-optimise them.
      </p>
    </>
  );
}

function YearBars({ life }: { life: Lifetime }) {
  const ys = life.years.filter((y) => y.opex_design != null);
  if (!ys.length) return null;
  const max = Math.max(1, ...ys.flatMap((y) => [y.opex_design ?? 0, y.opex_grid_only ?? 0]));
  const w = 280, h = 90, bw = w / ys.length;
  return (
    <div style={{ marginTop: 12 }}>
      <svg viewBox={`0 0 ${w} ${h + 16}`} style={{ width: '100%', height: 'auto' }}
           role="img" aria-label="Operating cost by year, design against grid only">
        {ys.map((y, i) => {
          const x = i * bw;
          const hd = ((y.opex_design ?? 0) / max) * h;
          const hg = ((y.opex_grid_only ?? 0) / max) * h;
          return (
            <g key={y.year} opacity={y.sampled ? 1 : 0.45}>
              {y.opex_grid_only != null && (
                <rect x={x + bw * 0.12} y={h - hg} width={bw * 0.36} height={hg} fill={BLUE} />
              )}
              <rect x={x + bw * 0.52} y={h - hd} width={bw * 0.36} height={hd} fill={GOLD} />
              {(y.year === 1 || y.year % 5 === 0) && (
                <text x={x + bw / 2} y={h + 12} textAnchor="middle" fontSize="8" fill={MUTED}
                      fontFamily="var(--font-mono)">{y.year}</text>
              )}
            </g>
          );
        })}
      </svg>
      <p className="hint" style={{ marginTop: 2 }}>
        Operating cost by year: <span style={{ color: BLUE }}>■</span> grid only,
        {' '}<span style={{ color: GOLD }}>■</span> this design. Solid years were solved and
        certified; faint ones are interpolated.
      </p>
    </div>
  );
}
