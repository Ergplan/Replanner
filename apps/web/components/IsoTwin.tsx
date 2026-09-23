'use client';
/**
 * The factory stage.
 *
 * Every shape is generated from plan coordinates through the 2:1 isometric projection in
 * lib/iso.ts, the same system the other jouleWise drawings use. Nothing is a hand-drawn
 * path, which is what lets the panel array grow, the containers appear and the flow lines
 * reverse as the solved numbers change, without the picture quietly going out of step
 * with them.
 *
 * Flow widths and speeds come from one solved 15-minute record. They are a reading of
 * that record, not an animation loop with a life of its own: when playback is paused the
 * arrows show exactly what the optimiser decided for the block on screen.
 */
import { useMemo } from 'react';
import { box, ground, iso, panelGrid, poly, route, routeMid, tile } from '@/lib/iso';
import { mw, num } from '@/lib/format';

export type Flows = {
  loadMw: number;
  roofMw: number;            // onsite solar reaching the site
  oaSolarMw: number;         // wheeled solar, after losses
  oaWindMw: number;          // wheeled wind, after losses
  importUtilityMw: number;
  importMarketMw: number;
  exportMw: number;
  chargeMw: number;
  dischargeMw: number;
  socFrac: number | null;
  curtailedMw: number;
};

export type Sizes = {
  roofMwp: number;           // including anything already on the roof
  oaSolarMw: number;
  oaWindMw: number;
  bessMw: number;
  bessMwh: number;
};

const OX = 470, OY = 150;            // stage origin
const GW = 22, GD = 15;              // plan extent

const C = {
  ink: '#14120B', panel: '#1C4E80', panelLit: '#3B82C4',
  gold: '#CEAC10', goldDim: '#8A710A', pale: '#F5EABF',
  steel: '#3A3527', steelTop: '#4A442F', roof: '#2C281B', roofTop: '#3A3527',
  grid: '#2A2720', line: '#6B6453', text: '#C9C2AF', faint: '#6B6453',
  green: '#5FA97A', blue: '#5B8DC4', red: '#C0453B', wind: '#9FB8C9',
};

/** Stroke width that stays readable across three orders of magnitude of power. */
const widthFor = (mwVal: number) => (mwVal <= 0.001 ? 0 : Math.min(9, 1.4 + 2.4 * Math.sqrt(mwVal)));
/** Dash cycle time: faster for bigger flows, but bounded so it never strobes. */
const durFor = (mwVal: number) => `${Math.max(0.5, Math.min(3.2, 5 / Math.max(mwVal, 0.35)))}s`;

function Flow({ d, mwVal, color, reverse = false, dash = '9 7' }: {
  d: string; mwVal: number; color: string; reverse?: boolean; dash?: string;
}) {
  const w = widthFor(mwVal);
  if (w === 0) return null;                       // near-zero flows are hidden, not drawn faint
  return (
    <g>
      <path d={d} fill="none" stroke={color} strokeOpacity={0.22} strokeWidth={w + 3}
            strokeLinecap="round" />
      <path className="flow" d={d} fill="none" stroke={color} strokeWidth={w}
            strokeLinecap="round" strokeDasharray={dash}>
        <animate attributeName="stroke-dashoffset" from={reverse ? '-16' : '16'} to="0"
                 dur={durFor(mwVal)} repeatCount="indefinite" />
      </path>
    </g>
  );
}

function Label({ at, title, value, sub, anchor = 'middle', tone = C.text }: {
  at: [number, number]; title: string; value?: string; sub?: string;
  anchor?: 'start' | 'middle' | 'end'; tone?: string;
}) {
  return (
    <g transform={`translate(${at[0]},${at[1]})`} textAnchor={anchor}>
      <text fontFamily="var(--font-mono)" fontSize="11.5" letterSpacing="1.1" fill={C.faint}>
        {title.toUpperCase()}
      </text>
      {value && (
        <text y="19" fontFamily="var(--font-mono)" fontSize="16.5" fill={tone}>{value}</text>
      )}
      {sub && (
        <text y={value ? 35 : 17} fontFamily="var(--font-mono)" fontSize="11" fill={C.faint}>
          {sub}
        </text>
      )}
    </g>
  );
}

