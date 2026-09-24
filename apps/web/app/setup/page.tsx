'use client';
/**
 * Project setup: where a user describes their site before anything is solved.
 *
 * The page edits the whole ProjectInputs document and sends it back whole; the backend
 * validates it with the same schema the solver reads, so this page never has to decide
 * what is valid. Values are shown in the units people quote — crore per MW, lakh per MW
 * a year, per cent — and converted to the stored units at the edge, nowhere else.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import s from './setup.module.css';
import {
  ApiError, FieldError, formatRanges, Inputs, parseRanges, ProjectDoc, ProjectListItem,
  projectFromUrl, projects, SAMPLE_ID, UploadSummary,
} from '@/lib/projects';

const CRORE = 1e7, LAKH = 1e5, PCT = 0.01, PER_KWH = 1;

const TECH_LABEL: Record<string, string> = {
  solar_onsite: 'Rooftop solar, behind the meter',
  solar_remote: 'Open-access solar',
  wind_remote: 'Open-access wind',
  bess: 'Battery storage',
};

const SECTIONS = [
  ['site', 'Site'], ['existing', 'Existing plant'], ['load', 'Load profile'],
  ['tariff', 'Utility tariff'], ['oa', 'Open access & market'],
  ['tech', 'Technologies & costs'], ['finance', 'Finance'],
] as const;

type Ctx = {
  doc: Inputs; set: (path: string, v: unknown) => void; ro: boolean; errs: Record<string, string>;
};

function getIn(o: Inputs, path: string): any {                 // eslint-disable-line @typescript-eslint/no-explicit-any
  return path.split('.').reduce((a, k) => (a == null ? a : a[k]), o);
}
function setIn(o: Inputs, path: string, v: unknown): Inputs {
  const copy = structuredClone(o);
  const keys = path.split('.');
  let cur = copy;
  for (const k of keys.slice(0, -1)) cur = cur[k];
  cur[keys[keys.length - 1]] = v;
  return copy;
}
const tidy = (x: number) => Number(x.toPrecision(10));

function Num({ c, path, label, unit, scale = 1, help, nullable, min, step }: {
  c: Ctx; path: string; label: string; unit?: string; scale?: number; help?: string;
  nullable?: boolean; min?: number; step?: number;
}) {
  const raw = getIn(c.doc, path);
  const shown = raw == null ? '' : String(tidy(raw / scale));
  const [text, setText] = useState(shown);
  useEffect(() => setText(shown), [shown]);
  return (
    <div className={s.field}>
      <label htmlFor={path}>{label}{unit && <span className={s.unit}>{unit}</span>}</label>
      <input id={path} type="number" inputMode="decimal" value={text} disabled={c.ro}
             min={min} step={step ?? 'any'}
             onChange={(e) => {
               setText(e.target.value);
               if (e.target.value === '') { if (nullable) c.set(path, null); return; }
               const n = Number(e.target.value);
               if (Number.isFinite(n)) c.set(path, n * scale);
             }} />
      {c.errs[path] && <span className={s.err}>{inShownUnits(c.errs[path], scale)}</span>}
      {help && <span className={s.help}>{help}</span>}
    </div>
  );
}

/** The schema reports limits in stored units (a fraction, rupees); the field shows per
 *  cent or crore, so the limit in the message is converted to match what was typed. */
function inShownUnits(message: string, scale: number): string {
  return scale === 1 ? message
    : message.replace(/-?\d+(\.\d+)?(e-?\d+)?/g, (n) => String(tidy(Number(n) / scale)));
}

function Check({ c, path, label }: { c: Ctx; path: string; label: string }) {
  return (
    <label className={s.check}>
      <input type="checkbox" checked={!!getIn(c.doc, path)} disabled={c.ro}
             onChange={(e) => c.set(path, e.target.checked)} />
      {label}
    </label>
  );
}

