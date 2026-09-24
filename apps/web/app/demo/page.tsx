'use client';
/**
 * Client demo: what a 5 GW solar-cell gigafactory pays for power today, and what it could.
 *
 * Built for presenting. The run is a five-second animation over fixed, illustrative
 * numbers (lib/demo.ts), and the page says so: a site's own answer comes from Setup and
 * the certified optimiser, not from here.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import GigaFactory from '@/components/demo/GigaFactory';
import s from './demo.module.css';
import {
  ANNUAL_GWH, BESS_MW, BESS_MWH, BUILDINGS, DEMO_BASELINE, demoResult, OA_SOLAR_MW, OA_WIND_MW,
  PEAK_MW, Selection,
} from '@/lib/demo';

const RUN_MS = 5000;
const PHASES: [number, string][] = [
  [0, 'Placing rooftop arrays on the fab halls…'],
  [0.16, 'Commissioning the open-access solar park…'],
  [0.36, 'Raising the wind turbines…'],
  [0.56, 'Installing battery storage…'],
  [0.8, 'Optimising 35,040 quarter-hours of dispatch…'],
  [1, 'Done.'],
];

const none: Selection = { roofs: {}, oaSolar: false, wind: false, bess: false };
const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);

export default function Demo() {
  const [sel, setSel] = useState<Selection>(none);
  const [progress, setProgress] = useState<number | null>(null);   // null: not run yet
  const raf = useRef<number | null>(null);
  const running = progress !== null && progress < 1;

  const result = useMemo(() => demoResult(sel), [sel]);
  const roofCount = BUILDINGS.filter((b) => sel.roofs[b.id]).length;
  const anything = roofCount > 0 || sel.oaSolar || sel.wind || sel.bess;

  // Any change to the selection makes a finished picture stale, so it goes back to plan.
  const change = (next: Selection) => {
    if (running) return;
    setSel(next);
    setProgress(null);
  };
  const toggleRoof = (id: string) =>
    change({ ...sel, roofs: { ...sel.roofs, [id]: !sel.roofs[id] } });

  const run = useCallback(() => {
    if (raf.current) cancelAnimationFrame(raf.current);
    const t0 = performance.now();
    const tick = (now: number) => {
      const p = Math.min(1, (now - t0) / RUN_MS);
      setProgress(p);
      if (p < 1) raf.current = requestAnimationFrame(tick);
    };
    setProgress(0);
    raf.current = requestAnimationFrame(tick);
  }, []);
  useEffect(() => () => { if (raf.current) cancelAnimationFrame(raf.current); }, []);

  const shownP = progress === null ? 0 : ease(Math.min(1, Math.max(0, (progress - 0.04) / 0.92)));
  const cost = DEMO_BASELINE - (DEMO_BASELINE - result.costPerKwh) * shownP;
  const saving = (DEMO_BASELINE - cost) / DEMO_BASELINE;
  const phase = progress === null ? '' : [...PHASES].reverse().find(([at]) => progress >= at)?.[1] ?? '';

  return (
    <div className={s.page}>
      <div className={s.shell}>
        <header className={s.top}>
          <div className={s.brand}><b>joule</b>Wise</div>
          <div className={s.eyebrow}>ergOS · energy transition demo</div>
          <nav><a href="/setup">Plan a real site</a><a href="/">Planning twin</a></nav>
        </header>

        <div className={s.title}>
          <div>
            <h1>5 GW TOPCon cell gigafactory</h1>
            <p>
              {ANNUAL_GWH} GWh a year at a {PEAK_MW} MW peak, running round the clock through
              texturing, diffusion, coating, printing and firing. Choose which roofs carry solar,
              add open-access solar, wind and storage, and run.
            </p>
          </div>
        </div>

        <div className={s.layout}>
          <div className={s.stage}>
            <GigaFactory sel={sel} progress={progress} locked={running} onToggleRoof={toggleRoof} />
          </div>

          <aside className={s.panel}>
            <section className={s.card}>
              <h3><span className={s.n}>01</span>Rooftop solar</h3>
              <p className={s.hint}>Click a building on the campus to put solar on its roof.</p>
              <div className={s.count}>
                {roofCount} of {BUILDINGS.length} roofs · {result.roofMwp.toFixed(1)} MWp
              </div>
              <div className={s.row}>
                <button className={s.ghost} disabled={running}
                        onClick={() => change({ ...sel, roofs: Object.fromEntries(BUILDINGS.map((b) => [b.id, true])) })}>
                  All roofs</button>
                <button className={s.ghost} disabled={running || roofCount === 0}
                        onClick={() => change({ ...sel, roofs: {} })}>Clear</button>
              </div>
            </section>

            <section className={s.card}>
              <h3><span className={s.n}>02</span>Offsite supply &amp; storage</h3>
              <Toggle on={sel.oaSolar} disabled={running} label="Open-access solar"
                      spec={`${OA_SOLAR_MW} MW · wheeled from a remote park`}
                      onClick={() => change({ ...sel, oaSolar: !sel.oaSolar })} />
              <Toggle on={sel.wind} disabled={running} label="Open-access wind"
                      spec={`${OA_WIND_MW} MW · evening and night output`}
                      onClick={() => change({ ...sel, wind: !sel.wind })} />
              <Toggle on={sel.bess} disabled={running} label="Battery storage"
                      spec={`${BESS_MW} MW / ${BESS_MWH} MWh · shifts and shaves peaks`}
                      onClick={() => change({ ...sel, bess: !sel.bess })} />
            </section>

            <section className={s.card}>
              <button className={s.run} disabled={!anything || running} onClick={run}>
                {running ? 'Running…' : progress === 1 ? 'Run again' : 'Run'}
              </button>
              <div className={s.progress}><div style={{ width: `${(progress ?? 0) * 100}%` }} /></div>
              <div className={s.phase}>{anything ? phase : 'Select at least one option to run.'}</div>
              <button className={s.ghost} style={{ marginTop: 10 }} disabled={running}
                      onClick={() => change(none)}>Reset</button>
            </section>
          </aside>
        </div>

        <div className={s.results} aria-live="polite">
          <div className={`${s.metric} ${s.cost}`}>
            <div className={s.k}>Cost of power</div>
            <div className={s.v}>₹{cost.toFixed(2)}<span style={{ fontSize: 15, color: '#8391A0' }}> /kWh</span></div>
            <div className={s.from}>DISCOM today <s>₹{DEMO_BASELINE.toFixed(2)}</s></div>
            <div className={s.bar}><div style={{ width: `${(cost / DEMO_BASELINE) * 100}%` }} /></div>
          </div>
          <Metric k="Saving" v={`${(saving * 100).toFixed(0)}%`} s="on every unit" />
          <Metric k="Annual saving" v={`₹${Math.round(result.annualSavingCr * shownP).toLocaleString('en-IN')} Cr`}
                  s={`on ${ANNUAL_GWH} GWh a year`} />
          <Metric k="Renewable share" v={`${Math.round(result.reShare * shownP * 100)}%`} s="of annual energy" />
          <Metric k="CO₂ avoided" v={`${Math.round(result.co2Tonnes * shownP / 1000).toLocaleString('en-IN')} kt`}
                  s="a year, at the CEA grid factor" />
        </div>

        <p className={s.foot}>
          Illustrative demo for discussion. The figures are indicative for a site of this kind
          and are not a solved result. A site&apos;s own numbers come from its metered load, tariff
          order and quotes, through the planning engine&apos;s certified optimisation.
        </p>
      </div>
    </div>
  );
}

function Toggle({ on, disabled, label, spec, onClick }: {
  on: boolean; disabled: boolean; label: string; spec: string; onClick: () => void;
}) {
  return (
    <button className={s.toggle} aria-pressed={on} disabled={disabled} onClick={onClick}>
      <span className={s.sw} />
      <span><span className={s.label}>{label}</span><span className={s.spec}>{spec}</span></span>
    </button>
  );
}

function Metric({ k, v, s: sub }: { k: string; v: string; s: string }) {
  return (
    <div className={s.metric}>
      <div className={s.k}>{k}</div>
      <div className={s.v}>{v}</div>
      <div className={s.s}>{sub}</div>
    </div>
  );
}
