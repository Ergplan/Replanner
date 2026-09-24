'use client';
/**
 * The client demo stage: a solar-cell gigafactory campus in dark mode.
 *
 * The layout follows a TOPCon/PERC cell fab. Six production halls form one spine in wafer
 * order (inbound, texturing, diffusion, ALD/PECVD, laser and print, firing and test), and
 * the plant a cell line cannot run without sits beside it: chemicals, bulk gases and the
 * silane bunker, ultrapure water, exhaust scrubbers, chillers, wastewater treatment, the
 * dispatch store and the main receiving substation.
 *
 * Built from the same 2:1 projection as the twin (lib/iso.ts): every roof, tank, container
 * and turbine comes from plan coordinates and is drawn back to front by the depth of its
 * plan centre. `progress` runs 0 to 1 over the demo; each lever fills in over its own
 * slice of it, so the picture builds in a readable order rather than all at once.
 */
import { useMemo, useState } from 'react';
import { box, ground, iso, panelGrid, poly, route, tile, U, V, H } from '@/lib/iso';
import { BESS_MW, BESS_MWH, BUILDINGS, Building, OA_SOLAR_MW, OA_WIND_MW, Selection } from '@/lib/demo';

const OX = 690, OY = 96;
const GW = 26, GD = 22;

export const D = {
  bg0: '#06080B', bg1: '#0C1117', ground: '#0D131A', grid: '#17202A', road: '#111922',
  top: '#1E2732', topHover: '#27323F', left: '#151C25', right: '#10161E', edge: '#2B3644',
  panel: '#16426F', panelHi: '#3B82C4', gold: '#CEAC10', goldDim: '#7A650E',
  wind: '#A9C3D6', text: '#C9D3DD', faint: '#6A7785', green: '#4FB286', blue: '#5B8DC4',
  plant: '#2A3440', tank: '#3A4757', water: '#15324A',
};

const clamp01 = (v: number) => Math.max(0, Math.min(1, v));
const P = (x: number, y: number, z = 0) => iso(x, y, z, OX, OY);

/** Slices of the run, as [start, length] of progress, in the order things appear. */
export const SLICES = {
  roofs: [0.02, 0.42], park: [0.14, 0.4], wind: [0.34, 0.36], bess: [0.55, 0.3],
} as const;
const sliceFrac = (p: number, [a, len]: readonly [number, number]) => clamp01((p - a) / len);

const TURBINES: [number, number][] = [[1.6, 10.2], [4.4, 10.6], [2.2, 12.6], [5.0, 13.0],
  [1.8, 15.0], [4.6, 15.4]];
const CONTAINERS = Array.from({ length: 10 }, (_, i) => ({
  x: 9.1 + (i % 5) * 0.66, y: 16.8 + Math.floor(i / 5) * 2.1 }));

/** A vertical cylinder standing on the plan point (x, y): tanks, scrubbers, silos. */
function Cylinder({ x, y, r, h, z = 0, fill = D.tank, top = '#56657A' }: {
  x: number; y: number; r: number; h: number; z?: number; fill?: string; top?: string;
}) {
  const [cx, cy] = P(x, y, z);
  const rx = r * U * Math.SQRT2, ry = r * V * Math.SQRT2, hh = h * H;
  return (
    <g>
      <path d={`M${cx - rx},${cy} L${cx - rx},${cy - hh} A${rx},${ry} 0 0 0 ${cx + rx},${cy - hh}
                L${cx + rx},${cy} A${rx},${ry} 0 0 1 ${cx - rx},${cy} Z`} fill={fill} />
      <path d={`M${cx},${cy + ry} L${cx},${cy - hh + ry} L${cx + rx},${cy - hh} L${cx + rx},${cy}
                A${rx},${ry} 0 0 1 ${cx},${cy + ry} Z`} fill="#000" opacity="0.18" />
      <ellipse cx={cx} cy={cy - hh} rx={rx} ry={ry} fill={top} stroke={D.edge} strokeWidth="0.5" />
    </g>
  );
}