function Ranges({ c, path, label, lo, hi, help }: {
  c: Ctx; path: string; label: string; lo: number; hi: number; help?: string;
}) {
  const shown = formatRanges(getIn(c.doc, path) ?? []);
  const [text, setText] = useState(shown);
  const [bad, setBad] = useState(false);
  useEffect(() => { setText(shown); setBad(false); }, [shown]);
  return (
    <div className={s.field}>
      <label htmlFor={path}>{label}</label>
      <input id={path} value={text} disabled={c.ro} placeholder={`e.g. ${lo}-${hi}`}
             onChange={(e) => {
               setText(e.target.value);
               const xs = parseRanges(e.target.value, lo, hi);
               setBad(xs === null);
               if (xs !== null) c.set(path, xs);
             }} />
      {bad && <span className={s.err}>use numbers {lo}–{hi}, ranges like 18-21, commas between</span>}
      {c.errs[path] && <span className={s.err}>{c.errs[path]}</span>}
      {help && <span className={s.help}>{help}</span>}
    </div>
  );
}

export default function Setup() {
  const [pid, setPid] = useState<string>(SAMPLE_ID);
  const [list, setList] = useState<ProjectListItem[]>([]);
  const [proj, setProj] = useState<ProjectDoc | null>(null);
  const [draft, setDraft] = useState<Inputs | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<string | null>(null);
  const [newName, setNewName] = useState('');

  const load = useCallback(async (id: string) => {
    const [doc, ls] = await Promise.all([projects.get(id), projects.list()]);
    setProj(doc); setDraft(doc.inputs); setList(ls); setDirty(false); setErrs({});
  }, []);

  useEffect(() => {
    const id = projectFromUrl();
    setPid(id);
    load(id).catch((e) => setMsg(`Cannot reach the solver service: ${e}`));
  }, [load]);

  const go = (id: string) => {
    window.history.replaceState(null, '', `/setup?project=${id}`);
    setPid(id); setMsg(null);
    load(id).catch((e) => setMsg(String(e)));
  };

  const set = useCallback((path: string, v: unknown) => {
    setDraft((d) => (d ? setIn(d, path, v) : d)); setDirty(true);
  }, []);
  const ro = !!proj?.sample;
  const c: Ctx | null = draft ? { doc: draft, set, ro, errs } : null;

  const save = async (): Promise<boolean> => {
    if (!draft) return false;
    setBusy(true); setMsg(null);
    try {
      const doc = await projects.save(pid, draft);
      setProj(doc); setDraft(doc.inputs); setDirty(false); setErrs({});
      setMsg(doc.problems.length ? 'Saved, but the solver cannot run these inputs yet — see below.'
        : 'Saved.');
      return doc.problems.length === 0;
    } catch (e) {
      if (e instanceof ApiError && e.status === 422) {
        const invalid = (e.detail as { invalid: FieldError[] }).invalid;
        setErrs(Object.fromEntries(invalid.map((f) => [f.field, f.message])));
        setMsg(`Not saved: ${invalid.length} value${invalid.length > 1 ? 's are' : ' is'} out of range. `
          + 'They are marked below.');
      } else setMsg(`Not saved: ${e}`);
      return false;
    } finally { setBusy(false); }
  };

  const saveAndSolve = async () => {
    if (await save()) window.location.href = `/?project=${pid}&solve=1`;
  };

  const createFrom = async () => {
    const name = newName.trim();
    if (!name) { setMsg('Give the project a name first.'); return; }
    try {
      const { project_id } = await projects.create(name);
      setNewName('');
      go(project_id);
    } catch (e) { setMsg(String(e)); }
  };

  const year = draft?.project?.operating_years?.[0] ?? proj?.year;
  const setYear = (y: number) => {
    set('project.operating_years', [y]); set('project.base_year', y); set('finance.base_year', y);
  };

  if (!c || !draft || !proj) {
    return <div className="shell"><Top pid={pid} />{msg && <p className="note">{msg}</p>}</div>;
  }

  return (
    <div className="shell">
      <Top pid={pid} />

      <div className="card" style={{ marginTop: 14 }}>
        <div className={s.inline}>
          <label className="eyebrow" htmlFor="proj">project</label>
          <select id="proj" value={pid} onChange={(e) => go(e.target.value)}>
            {list.map((p) => <option key={p.project_id} value={p.project_id}>
              {p.name}{p.sample ? ' (sample)' : ''}</option>)}
          </select>
          <span style={{ flex: 1 }} />
          <input placeholder="New project name" value={newName} aria-label="new project name"
                 onChange={(e) => setNewName(e.target.value)}
                 onKeyDown={(e) => { if (e.key === 'Enter') createFrom(); }} />
          <button className="act" onClick={createFrom}>New project from sample</button>
        </div>
        {ro && <p className="hint">
          This is the sample project and it is read-only. Name a new project above: it starts
          as a copy of the sample, and everything below becomes editable.
        </p>}
      </div>

      {proj.problems.length > 0 && (
        <div className={`card ${s.problems}`}>
          <h3>The solver cannot run these inputs yet</h3>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {proj.problems.map((p) => <li key={p}>{p}</li>)}
          </ul>
        </div>
      )}

      <div className={s.layout} style={{ marginTop: 14 }}>
        <nav className={s.nav}>
          {SECTIONS.map(([id, label], i) => (
            <a key={id} href={`#${id}`}><span className={s.step}>{i + 1}</span>{label}</a>
          ))}
        </nav>

        <div>
          {/* 1 ---------------------------------------------------------------- */}
          <section id="site" className={`card ${s.section}`}>
            <h2>1 · Site</h2>
            <p>The connection and the space you have. Utility supply, open access and exchange
              purchases all share the import limit, because they come in over the same wires.</p>
            <div className={s.fields}>
              <div className={s.field}>
                <label htmlFor="name">Project name</label>
                <input id="name" value={draft.project.name} disabled={ro}
                       onChange={(e) => set('project.name', e.target.value)} />
              </div>
              <div className={s.field}>
                <label htmlFor="year">Operating year</label>
                <input id="year" type="number" value={year} disabled={ro} min={2020} max={2060}
                       onChange={(e) => { const y = Number(e.target.value); if (y >= 2000) setYear(y); }} />
                <span className={s.help}>One calendar year, every 15 minutes, in IST.</span>
              </div>
              <Num c={c} path="project.site.import_limit_mw" label="Import limit" unit="MW"
                   help="Sanctioned import at the point of connection." />
              <Num c={c} path="project.site.export_limit_mw" label="Export limit" unit="MW"
                   help="0 if the site may not export." />
              <Num c={c} path="project.site.roof_area_mw_cap" label="Roof space for solar" unit="MWp"
                   help="Includes any panels already there." />
              <Num c={c} path="project.site.land_mw_cap" label="Land for ground-mount solar" unit="MWp" />
            </div>
          </section>

          {/* 2 ---------------------------------------------------------------- */}
          <section id="existing" className={`card ${s.section}`}>
            <h2>2 · Existing plant</h2>
            <p>What is already built. Its capital is sunk, so only its O&amp;M is charged, and
              it produces energy from day one.</p>
            <ExistingAssets c={c} />
          </section>

          {/* 3 ---------------------------------------------------------------- */}
          <section id="load" className={`card ${s.section}`}>
            <h2>3 · Load profile</h2>
            <p>A year of the site&apos;s demand for {year}. Upload a meter export, or keep the sample
              profile to explore. Solar, wind and exchange prices use sample profiles for a
              western-India site; uploading your own is not supported yet.</p>
            <LoadPanel pid={pid} proj={proj} ro={ro} dirty={dirty}
                       onChange={(doc) => { setProj(doc); }} onMsg={setMsg} />
          </section>

          {/* 4 ---------------------------------------------------------------- */}
          <section id="tariff" className={`card ${s.section}`}>
            <h2>4 · Utility tariff</h2>
            <p>Your DISCOM&apos;s retail tariff. Time-of-day rows are applied in order, so a later
              row overrides an earlier one for the hours it names. Keep one row covering all
              hours as the default.</p>
            <div className={s.fields}>
              <Num c={c} path="tariff.contract_demand_mw" label="Contract demand" unit="MW" />
              <Num c={c} path="tariff.demand_charge_inr_per_unit_month" label="Demand charge"
                   unit={`₹/${draft.tariff.demand_basis}/month`} />
              <div className={s.field}>
                <label htmlFor="basis">Demand billed in</label>
                <select id="basis" value={draft.tariff.demand_basis} disabled={ro}
                        onChange={(e) => set('tariff.demand_basis', e.target.value)}>
                  <option value="kVA">kVA</option><option value="kW">kW</option>
                </select>
              </div>
              <Num c={c} path="tariff.power_factor" label="Power factor" help="Used to bill kVA." />
              <Num c={c} path="tariff.ratchet_frac_of_contract" label="Minimum billing demand"
                   unit="% of contract" scale={PCT} />
              <Num c={c} path="tariff.duty_frac" label="Electricity duty" unit="%" scale={PCT} />
            </div>
            <div className={s.sub}>
              <h4>Energy charges by time of day</h4>
              <TariffRules c={c} />
            </div>
          </section>

          {/* 5 ---------------------------------------------------------------- */}
          <section id="oa" className={`card ${s.section}`}>
            <h2>5 · Open access &amp; market</h2>
            <p>Charges on energy wheeled from a remote plant, and exchange purchase. These are
              set by commission order and change; use the order in force.</p>
            <div className={s.fields}>
              <Num c={c} path="open_access.transmission_inr_per_kwh" label="Transmission" unit="₹/kWh" scale={PER_KWH} />
              <Num c={c} path="open_access.wheeling_inr_per_kwh" label="Wheeling" unit="₹/kWh" />
              <Num c={c} path="open_access.cross_subsidy_surcharge_inr_per_kwh" label="Cross-subsidy surcharge" unit="₹/kWh" />
              <Num c={c} path="open_access.additional_surcharge_inr_per_kwh" label="Additional surcharge" unit="₹/kWh" />
              <Num c={c} path="open_access.scheduling_inr_per_kwh" label="Scheduling" unit="₹/kWh" />
            </div>
            <div className={s.sub}>
              <h4>Banking</h4>
              <Check c={c} path="open_access.banking_enabled"
                     label="Surplus wheeled energy may be banked and drawn later" />
              {draft.open_access.banking_enabled && (
                <div className={s.fields} style={{ marginTop: 10 }}>
                  <div className={s.field}>
                    <label htmlFor="settle">Settled every</label>
                    <select id="settle" value={draft.open_access.banking_settlement} disabled={ro}
                            onChange={(e) => set('open_access.banking_settlement', e.target.value)}>
                      <option value="month">month</option><option value="year">year</option>
                    </select>
                    <span className={s.help}>What is left at settlement lapses.</span>
                  </div>
                  <Num c={c} path="open_access.banking_charge_frac" label="Banking charge, in kind"
                       unit="% of energy banked" scale={PCT} />
                  <Num c={c} path="open_access.banking_charge_inr_per_kwh" label="Banking charge, money"
                       unit="₹/kWh banked" />
                  <Num c={c} path="open_access.banking_lapse_credit_inr_per_kwh"
                       label="Paid for lapsed energy" unit="₹/kWh" />
                  <Ranges c={c} path="open_access.banking_drawal_blocked_hours" lo={0} hi={23}
                          label="No drawal in these hours" help="Local clock hours, e.g. 18-21" />
                  <Num c={c} path="open_access.banking_cap_frac_of_load" nullable scale={PCT}
                       label="Banking cap" unit="% of period's load" help="Blank for no cap." />
                </div>
              )}
            </div>
            <div className={s.sub}>
              <h4>Power exchange</h4>
              <Check c={c} path="market.enabled" label="Buy from the power exchange (day-ahead)" />
              {draft.market.enabled && (
                <div className={s.fields} style={{ marginTop: 10 }}>
                  <Num c={c} path="market.buy_limit_mw" label="Purchase limit" unit="MW" />
                  <Num c={c} path="market.transaction_inr_per_kwh" label="Trading margin & fees" unit="₹/kWh" />
                  <Check c={c} path="market.oa_charges_apply"
                         label="Open-access charges apply to exchange energy" />
                </div>
              )}
            </div>
          </section>

          {/* 6 ---------------------------------------------------------------- */}
          <section id="tech" className={`card ${s.section}`}>
            <h2>6 · Technologies &amp; costs</h2>
            <p>What the optimiser may build, up to what size, and at what price. Capital is
              annualised over each technology&apos;s life at the discount rate in Finance.</p>
            {draft.asset_options.map((o: Inputs, i: number) => (
              <div className={i ? s.sub : undefined} key={o.option_id}>
                <h4>{TECH_LABEL[o.technology] ?? o.technology}</h4>
                <Check c={c} path={`asset_options.${i}.enabled`} label="The optimiser may build this" />
                {o.enabled && (
                  <div className={s.fields} style={{ marginTop: 10 }}>
                    <Num c={c} path={`asset_options.${i}.max_mw`} label="Most it may build" unit="MW" />
                    <Num c={c} path={`asset_options.${i}.min_mw`} label="Least it must build" unit="MW" />
                    <Num c={c} path={`asset_options.${i}.step_mw`} label="Unit size" unit="MW" nullable
                         help="Blank for any size; e.g. 2.5 for whole turbines." />
                    <Num c={c} path={`asset_options.${i}.capex_inr_per_mw`} label="Capital cost"
                         unit="₹ Cr/MW" scale={CRORE} />
                    <Num c={c} path={`asset_options.${i}.fixed_om_inr_per_mw_year`} label="Fixed O&M"
                         unit="₹ lakh/MW/yr" scale={LAKH} />
                    <Num c={c} path={`asset_options.${i}.life_years`} label="Life" unit="years" step={1} />
                    {o.route === 'open_access' && (
                      <Num c={c} path={`asset_options.${i}.delivery_loss_pct`} label="Transmission loss"
                           unit="%" scale={PCT} />
                    )}
                  </div>
                )}
              </div>
            ))}
            <div className={s.sub}>
              <h4>Battery storage</h4>
              <Check c={c} path="battery.enabled" label="The optimiser may build storage" />
              {draft.battery.enabled && (
                <div className={s.fields} style={{ marginTop: 10 }}>
                  <Num c={c} path="battery.max_power_mw" label="Most power" unit="MW" />
                  <Num c={c} path="battery.max_energy_mwh" label="Most energy" unit="MWh" />
                  <Num c={c} path="battery.capex_inr_per_mw" label="Power cost (PCS, BoP)" unit="₹ Cr/MW" scale={CRORE} />
                  <Num c={c} path="battery.capex_inr_per_mwh" label="Energy cost (cells)" unit="₹ Cr/MWh" scale={CRORE} />
                  <Num c={c} path="battery.fixed_om_inr_per_mw_year" label="Fixed O&M" unit="₹ lakh/MW/yr" scale={LAKH} />
                  <Num c={c} path="battery.energy_life_years" label="Cell life" unit="years" step={1} />
                  <Num c={c} path="battery.power_life_years" label="PCS life" unit="years" step={1} />
                  <Num c={c} path="battery.technical.eta_charge" label="Charge efficiency" unit="%" scale={PCT} />
                  <Num c={c} path="battery.technical.eta_discharge" label="Discharge efficiency" unit="%" scale={PCT} />
                  <Num c={c} path="battery.technical.soc_min_frac" label="Lowest charge" unit="%" scale={PCT} />
                  <Num c={c} path="battery.technical.soc_max_frac" label="Highest charge" unit="%" scale={PCT} />
                  <Num c={c} path="battery.technical.max_c_rate" label="Most power per MWh" unit="C" />
                  <Num c={c} path="battery.technical.warranty_throughput_mwh_per_mwh"
                       label="Warranty" unit="full cycles" step={1} />
                  <Check c={c} path="battery.technical.allow_grid_charging" label="May charge from the grid" />
                </div>
              )}
            </div>
          </section>

          {/* 7 ---------------------------------------------------------------- */}
          <section id="finance" className={`card ${s.section}`}>
            <h2>7 · Finance</h2>
            <p>How capital is turned into an annual cost that can be set against a year of bills.</p>
            <div className={s.fields}>
              <Num c={c} path="finance.discount_rate" label="Discount rate" unit="%" scale={PCT} />
              <Num c={c} path="finance.study_period_years" label="Study period" unit="years" step={1} />
            </div>
          </section>

          <div className={s.footer}>
            <span className={`${s.status} ${dirty ? s.dirty : msg === 'Saved.' ? s.saved : ''}`}>
              {ro ? 'Read-only sample.' : dirty ? 'Unsaved changes.' : msg ?? 'All changes saved.'}
            </span>
            {!ro && <button className="act ghost" onClick={save} disabled={busy || !dirty}>Save</button>}
            {!ro && <button className="act" onClick={saveAndSolve} disabled={busy}>
              Save and find optimum</button>}
            {ro && <a className="act" href={`/?project=${pid}`}
                      style={{ textDecoration: 'none' }}>Open the twin</a>}
          </div>
          {msg && dirty && <p className="note">{msg}</p>}
        </div>
      </div>
    </div>
  );
}