export default function IsoTwin({ flows, sizes, stamp, mode }: {
  flows: Flows; sizes: Sizes; stamp: string; mode: 'find_optimum' | 'manual';
}) {
  const g = useMemo(() => ground(GW, GD, OX, OY), []);

  // ---- the shed ---------------------------------------------------------------
  const shed = box(8, 5, 7, 6, 2.6, 0, OX, OY);
  const roofZ = 2.6;
  // Panel count tracks installed MWp, filling in reading order so the array grows from a
  // corner rather than reshuffling.
  const cols = 8, rows = 7;
  const panels = useMemo(() => panelGrid({
    x: 8.25, y: 5.25, w: 6.5, d: 5.5, z: roofZ, cols, rows, ox: OX, oy: OY }), []);
  const roofFill = Math.round((Math.min(sizes.roofMwp, 9) / 9) * cols * rows);
  const roofLit = flows.roofMw > 0.01;

  // ---- battery containers: one per 4 MWh, capped so the yard stays legible -------
  const nCont = Math.max(0, Math.min(8, Math.ceil(sizes.bessMwh / 9)));
  const containers = Array.from({ length: nCont }, (_, i) =>
    box(3.0 + (i % 4) * 1.15, 11.4 + Math.floor(i / 4) * 1.5, 1.0, 1.25, 0.85, 0, OX, OY));

  // ---- offsite park, drawn beyond a boundary so wheeling is visible -------------
  const parkPanels = useMemo(() => panelGrid({
    x: 0.6, y: 0.6, w: 4.6, d: 3.2, z: 0.05, cols: 6, rows: 4, ox: OX, oy: OY }), []);
  const parkFill = Math.round((Math.min(sizes.oaSolarMw, 60) / 60) * 24);
  const turbines = Math.max(0, Math.min(5, Math.ceil(sizes.oaWindMw / 4)));

  // ---- routes ------------------------------------------------------------------
  const rParkToPcc: [number, number][] = [[5.4, 2.2], [6.6, 3.4], [6.6, 7.0]];
  const rWindToPcc: [number, number][] = [[1.6, 6.2], [4.2, 6.6], [6.6, 7.0]];
  const rGridToPcc: [number, number][] = [[19.5, 9.4], [17.0, 8.6], [15.6, 8.0]];
  const rPccToShed: [number, number][] = [[6.6, 7.0], [7.8, 7.4]];
  const rShedToBess: [number, number][] = [[8.6, 10.9], [5.2, 11.6]];
  const rBessToShed: [number, number][] = [[5.2, 11.9], [8.9, 11.2]];
  const rMarketToPcc: [number, number][] = [[19.0, 13.0], [16.4, 11.4], [15.6, 8.4]];

  const importMw = flows.importUtilityMw;
  const netGrid = importMw - flows.exportMw;
  const socPct = flows.socFrac == null ? null : Math.round(flows.socFrac * 100);

  return (
    <div className="stage">
      <svg viewBox="0 0 1180 700" role="img"
           aria-label={`Isometric plant view for ${stamp}. Load ${flows.loadMw.toFixed(2)} megawatts.`}>
        <defs>
          <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#1A1813" /><stop offset="1" stopColor="#14120B" />
          </linearGradient>
          <linearGradient id="panelG" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor={C.panelLit} /><stop offset="1" stopColor={C.panel} />
          </linearGradient>
        </defs>
        <rect width="1180" height="700" fill="url(#sky)" />

        {/* ground plane */}
        <path d={g.outline} fill="#1A1813" stroke={C.grid} strokeWidth="1" />
        <path d={g.grid} fill="none" stroke={C.grid} strokeWidth="0.7" opacity="0.65" />

        {/* offsite boundary: everything left of this is wheeled, and pays for it */}
        <path d={tile(0, 0, 6.2, 8.2, 0.01, OX, OY)} fill="#201D14" stroke={C.goldDim}
              strokeWidth="1" strokeDasharray="6 5" opacity="0.85" />
        {/* caption sits above the park: below it would land on the wind and remote-solar
            labels, which the collision sweep picks up immediately */}
        <Label at={iso(0.2, -1.0, 0, OX, OY)} anchor="start" title="offsite · open access"
               sub="wheeled to site · charges apply" />

        {/* remote solar */}
        {parkPanels.map((p, i) => (
          <path key={p.key} d={p.d}
                fill={i < parkFill ? (flows.oaSolarMw > 0.01 ? 'url(#panelG)' : C.panel) : '#232016'}
                opacity={i < parkFill ? 1 : 0.32} stroke={C.grid} strokeWidth="0.4" />
        ))}
        <Label at={iso(2.9, 4.4, 0, OX, OY)} title="remote solar"
               value={`${num(sizes.oaSolarMw, 1)} MW`} sub={mw(flows.oaSolarMw)} tone={C.pale} />

        {/* wind */}
        {Array.from({ length: turbines }, (_, i) => {
          const [bx, by] = iso(0.9 + i * 1.05, 6.0, 0, OX, OY);
          const spin = flows.oaWindMw > 0.01;
          return (
            <g key={i} transform={`translate(${bx},${by})`}>
              <path d={`M0,0 L0,-52`} stroke={C.wind} strokeWidth="2.2" />
              <g transform="translate(0,-52)">
                {spin && <animateTransform attributeName="transform" type="rotate"
                          from="0" to="360" dur="2.6s" repeatCount="indefinite"
                          additive="sum" />}
                {[0, 120, 240].map((a) => (
                  <path key={a} d="M0,0 L0,-17" stroke={C.wind} strokeWidth="2.6"
                        strokeLinecap="round" transform={`rotate(${a})`} />
                ))}
              </g>
              <circle cx="0" cy="-52" r="2.4" fill={C.pale} />
            </g>
          );
        })}
        <Label at={iso(0.6, 7.4, 0, OX, OY)} anchor="start" title="remote wind"
               value={`${num(sizes.oaWindMw, 1)} MW`} sub={mw(flows.oaWindMw)} tone={C.pale} />

        {/* wheeled routes into the point of common coupling */}
        <Flow d={route(rParkToPcc, 0.1, OX, OY)} mwVal={flows.oaSolarMw} color={C.gold} />
        <Flow d={route(rWindToPcc, 0.1, OX, OY)} mwVal={flows.oaWindMw} color={C.wind} />

        {/* the shed */}
        <path d={shed.left} fill={C.roof} />
        <path d={shed.right} fill={C.steel} />
        <path d={shed.top} fill={C.roofTop} stroke={C.grid} strokeWidth="0.8" />
        {panels.map((p, i) => (
          <path key={p.key} d={p.d}
                fill={i < roofFill ? (roofLit ? 'url(#panelG)' : C.panel) : '#2A2720'}
                opacity={i < roofFill ? 1 : 0.3} stroke="#14120B" strokeWidth="0.35" />
        ))}
        <Label at={iso(11.5, 4.3, 2.6, OX, OY)} title="rooftop solar"
               value={`${num(sizes.roofMwp, 1)} MWp`} sub={mw(flows.roofMw)} tone={C.pale} />
        <Label at={iso(11.5, 11.6, 0, OX, OY)} title="factory load"
               value={mw(flows.loadMw)} tone={C.gold} />

        {/* battery yard */}
        {containers.map((c, i) => (
          <g key={i}>
            <path d={c.left} fill="#3A3527" />
            <path d={c.right} fill="#2C281B" />
            <path d={c.top} fill={flows.dischargeMw > 0.01 ? C.goldDim
              : flows.chargeMw > 0.01 ? '#2F5D43' : '#4A442F'} stroke={C.grid} strokeWidth="0.6" />
          </g>
        ))}
        {nCont === 0 && (
          <path d={tile(3.0, 11.4, 4.3, 2.4, 0.02, OX, OY)} fill="none" stroke={C.faint}
                strokeWidth="1" strokeDasharray="5 5" opacity="0.5" />
        )}
        <Label at={iso(2.6, 14.2, 0, OX, OY)} anchor="start" title="storage"
               value={sizes.bessMw > 0 ? `${num(sizes.bessMw, 1)} MW · ${num(sizes.bessMwh, 0)} MWh` : 'none'}
               sub={socPct == null ? 'no battery'
                 : `SOC ${socPct}% · ${flows.dischargeMw > 0.01 ? `out ${mw(flows.dischargeMw)}`
                    : flows.chargeMw > 0.01 ? `in ${mw(flows.chargeMw)}` : 'idle'}`}
               tone={C.pale} />
        <Flow d={route(rShedToBess, 0.2, OX, OY)} mwVal={flows.chargeMw} color={C.green} />
        <Flow d={route(rBessToShed, 0.2, OX, OY)} mwVal={flows.dischargeMw} color={C.gold} />

        {/* utility connection */}
        <g>
          {[0, 1].map((i) => {
            const [px, py] = iso(18.6 + i * 1.3, 9.0 + i * 0.5, 0, OX, OY);
            return (
              <g key={i} transform={`translate(${px},${py})`}>
                <path d="M0,0 L-9,-58 M0,0 L9,-58 M-6,-38 L6,-38 M-4,-22 L4,-22"
                      stroke={C.line} strokeWidth="2" fill="none" />
              </g>
            );
          })}
        </g>
        <Flow d={route(rGridToPcc, 0.1, OX, OY)} mwVal={Math.abs(netGrid)} color={netGrid >= 0 ? C.blue : C.green}
              reverse={netGrid < 0} />
        <Label at={iso(19.4, 10.6, 0, OX, OY)} anchor="start" title="DISCOM supply"
               value={mw(flows.importUtilityMw)}
               sub={flows.exportMw > 0.01 ? `export ${mw(flows.exportMw)}` : 'import only'}
               tone={C.pale} />

        {/* exchange */}
        <path d={box(18.4, 12.6, 1.4, 1.4, 1.1, 0, OX, OY).top} fill="#2C3A4A"
              stroke={C.blue} strokeWidth="0.8" />
        <path d={box(18.4, 12.6, 1.4, 1.4, 1.1, 0, OX, OY).left} fill="#1E2833" />
        <path d={box(18.4, 12.6, 1.4, 1.4, 1.1, 0, OX, OY).right} fill="#17202A" />
        <Flow d={route(rMarketToPcc, 0.1, OX, OY)} mwVal={flows.importMarketMw} color={C.blue} />
        <Label at={iso(18.6, 14.6, 0, OX, OY)} anchor="start" title="exchange · RTM"
               value={mw(flows.importMarketMw)} sub="forecast prices" tone={C.pale} />

        {/* point of common coupling */}
        <circle cx={iso(6.6, 7.0, 0.1, OX, OY)[0]} cy={iso(6.6, 7.0, 0.1, OX, OY)[1]}
                r="5" fill={C.gold} opacity="0.9" />
        <Flow d={route(rPccToShed, 0.15, OX, OY)}
              mwVal={flows.oaSolarMw + flows.oaWindMw} color={C.gold} />

        {/* ergOS: the control layer, deliberately drawn apart from the power routes */}
        <g opacity="0.92">
          <rect x="60" y="34" width="238" height="60" rx="4" fill="#201D14"
                stroke={C.goldDim} strokeWidth="1" strokeDasharray="4 4" />
          <text x="76" y="56" fontFamily="var(--font-mono)" fontSize="11" letterSpacing="1.4"
                fill={C.gold}>ergOS · DISPATCH ORCHESTRATION</text>
          <text x="76" y="76" fontFamily="var(--font-mono)" fontSize="11" fill={C.faint}>
            {mode === 'find_optimum' ? 'schedule from the certified optimum'
              : 'schedule from the manual scenario'}
          </text>
          {[[298, 64, 470, 132], [298, 64, 700, 150]].map(([x1, y1, x2, y2], i) => (
            <path key={i} d={`M${x1},${y1} C${x1 + 70},${y1} ${x2 - 70},${y2} ${x2},${y2}`}
                  fill="none" stroke={C.goldDim} strokeWidth="1" strokeDasharray="3 5"
                  opacity="0.6" />
          ))}
        </g>

        {/* stamp and curtailment */}
        <text x="1150" y="52" textAnchor="end" fontFamily="var(--font-mono)" fontSize="13"
              fill={C.text}>{stamp}</text>
        <text x="1150" y="72" textAnchor="end" fontFamily="var(--font-mono)" fontSize="10.5"
              letterSpacing="1.2" fill={C.faint}>SOLVED 15-MINUTE BLOCK</text>
        {flows.curtailedMw > 0.01 && (
          <text x="1150" y="94" textAnchor="end" fontFamily="var(--font-mono)" fontSize="11"
                fill={C.red}>curtailing {mw(flows.curtailedMw)}</text>
        )}
      </svg>
    </div>
  );
}