function roofPanels(b: Building) {
  const m = 0.22;
  const cols = Math.max(3, Math.round((b.w - 2 * m) * 1.5));
  const rows = Math.max(2, Math.round((b.d - 2 * m) * 1.5));
  const grid = panelGrid({ x: b.x + m, y: b.y + m, w: b.w - 2 * m, d: b.d - 2 * m, z: b.h,
    cols, rows, ox: OX, oy: OY, gap: 0.09 });
  if (!b.keepClear) return grid;
  const k = b.keepClear, cw = (b.w - 2 * m) / cols, ch = (b.d - 2 * m) / rows;
  return grid.filter((_, i) => {
    const cx = m + ((i % cols) + 0.5) * cw, cy = m + (Math.floor(i / cols) + 0.5) * ch;
    return !(cx > k.x && cx < k.x + k.w && cy > k.y && cy < k.y + k.d);
  });
}

function RoofPlant({ b }: { b: Building }) {
  const k = b.keepClear;
  if (!k || !b.roofPlant) return null;
  if (b.roofPlant === 'fans') {
    // cooling-tower cells: a row of fan shrouds
    const n = 3;
    return <>{Array.from({ length: n }, (_, i) => {
      const cx = b.x + k.x + (i + 0.5) * (k.w / n), cy = b.y + k.y + k.d / 2;
      const c = box(cx - 0.42, cy - 0.42, 0.84, 0.84, 0.5, b.h, OX, OY);
      const [ex, ey] = P(cx, cy, b.h + 0.5);
      return <g key={i}><path d={c.left} fill="#1B232D" /><path d={c.right} fill="#161D26" />
        <path d={c.top} fill={D.plant} stroke={D.edge} strokeWidth="0.5" />
        <ellipse cx={ex} cy={ey} rx={0.34 * U * Math.SQRT2} ry={0.34 * V * Math.SQRT2} fill="#0B1117" />
      </g>;
    })}</>;
  }
  if (b.roofPlant === 'mau') {
    // cleanroom make-up air units
    return <>{[0, 1].map((i) => {
      const u = box(b.x + 0.3 + i * 1.4, b.y + 0.15, 1.1, 0.8, 0.55, b.h, OX, OY);
      return <g key={i}><path d={u.left} fill="#1B232D" /><path d={u.right} fill="#161D26" />
        <path d={u.top} fill={D.plant} stroke={D.edge} strokeWidth="0.5" /></g>;
    })}</>;
  }
  // process exhaust stacks: furnaces and firing lines vent through the roof
  return <>{[0.3, 1.25, 2.2].filter((dx) => dx + 0.35 < b.w).map((dx, i) => (
    <Cylinder key={i} x={b.x + dx + 0.18} y={b.y + 0.5} r={0.14} h={1.5} z={b.h}
              fill="#2A3440" top="#4A5767" />
  ))}</>;
}

function Flow({ pts, width, color, on }: {
  pts: [number, number][]; width: number; color: string; on: number;
}) {
  if (on <= 0 || width <= 0) return null;
  const d = route(pts, 0.06, OX, OY);
  return (
    <g opacity={on}>
      <path d={d} fill="none" stroke={color} strokeOpacity={0.18} strokeWidth={width + 5}
            strokeLinecap="round" strokeLinejoin="round" />
      <path d={d} fill="none" stroke={color} strokeWidth={width} strokeLinecap="round"
            strokeLinejoin="round" strokeDasharray="10 8">
        <animate attributeName="stroke-dashoffset" from="18" to="0" dur="0.9s"
                 repeatCount="indefinite" />
      </path>
    </g>
  );
}