function Top({ pid }: { pid: string }) {
  return (
    <header className="topbar">
      <div className="brand"><b>joule</b>Wise</div>
      <div className="eyebrow">ergOS · project setup</div>
      <nav style={{ marginLeft: 'auto', display: 'flex', gap: 14 }}>
        <span className="eyebrow" style={{ color: 'var(--text-strong)' }}>1 · Setup</span>
        <a className="eyebrow" href={`/?project=${pid}`}>2 · Optimise &amp; explore →</a>
      </nav>
    </header>
  );
}

function ExistingAssets({ c }: { c: Ctx }) {
  const rows: Inputs[] = c.doc.existing_assets ?? [];
  const put = (i: number, key: string, v: unknown) => c.set(`existing_assets.${i}.${key}`, v);
  const add = () => c.set('existing_assets', [...rows, {
    asset_id: `existing-${rows.length + 1}`, technology: 'solar_onsite', capacity_mw: 1,
    energy_mwh: 0, commissioned: '2020-01-01', retires: null, fixed_om_inr_per_mw_year: 0,
    route: 'onsite' }]);
  const remove = (i: number) => c.set('existing_assets', rows.filter((_, j) => j !== i));
  if (!rows.length && c.ro) return <p className="hint">None.</p>;
  return (
    <>
      <table className={s.edit}>
        <thead><tr><th>Technology</th><th>Capacity MW</th><th>Storage MWh</th>
          <th>Commissioned</th><th>Fixed O&amp;M ₹ lakh/MW/yr</th><th /></tr></thead>
        <tbody>
          {rows.map((a, i) => (
            <tr key={i}>
              <td><select value={a.technology} disabled={c.ro}
                          onChange={(e) => {
                            put(i, 'technology', e.target.value);
                            put(i, 'route', e.target.value.endsWith('_remote') ? 'open_access' : 'onsite');
                          }}>
                {Object.entries(TECH_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select></td>
              <td><input type="number" value={a.capacity_mw} disabled={c.ro}
                         onChange={(e) => put(i, 'capacity_mw', Number(e.target.value))} /></td>
              <td><input type="number" value={a.energy_mwh} disabled={c.ro || a.technology !== 'bess'}
                         onChange={(e) => put(i, 'energy_mwh', Number(e.target.value))} /></td>
              <td><input type="date" value={a.commissioned} disabled={c.ro}
                         onChange={(e) => put(i, 'commissioned', e.target.value)} /></td>
              <td><input type="number" value={tidy(a.fixed_om_inr_per_mw_year / LAKH)} disabled={c.ro}
                         onChange={(e) => put(i, 'fixed_om_inr_per_mw_year', Number(e.target.value) * LAKH)} /></td>
              <td>{!c.ro && <button className="act ghost" onClick={() => remove(i)}>Remove</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {Object.entries(c.errs).filter(([k]) => k.startsWith('existing_assets')).map(([k, v]) =>
        <p key={k} className={s.err}>{k}: {v}</p>)}
      {!c.ro && <button className="act ghost" style={{ marginTop: 8 }} onClick={add}>Add existing plant</button>}
    </>
  );
}

function TariffRules({ c }: { c: Ctx }) {
  const rules: Inputs[] = c.doc.tariff.rules;
  const add = () => c.set('tariff.rules', [...rules, {
    rule_id: `rule-${rules.length + 1}`, effective_from: '2020-01-01', effective_to: null,
    months: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], hours: [18, 19, 20, 21],
    weekdays_only: false, energy_inr_per_kwh: 0, label: 'new period' }]);
  const remove = (i: number) => c.set('tariff.rules', rules.filter((_, j) => j !== i));
  return (
    <>
      <table className={s.edit}>
        <thead><tr><th>Period</th><th>Hours</th><th>Months</th><th>Weekdays only</th>
          <th>₹/kWh</th><th /></tr></thead>
        <tbody>
          {rules.map((r, i) => (
            <tr key={i}>
              <td><input value={r.label} disabled={c.ro}
                         onChange={(e) => c.set(`tariff.rules.${i}.label`, e.target.value)} /></td>
              <td><RangeCell c={c} path={`tariff.rules.${i}.hours`} lo={0} hi={23} /></td>
              <td><RangeCell c={c} path={`tariff.rules.${i}.months`} lo={1} hi={12} /></td>
              <td style={{ textAlign: 'center' }}><input type="checkbox" checked={r.weekdays_only}
                         disabled={c.ro} style={{ width: 'auto' }}
                         onChange={(e) => c.set(`tariff.rules.${i}.weekdays_only`, e.target.checked)} /></td>
              <td><input type="number" value={r.energy_inr_per_kwh} disabled={c.ro} step="any"
                         onChange={(e) => c.set(`tariff.rules.${i}.energy_inr_per_kwh`, Number(e.target.value))} /></td>
              <td>{!c.ro && rules.length > 1 &&
                <button className="act ghost" onClick={() => remove(i)}>Remove</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {Object.entries(c.errs).filter(([k]) => k.startsWith('tariff.rules')).map(([k, v]) =>
        <p key={k} className={s.err}>{k}: {v}</p>)}
      {!c.ro && <button className="act ghost" style={{ marginTop: 8 }} onClick={add}>Add time-of-day period</button>}
    </>
  );
}

function RangeCell({ c, path, lo, hi }: { c: Ctx; path: string; lo: number; hi: number }) {
  const shown = formatRanges(getIn(c.doc, path) ?? []);
  const [text, setText] = useState(shown);
  const [bad, setBad] = useState(false);
  useEffect(() => { setText(shown); setBad(false); }, [shown]);
  return (
    <>
      <input value={text} disabled={c.ro} aria-invalid={bad}
             style={bad ? { borderColor: 'var(--red-500)' } : undefined}
             onChange={(e) => {
               setText(e.target.value);
               const xs = parseRanges(e.target.value, lo, hi);
               setBad(xs === null || xs.length === 0);
               if (xs && xs.length) c.set(path, xs);
             }} />
      {bad && <span className={s.err}>{lo}–{hi}, e.g. {lo}-{hi}</span>}
    </>
  );
}

function LoadPanel({ pid, proj, ro, dirty, onChange, onMsg }: {
  pid: string; proj: ProjectDoc; ro: boolean; dirty: boolean;
  onChange: (doc: ProjectDoc) => void; onMsg: (m: string | null) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [unit, setUnit] = useState('kW');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<UploadSummary | null>(null);
  const load = useMemo(() => proj.series.find((x) => x.column === 'load_mw'), [proj]);

  const upload = async () => {
    if (!file) return;
    setBusy(true); setResult(null);
    try {
      setResult(await projects.uploadLoad(pid, file, unit));
      onChange(await projects.get(pid));
    } catch (e) {
      if (e instanceof ApiError && e.status === 400 && typeof e.detail === 'object') {
        setResult(e.detail as UploadSummary);
      } else onMsg(`Upload failed: ${e}`);
    } finally { setBusy(false); }
  };

  return (
    <>
      <table className="data" style={{ marginBottom: 12 }}>
        <thead><tr><th>series</th><th>source</th><th>amount</th></tr></thead>
        <tbody>
          {proj.series.map((x) => (
            <tr key={x.column}>
              <td>{x.label}</td>
              <td>{x.source}</td>
              <td>{x.annual_mwh != null ? `${Math.round(x.annual_mwh).toLocaleString('en-IN')} MWh/yr, peak ${x.peak_mw?.toFixed(2)} MW`
                : x.annual_cf != null ? `${(x.annual_cf * 100).toFixed(1)}% average output`
                : x.outage_hours != null ? `${x.outage_hours} h of outage`
                : x.mean != null ? `₹${x.mean.toFixed(2)}/kWh average` : ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {ro ? <p className="hint">Create your own project to upload a load profile.</p> : (
        <>
          <div className={s.inline}>
            <input type="file" accept=".csv,text/csv" aria-label="load profile CSV"
                   onChange={(e) => { setFile(e.target.files?.[0] ?? null); setResult(null); }} />
            <label className="eyebrow" htmlFor="unit">values are</label>
            <select id="unit" value={unit} onChange={(e) => setUnit(e.target.value)}>
              <option value="kW">kW (average demand)</option>
              <option value="MW">MW (average demand)</option>
              <option value="kWh">kWh per interval</option>
            </select>
            <button className="act" onClick={upload} disabled={!file || busy}>
              {busy ? 'Checking…' : 'Upload load profile'}</button>
            {load?.source === 'uploaded' && (
              <button className="act ghost" disabled={busy}
                      onClick={async () => onChange(await projects.useSample(pid, 'load_mw'))}>
                Use the sample load instead</button>
            )}
          </div>
          <p className="hint">
            A CSV with a timestamp column and a load column, covering every 15-minute, 30-minute
            or hourly interval of {proj.year} in IST (interval start). Extra rows outside the year
            are ignored; gaps are refused rather than filled.
            {dirty && ' Save your other changes first if you changed the operating year.'}
          </p>
          {result && (
            <div className="card" style={{ marginTop: 10, borderLeft: `3px solid ${result.ok ? 'var(--green-700)' : 'var(--red-500)'}` }}>
              {result.ok ? (
                <p style={{ margin: 0, fontSize: 12 }}>
                  Loaded {result.rows_used.toLocaleString('en-IN')} rows at {result.resolution_min}-minute
                  resolution from <code>{result.time_column}</code> / <code>{result.value_column}</code>:
                  {' '}{Math.round(result.annual_mwh ?? 0).toLocaleString('en-IN')} MWh a year, peak
                  {' '}{result.peak_mw?.toFixed(2)} MW.
                </p>
              ) : (
                <>
                  <strong style={{ fontSize: 12 }}>Not loaded.</strong>
                  <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
                    {result.errors.map((e) => <li key={e} className={s.err}>{e}</li>)}
                  </ul>
                </>
              )}
              {result.warnings.length > 0 && (
                <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
                  {result.warnings.map((w) => <li key={w} className="hint">{w}</li>)}
                </ul>
              )}
            </div>
          )}
        </>
      )}
    </>
  );
}