export default function GigaFactory({ sel, progress, locked, onToggleRoof }: {
  sel: Selection; progress: number | null; locked: boolean;
  onToggleRoof: (id: string) => void;
}) {
  const [hover, setHover] = useState<string | null>(null);
  const g = useMemo(() => ground(GW, GD, OX, OY), []);
  const roofs = useMemo(() => Object.fromEntries(BUILDINGS.map((b) => [b.id, roofPanels(b)])), []);
  const park = useMemo(() => panelGrid({ x: 0.6, y: 0.8, w: 6.6, d: 7.4, z: 0.04,
    cols: 11, rows: 12, ox: OX, oy: OY, gap: 0.12 }), []);

  const p = progress ?? 0;
  const ran = progress !== null;
  const roofOrder = BUILDINGS.filter((b) => sel.roofs[b.id]).map((b) => b.id);
  const roofFrac = (id: string) => {
    if (!ran || !sel.roofs[id]) return 0;
    const k = roofOrder.indexOf(id);
    const [a, len] = SLICES.roofs;
    return clamp01((p - a - k * 0.025) / len);
  };
  const parkF = ran && sel.oaSolar ? sliceFrac(p, SLICES.park) : 0;
  const windF = ran && sel.wind ? sliceFrac(p, SLICES.wind) : 0;
  const bessF = ran && sel.bess ? sliceFrac(p, SLICES.bess) : 0;
  const reOn = Math.max(parkF, windF, ...BUILDINGS.map((b) => roofFrac(b.id)));

  // Everything that stands up, sorted back to front by the depth of its plan centre.
  type Item = { depth: number; key: string; node: React.ReactNode };
  const items: Item[] = [];
  const stand = (depth: number, key: string, node: React.ReactNode) => items.push({ depth, key, node });

  BUILDINGS.forEach((b) => {
    const faces = box(b.x, b.y, b.w, b.d, b.h, 0, OX, OY);
    const selected = !!sel.roofs[b.id];
    const f = roofFrac(b.id);
    const panels = roofs[b.id];
    const nLit = Math.floor(f * panels.length);
    const isHover = hover === b.id && !locked;
    stand(b.x + b.w / 2 + b.y + b.d / 2, b.id, (
      <g role="button" tabIndex={locked ? -1 : 0} aria-pressed={selected}
         aria-label={`${b.name}, ${b.role}: rooftop solar ${selected ? 'on' : 'off'}, ${b.roofMwp} MWp`}
         style={{ cursor: locked ? 'default' : 'pointer', outline: 'none' }}
         onMouseEnter={() => setHover(b.id)} onMouseLeave={() => setHover(null)}
         onFocus={() => setHover(b.id)} onBlur={() => setHover(null)}
         onClick={() => !locked && onToggleRoof(b.id)}
         onKeyDown={(e) => {
           if (!locked && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); onToggleRoof(b.id); }
         }}>
        <path d={faces.left} fill={D.left} stroke={D.edge} strokeWidth="0.6" />
        <path d={faces.right} fill={D.right} stroke={D.edge} strokeWidth="0.6" />
        <path d={faces.top} fill={isHover ? D.topHover : D.top}
              stroke={selected ? D.gold : D.edge} strokeWidth={selected ? 1.6 : 0.8}
              strokeDasharray={selected && f < 1 ? '6 4' : undefined} />
        {/* clerestory band on the front face, so the halls read as buildings */}
        <path d={poly([P(b.x + 0.25, b.y + b.d, b.h * 0.72), P(b.x + b.w - 0.25, b.y + b.d, b.h * 0.72),
          P(b.x + b.w - 0.25, b.y + b.d, b.h * 0.58), P(b.x + 0.25, b.y + b.d, b.h * 0.58)])}
              fill="#22303D" opacity="0.85" />
        {selected && f < 1 && (
          <path d={tile(b.x + 0.22, b.y + 0.22, b.w - 0.44, b.d - 0.44, b.h, OX, OY)}
                fill={D.gold} opacity={0.07} />
        )}
        {panels.slice(0, nLit).map((t) => (
          <path key={t.key} d={t.d} fill="url(#demoPanel)" stroke="#0A1A2B" strokeWidth="0.35" />
        ))}
        <RoofPlant b={b} />
      </g>
    ));
  });

  // ---- support plant, not clickable: no usable roof ----------------------------------
  // bulk gas yard (N2, O2, Ar, NH3) and the silane bunker, kept apart as silane must be
  [[14.9, 10.5, 0.32, 2.2], [15.8, 10.5, 0.32, 2.2], [16.7, 10.5, 0.32, 2.2],
   [14.9, 11.7, 0.26, 1.6], [15.8, 11.7, 0.26, 1.6]].forEach(([x, y, r, h], i) =>
    stand(x + y, `gas${i}`, <Cylinder x={x} y={y} r={r} h={h} />));
  const silane = box(16.4, 12.3, 0.9, 0.8, 0.9, 0, OX, OY);
  stand(16.8 + 12.7, 'silane', <g><path d={silane.left} fill="#2A2320" /><path d={silane.right} fill="#221C19" />
    <path d={silane.top} fill="#4A3A30" stroke="#6B4F3A" strokeWidth="0.6" /></g>);
  // acid and alkaline exhaust scrubbers
  [[21.1, 10.5], [21.1, 11.5], [21.1, 12.5]].forEach(([x, y], i) =>
    stand(x + y, `scr${i}`, <Cylinder x={x} y={y} r={0.3} h={3.6} fill="#2F3A47" top="#5A6A7E" />));
  // fluoride wastewater treatment: open clarifiers
  [[20.6, 16.9], [20.6, 18.6]].forEach(([x, y], i) => {
    const [cx, cy] = P(x, y, 0.35);
    stand(x + y, `wwt${i}`, <g>
      <Cylinder x={x} y={y} r={0.62} h={0.35} fill="#223040" top={D.water} />
      <ellipse cx={cx} cy={cy} rx={0.42 * U * Math.SQRT2} ry={0.42 * V * Math.SQRT2}
               fill="none" stroke="#2B5775" strokeWidth="0.8" />
    </g>);
  });
  // main receiving substation
  const mss = box(12.2, 15.6, 1.4, 1.2, 1.0, 0, OX, OY);
  stand(12.9 + 16.2, 'mss', <g><path d={mss.left} fill="#1C2530" /><path d={mss.right} fill="#161D26" />
    <path d={mss.top} fill="#34404E" stroke={D.gold} strokeWidth="0.8" /></g>);
  // DISCOM transmission line entering from the east
  [[25.6, 13.6], [26.0, 15.4]].forEach(([x, y], i) => {
    const [px, py] = P(x, y);
    stand(x + y, `tw${i}`, <g transform={`translate(${px},${py})`}>
      <path d="M0,0 L-10,-72 M0,0 L10,-72 M-10,-72 L10,-72 M-7,-50 L7,-50 M-4,-28 L4,-28 M-17,-64 L17,-64"
            stroke="#56657A" strokeWidth="1.8" fill="none" />
    </g>);
  });

  TURBINES.forEach(([x, y], i) => {
    const each = clamp01(windF * TURBINES.length - i);
    const [bx, by] = P(x, y);
    if (each <= 0) {
      if (sel.wind && !ran) stand(x + y, `t${i}`, (
        <ellipse cx={bx} cy={by} rx="9" ry="4.5" fill="none" stroke={D.gold} opacity="0.55"
                 strokeWidth="1" strokeDasharray="3 3" />));
      return;
    }
    const hTower = 96 * each;
    stand(x + y, `t${i}`, (
      <g transform={`translate(${bx},${by})`} opacity={0.35 + 0.65 * each}>
        <ellipse cx="0" cy="0" rx="7" ry="3.5" fill="#1B232D" />
        <path d={`M-2.2,0 L-1.1,${-hTower} L1.1,${-hTower} L2.2,0 Z`} fill={D.wind} />
        {each >= 1 && (
          <g transform={`translate(0,${-hTower})`}>
            <g>
              <animateTransform attributeName="transform" type="rotate" from={`${i * 40}`}
                                to={`${i * 40 + 360}`} dur={`${2.4 + (i % 3) * 0.3}s`}
                                repeatCount="indefinite" />
              {[0, 120, 240].map((a) => (
                <path key={a} d="M0,0 L-1.6,-4 L0,-30 L1.6,-4 Z" fill={D.wind} transform={`rotate(${a})`} />
              ))}
            </g>
            <circle r="3" fill="#E7EEF3" />
          </g>
        )}
      </g>
    ));
  });

  CONTAINERS.forEach((c, i) => {
    const each = clamp01(bessF * CONTAINERS.length - i);
    if (each <= 0) return;
    const f = box(c.x, c.y, 0.56, 1.75, 0.9, (1 - each) * 2.4, OX, OY);
    stand(c.x + c.y + 1, `c${i}`, (
      <g opacity={each}>
        <path d={f.left} fill="#1C2530" stroke={D.edge} strokeWidth="0.5" />
        <path d={f.right} fill="#161D26" stroke={D.edge} strokeWidth="0.5" />
        <path d={f.top} fill={p >= 1 ? '#2F5D43' : D.plant} stroke={D.edge} strokeWidth="0.5" />
      </g>
    ));
  });

  items.sort((a, b) => a.depth - b.depth);

  const gridW = Math.max(1.2, 9 * (1 - 0.75 * reOn * (sel.oaSolar || sel.wind ? 1 : 0.2)));
  const hovered = BUILDINGS.find((b) => b.id === hover);
  const spine = BUILDINGS.filter((b) => b.step);

  return (
    <svg viewBox="0 0 1500 860" role="group" aria-label="Solar cell gigafactory campus"
         style={{ display: 'block', width: '100%', height: 'auto' }}>
      <defs>
        <linearGradient id="demoSky" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={D.bg1} /><stop offset="1" stopColor={D.bg0} />
        </linearGradient>
        <linearGradient id="demoPanel" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor={D.panelHi} /><stop offset="1" stopColor={D.panel} />
        </linearGradient>
        <radialGradient id="demoGlow" cx="0.5" cy="0.45" r="0.6">
          <stop offset="0" stopColor="#12202E" stopOpacity="0.9" />
          <stop offset="1" stopColor={D.bg0} stopOpacity="0" />
        </radialGradient>
        <marker id="waferArrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7"
                markerHeight="7" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10 Z" fill={D.gold} />
        </marker>
      </defs>
      <rect width="1500" height="860" fill="url(#demoSky)" />
      <rect width="1500" height="860" fill="url(#demoGlow)" />

      <path d={g.outline} fill={D.ground} stroke={D.grid} strokeWidth="1" />
      <path d={g.grid} fill="none" stroke={D.grid} strokeWidth="0.6" opacity="0.7" />

      {/* site roads: the spine road, the service road and the east-west connector */}
      {[[8.2, 1.0, 0.6, 20.4], [8.8, 8.4, 17.2, 1.1], [8.8, 14.5, 17.2, 0.9]].map(([x, y, w, d], i) =>
        <path key={i} d={tile(x, y, w, d, 0.01, OX, OY)} fill={D.road} />)}

      {/* offsite, open access: wheeled to the site over the grid */}
      <path d={tile(0, 0, 7.6, 16.6, 0.01, OX, OY)} fill="#0B1117" stroke="#3A4656"
            strokeWidth="1" strokeDasharray="7 6" />
      <text x={P(0.3, -0.6)[0]} y={P(0.3, -0.6)[1]} fill={D.faint} fontFamily="var(--font-mono)"
            fontSize="12" letterSpacing="1.6">OFFSITE · OPEN ACCESS</text>

      {sel.oaSolar && !ran && (
        <path d={tile(0.6, 0.8, 6.6, 7.4, 0.02, OX, OY)} fill={D.gold} fillOpacity="0.05"
              stroke={D.gold} strokeWidth="1.2" strokeDasharray="6 5" />
      )}
      {park.slice(0, Math.floor(parkF * park.length)).map((t) => (
        <path key={t.key} d={t.d} fill="url(#demoPanel)" stroke="#0A1A2B" strokeWidth="0.35" />
      ))}

      <path d={tile(8.95, 16.6, 3.55, 4.3, 0.015, OX, OY)} fill="#0F161E"
            stroke={sel.bess && !ran ? D.gold : '#2B3644'} strokeWidth={sel.bess && !ran ? 1.2 : 0.8}
            strokeDasharray="6 5" />

      {/* power routes into the main receiving substation, under what stands up */}
      <Flow pts={[[25.9, 14.7], [14.4, 14.95], [13.6, 15.7]]} width={gridW} color={D.blue} on={1} />
      <Flow pts={[[7.2, 4.4], [7.95, 4.4], [7.95, 15.9], [12.2, 15.9]]} width={6.5}
            color={D.gold} on={parkF >= 1 ? 1 : parkF * 0.6} />
      <Flow pts={[[6.0, 12.8], [7.75, 12.8], [7.75, 16.25], [12.2, 16.25]]} width={5}
            color={D.wind} on={windF >= 1 ? 1 : windF * 0.6} />
      <Flow pts={[[11.1, 16.6], [12.4, 16.5]]} width={4.5} color={D.green} on={bessF >= 1 ? 1 : 0} />

      {items.map((it) => <g key={it.key}>{it.node}</g>)}

      {/* wafer flow along the front of the production spine, with the step numbers */}
      <path d={route([[9.4, 8.05], [25.5, 8.05]], 1.25, OX, OY)} fill="none" stroke={D.gold}
            strokeWidth="1.6" strokeDasharray="2 5" markerEnd="url(#waferArrow)" opacity="0.85"
            pointerEvents="none" />
      {spine.map((b) => {
        const [sx, sy] = P(b.x + b.w / 2, b.y + b.d, 1.25);
        return (
          <g key={b.id} transform={`translate(${sx},${sy})`} pointerEvents="none">
            <circle r="9" fill="#0A0F14" stroke={D.gold} strokeWidth="1" />
            <text y="4" textAnchor="middle" fontFamily="var(--font-mono)" fontSize="10.5" fill={D.gold}>{b.step}</text>
          </g>
        );
      })}
      <text x={P(25.9, 8.1, 1.25)[0] + 16} y={P(25.9, 8.1, 1.25)[1] + 4} fontFamily="var(--font-mono)"
            fontSize="11" letterSpacing="1.4" fill={D.gold} opacity="0.85" pointerEvents="none">
        WAFER FLOW</text>

      {/* roof labels, on top so nothing hides them */}
      {BUILDINGS.map((b) => {
        const [lx, ly] = P(b.x + b.w / 2, b.y + b.d / 2 + (b.keepClear?.y === 0 ? b.keepClear.d / 2 : 0), b.h);
        const on = !!sel.roofs[b.id];
        const w = Math.max(b.name.length * 7 + 18, hover === b.id ? 150 : 0);
        return (
          <g key={b.id} transform={`translate(${lx},${ly})`} pointerEvents="none">
            <rect x={-w / 2} y={-13} width={w} height={hover === b.id ? 40 : 24} rx="3"
                  fill="#0A0F14" fillOpacity="0.85" stroke={on ? D.gold : '#2B3644'} strokeWidth="0.8" />
            <text y="3" textAnchor="middle" fontFamily="var(--font-mono)" fontSize="11"
                  fill={on ? D.gold : D.text}>{b.name}</text>
            {hover === b.id && (
              <text y="19" textAnchor="middle" fontFamily="var(--font-mono)" fontSize="9.5" fill={D.faint}>
                {on ? `☀ ${b.roofMwp} MWp · click to remove` : `${b.roofMwp} MWp roof · click to add`}</text>
            )}
          </g>
        );
      })}

      {/* plant captions */}
      {/* plant tags sit above their equipment, where no roof label can land on them */}
      <PlantTag at={P(16.0, 10.5, 2.6)} text="Bulk gases" />
      <PlantTag at={P(21.4, 13.4)} text="Scrubbers" />
      <PlantTag at={P(19.4, 20.4)} text="Fluoride WWT" />
      <PlantTag at={P(12.9, 15.6, 1.3)} text="Main substation" />

      {/* offsite captions in the clear ground outside the site, off the turbines */}
      <Caption at={P(-0.4, 4.4)} anchor="end" title="Open-access solar" value={`${OA_SOLAR_MW} MW`}
               on={sel.oaSolar} done={parkF >= 1} />
      <Caption at={P(-0.4, 12.8)} anchor="end" title="Open-access wind" value={`${OA_WIND_MW} MW`}
               on={sel.wind} done={windF >= 1} />
      <Caption at={P(10.7, 21.8)} title="Battery storage" value={`${BESS_MW} MW · ${BESS_MWH} MWh`} on={sel.bess} done={bessF >= 1} />
      <Caption at={P(26.4, 17.2)} title="DISCOM 220 kV" value="grid supply" on done />
      {hovered && !locked && (
        <text x="750" y="842" textAnchor="middle" fontFamily="var(--font-mono)" fontSize="12.5"
              fill={D.text}>{hovered.step ? `Step ${hovered.step} · ` : ''}{hovered.name} — {hovered.role}</text>
      )}
    </svg>
  );
}

function PlantTag({ at, text }: { at: [number, number]; text: string }) {
  return (
    <text x={at[0]} y={at[1] - 8} textAnchor="middle" fontFamily="var(--font-mono)" fontSize="9.5"
          letterSpacing="1" fill={D.faint} pointerEvents="none">{text.toUpperCase()}</text>
  );
}

function Caption({ at, title, value, on, done, anchor = 'middle' }: {
  at: [number, number]; title: string; value: string; on: boolean; done: boolean;
  anchor?: 'start' | 'middle' | 'end';
}) {
  return (
    <g transform={`translate(${at[0]},${at[1]})`} textAnchor={anchor} opacity={on ? 1 : 0.4}
       pointerEvents="none">
      <text fontFamily="var(--font-mono)" fontSize="11" letterSpacing="1.4" fill={D.faint}>
        {title.toUpperCase()}</text>
      <text y="18" fontFamily="var(--font-mono)" fontSize="14" fill={on && done ? D.gold : D.text}>
        {on ? value : 'not selected'}</text>
    </g>
  );
}
