# The jouleWise isometric system — a build brief

This document is written to be handed to someone (or something) with no access to the
original code, and to be sufficient on its own to rebuild the whole isometric UI. It
carries the projection maths, the primitives in full, the drawing rules, the motion
vocabulary, the verification script, and the mistakes already paid for.

---

## 0. The prompt

> Build an isometric SVG scene for an energy system. Draw nothing by hand: define the
> site as a plan — flat `(x, y)` coordinates in ground units, plus heights — and project
> every shape through a single 2:1 isometric transform. The scene must stay correct while
> its inputs move, because sliders will change capacities and a solved dispatch will
> change the flows. A picture that has to be re-authored when a number changes is the
> failure mode this system exists to prevent.
>
> Use the primitives in §2 verbatim. Follow the draw order in §3, the composition recipe
> in §4, the growth rules in §5 and the flow rules in §6. Label with §7, colour with §8,
> animate with §9. Before calling it done, run the sweep in §11 and fix every collision it
> reports.

---

## 1. The governing idea

**Everything is generated from plan coordinates.** The shed, the panel array that grows
with a slider, the battery containers that appear one by one, the cable routes — all of
them are functions of data. No `<path d="M120,340L...">` written by hand anywhere.

The reason is not elegance. It is that these drawings sit next to a model whose numbers
change, and a hand-drawn picture silently stops telling the truth the moment an input
moves. A generated one cannot: if the capacity halves, the array halves, because the array
*is* the capacity.

---

## 2. The projection and the primitives

A 2:1 isometric: `x` runs to the lower right, `y` to the lower left, `z` straight up.
Two-to-one because the half-width `U` is twice the half-height `V`, which puts every
ground edge on a clean 26.57° and keeps the grid crisp at integer pixel steps.

```ts
export const U = 30; // half-width of one plan unit, in screen px
export const V = 15; // half-height  — U = 2V is what makes it 2:1
export const H = 24; // screen px per unit of height

export type Pt = [number, number];

export const iso = (x: number, y: number, z = 0, ox = 0, oy = 0): Pt => [
  ox + (x - y) * U,
  oy + (x + y) * V - z * H,
];

const pt = (p: Pt) => p.join(',');

/** A path through projected points, closed. */
export const poly = (pts: Pt[]) => `M${pts.map(pt).join('L')}Z`;

/** The three visible faces of an axis-aligned box, back to front. */
export function box(x: number, y: number, w: number, d: number, h: number, z = 0, ox = 0, oy = 0) {
  const P = (a: number, b: number, c: number) => iso(a, b, c, ox, oy);
  return {
    top:   poly([P(x, y, z + h), P(x + w, y, z + h), P(x + w, y + d, z + h), P(x, y + d, z + h)]),
    left:  poly([P(x, y + d, z + h), P(x + w, y + d, z + h), P(x + w, y + d, z), P(x, y + d, z)]),
    right: poly([P(x + w, y, z + h), P(x + w, y + d, z + h), P(x + w, y + d, z), P(x + w, y, z)]),
  };
}

/** A flat quad on a horizontal plane — roof panels, floor markings, water. */
export function tile(x: number, y: number, w: number, d: number, z: number, ox = 0, oy = 0) {
  const P = (a: number, b: number) => iso(a, b, z, ox, oy);
  return poly([P(x, y), P(x + w, y), P(x + w, y + d), P(x, y + d)]);
}

/**
 * Fill a rectangle with tiles, row by row, in reading order. Reading order matters:
 * it means the array grows from one corner as a slider moves, rather than flickering
 * into a new pattern each time the count changes.
 */
export function panelGrid(o: {
  x: number; y: number; w: number; d: number; z: number;
  cols: number; rows: number; gap?: number; ox?: number; oy?: number;
}) {
  const { x, y, w, d, z, cols, rows, gap = 0.12, ox = 0, oy = 0 } = o;
  const cw = w / cols, ch = d / rows;
  const out: { key: string; d: string; order: number }[] = [];
  for (let r = 0; r < rows; r++)
    for (let c = 0; c < cols; c++)
      out.push({
        key: `${r}-${c}`,
        d: tile(x + c * cw + gap / 2, y + r * ch + gap / 2, cw - gap, ch - gap, z, ox, oy),
        order: r * cols + c,
      });
  return out;
}

/** Ground plane diamond with a faint plan grid. */
export function ground(w: number, d: number, ox: number, oy: number) {
  const P = (a: number, b: number) => iso(a, b, 0, ox, oy);
  const outline = poly([P(0, 0), P(w, 0), P(w, d), P(0, d)]);
  const lines: string[] = [];
  for (let i = 1; i < w; i += 2) lines.push(`M${pt(P(i, 0))}L${pt(P(i, d))}`);
  for (let j = 1; j < d; j += 2) lines.push(`M${pt(P(0, j))}L${pt(P(w, j))}`);
  return { outline, grid: lines.join('') };
}

/** A polyline through plan points at a given height — cable and power routes. */
export function route(points: [number, number][], z: number, ox = 0, oy = 0) {
  return `M${points.map(([a, b]) => pt(iso(a, b, z, ox, oy))).join('L')}`;
}

/** Midpoint of a route, for hanging a label on it. */
export function routeMid(points: [number, number][], z: number, ox = 0, oy = 0): Pt {
  const i = Math.floor((points.length - 1) / 2);
  const [a, b] = points[i];
  const [c, d] = points[Math.min(i + 1, points.length - 1)];
  return iso((a + c) / 2, (b + d) / 2, z, ox, oy);
}
```

One more helper worth having for ribbons between distant nodes — a curve bowed along the
ground plane, which reads better than a straight line across a large site:

```ts
const arc = ([x1, y1]: Pt, [x2, y2]: Pt, bow = 26) =>
  `M${x1},${y1} Q${(x1 + x2) / 2},${(y1 + y2) / 2 + bow} ${x2},${y2}`;
```

---

## 3. Draw order and shading

SVG has no depth buffer, so **order is the depth buffer**. Paint back to front:

1. ground plane, then its grid lines
2. anything flat on the ground (boundaries, hardstanding, roads)
3. objects sorted by `x + y` ascending — larger `x + y` is nearer the viewer
4. routes and flow ribbons
5. labels
6. overlays (control layer, timestamp, warnings)

Each box gets three faces with a fixed relationship, which is what sells the solidity:

| face | relative lightness | why |
|---|---|---|
| `top` | lightest | catches the light |
| `left` | mid | the `+y` face, turned away |
| `right` | darkest | the `+x` face, in shadow |

Keep that relationship even when a face is doing something else — a container lid that
turns gold while discharging still has to be lighter than its own sides, or the object
stops reading as a solid.

---

## 4. Composition recipe

Fix an origin and a plan extent as module constants, then express every object as plan
geometry against them:

```ts
const OX = 470, OY = 150;     // where plan (0,0) lands on the canvas
const GW = 22, GD = 15;       // plan extent in ground units

const SHED = { x: 8, y: 5, w: 7, d: 6, h: 2.6 };
const YARD = { x: 3.0, y: 11.4 };
```

Rules that keep a scene from drifting:

- **Never guess a screen coordinate.** Anchors come from `iso(...)` of a plan point. If a
  label needs to sit beside the substation, project the substation's plan corner and
  offset from that, not from a number you eyeballed.
- **Choose the viewBox from the projected extent,** not by trial. The widest point is
  `iso(GW, 0)` and the lowest is `iso(GW, GD)`; add room for labels and overlays.
- **Put offsite things behind a boundary.** A dashed ground tile with its own caption
  reads instantly as "this is wheeled, and it pays charges" — a distinction no amount of
  legend text conveys as fast.
- **Reserve a corner for the control layer.** Draw it as a floating panel with dashed
  leaders to the plant, visually apart from the power routes. Control is not power, and
  the drawing should not imply it is.

---

## 5. Growth: how things appear

Anything a slider mounts should **arrive**, not snap.

- **Arrays.** `panelGrid` returns every tile; render the first `n` as lit and the rest at
  ~0.3 opacity. `n = round(installed / maximum × cols × rows)`. Because the grid fills in
  reading order, raising the slider extends the array from one corner.
- **Overflow.** When the roof is full, spill onto ground-mount rows in bands beside the
  building. A 45 MWp array on a 15 MW site genuinely does not fit on the roof, and the
  drawing should say so.
- **Discrete units.** One container per block of storage — `ceil(MWh / blockSize)`, capped
  at what stays legible (8 is about the limit before a yard turns to mush). Above the cap,
  let the label carry the number.
- **Empty states.** Zero storage is a dashed outline of the empty yard, not nothing. An
  absent object and an unrendered object look identical, and only one of them is honest.
- **Stagger the entrance.** `animation-delay: var(--d)` with `--d: ${i * 32}ms` gives a
  ripple across a new row instead of a slab appearing at once.

---

## 6. Flow lines

Flows read one solved record. Paused, the arrows must show exactly what was decided for
the interval on screen — never a loop with a life of its own.

```ts
// Readable across three orders of magnitude, and bounded so nothing becomes a slab.
const widthFor = (mw: number) => (mw <= 0.001 ? 0 : Math.min(9, 1.4 + 2.4 * Math.sqrt(mw)));

// Faster for bigger flows, clamped so it never strobes.
const durFor = (mw: number) => `${Math.max(0.5, Math.min(3.2, 5 / Math.max(mw, 0.35)))}s`;
```

- **Hide near-zero flows.** Return `null` below the threshold. A faint line for "nothing is
  happening" is worse than absence — it reads as a small flow.
- **Reverse, don't recolour.** When a flow changes sign, animate the dash offset the other
  way and switch the hue between import and export. Both cues, because either alone is
  ambiguous.
- **Draw a halo.** A wider, low-opacity copy of the same path underneath keeps a thin line
  legible over the ground grid.
- **Explain the scaling.** `√MW` width is not linear, so the legend must say so. A viewer
  who assumes linearity will misread every comparison.
- **Ribbons for annual energy** use a different scale from instantaneous flows:
  `1.7 + 9.5 × clamp(MU / max, 0, 1)`. Keep the two vocabularies visually distinct —
  dashed arcs for annual, solid animated dashes for interval.

---

## 7. Labels

One component, used everywhere, anchored to a projected plan point:

```tsx
function Label({ at, title, value, sub, anchor = 'middle' }: {
  at: Pt; title: string; value?: string; sub?: string; anchor?: 'start' | 'middle' | 'end';
}) {
  return (
    <g transform={`translate(${at[0]},${at[1]})`} textAnchor={anchor}>
      <text fontFamily="var(--font-mono)" fontSize="11.5" letterSpacing="1.1" fill={FAINT}>
        {title.toUpperCase()}
      </text>
      {value && <text y="19" fontFamily="var(--font-mono)" fontSize="16.5" fill={TEXT}>{value}</text>}
      {sub && <text y={value ? 35 : 17} fontFamily="var(--font-mono)" fontSize="11" fill={FAINT}>{sub}</text>}
    </g>
  );
}
```

- **Everything numeric is mono**, always. Tabular figures stop labels jittering as values
  change during playback.
- **Three tiers**: a spaced uppercase caption, a large value, a quiet qualifier.
- **Font sizes are large** — 11.5 / 16.5 / 11 px against a ~1180 px viewBox. Isometric
  scenes are usually shown scaled down; anything smaller becomes unreadable immediately.
- **Labels are the thing that collides.** Not the geometry. See §11.

---

## 8. Palette

Gold on charcoal. The full token set lives in `app/globals.css`; these are the ones the
stage uses.

| role | value | used for |
|---|---|---|
| stage background | `#14120B` → `#1A1813` | vertical gradient |
| ground fill | `#1A1813` | plan diamond |
| grid lines | `#2A2720` | faint plan grid |
| brand gold | `#CEAC10` | generation, discharge, the live path |
| gold dim | `#8A710A` | control layer, secondary routes |
| gold pale | `#F5EABF` | values on dark |
| panel | `#1C4E80` → `#3B82C4` | photovoltaic, as a gradient |
| steel / roof | `#3A3527` / `#2C281B` | box faces |
| text | `#C9C2AF` | values |
| faint | `#6B6453` | captions |
| import blue | `#5B8DC4` | grid import, exchange |
| charge green | `#3E8E5A` | charging, export |
| curtail red | `#C0453B` | spillage, violations |

Unlit and lit states of the same object should share a hue and differ in value, so a panel
that stops generating still reads as a panel.

---

## 9. Motion vocabulary

```css
@keyframes bx-rise  { from { opacity: 0; transform: translateY(9px) } to { opacity: 1; transform: none } }
@keyframes bx-dash  { to { stroke-dashoffset: -320 } }
@keyframes bx-pulse { 0%, 100% { opacity: .3 } 50% { opacity: 1 } }
@keyframes bx-spin  { to { transform: rotate(360deg) } }
@keyframes bx-bob   { 0%, 100% { transform: translateY(0) } 50% { transform: translateY(-3px) } }

/* Things a slider mounts fade up in place instead of snapping in. */
.bx-arrive { animation: bx-rise 460ms var(--ease-out) both; animation-delay: var(--d, 0ms); }

/* Value changes tween rather than jump, so playback reads as motion not flicker. */
.bx-iso path, .bx-iso line, .bx-iso rect, .bx-iso circle, .bx-iso text {
  transition: stroke-width 600ms var(--ease-out), stroke 600ms var(--ease-out),
              fill 600ms var(--ease-out), opacity 600ms var(--ease-out);
}
```

Turbines spin only when generating. Racks pulse only when loaded. **Motion is a readout,
not decoration** — if something moves while its value is zero, the drawing is lying.

---

## 10. Accessibility

- `role="img"` on the `<svg>` with an `aria-label` stating the timestamp and the headline
  figure, so the scene has a text equivalent at every step of playback.
- A **table view** toggle listing every solved quantity for the selected block. The scene
  is a summary; the table is the record.
- Keyboard-reachable transport controls; playback must be steppable without a pointer.
- Honour reduced motion:

```css
@media (prefers-reduced-motion: reduce) {
  .bx-arrive { animation: none; }
  .bx-iso *  { transition: none !important; animation: none !important; }
  .flow      { animation: none !important; }
}
```

---

## 11. Verification: sweep, don't squint

Label collisions are the defect this system produces, and they appear only at particular
input combinations. Check after **every** interaction, not once per screen.

```js
// getBBox() reports LOCAL coordinates, so transformed groups all look like they overlap.
// Screen rects are the honest test.
const check = () => {
  const svg = document.querySelector('.stage svg');
  const items = [...svg.querySelectorAll('text')].filter(t => t.textContent.trim()).map(t => {
    const b = t.getBoundingClientRect();
    return { s: t.textContent.trim().slice(0, 26), x: b.x, y: b.y, w: b.width, h: b.height };
  });
  const hit = (a, b) => !(a.x + a.w < b.x + 1 || b.x + b.w < a.x + 1 ||
                          a.y + a.h < b.y + 1 || b.y + b.h < a.y + 1);
  const out = [];
  for (let i = 0; i < items.length; i++)
    for (let j = i + 1; j < items.length; j++)
      if (hit(items[i], items[j])) out.push(`${items[i].s} × ${items[j].s}`);
  return { collisions: out, nan: items.filter(i => /NaN|undefined|Infinity/.test(i.s)) };
};

// A hidden pane clamps setTimeout to ~1s. Yield through MessageChannel instead, or a
// sweep of a few hundred interactions will time out instead of finishing in seconds.
const yieldTick = () => new Promise(r => {
  const c = new MessageChannel(); c.port1.onmessage = () => r(); c.port2.postMessage(0);
});
```

Drive every slider across its range and every playback step across several days, calling
`check()` after each change. Zero collisions and zero non-finite labels, or it is not done.

---

## 12. Paid for already

- **`getBBox()` is local, `getBoundingClientRect()` is screen.** A collision check built on
  the former reports that everything overlaps everything, which is useless in exactly the
  way that wastes an hour.
- **Sweep after every interaction, not once per screen.** Extending a sweep from per-tab to
  per-interaction immediately surfaced three collisions that had been shipping for weeks.
- **Near-zero is not zero.** Drawing a hairline for a 0.0001 MW flow reads as a real flow.
  Threshold and hide.
- **Formatters must survive `null` and `NaN`.** A capacity that has not solved yet is not
  a number, and `toFixed` on it renders "NaN MW" straight onto the stage. Return an em
  dash.
- **Never divide by zero for a derived figure.** Storage duration is `MWh / MW`; with no
  battery that is not `Infinity h`, it is "no storage".
- **A capacity of zero must still draw something.** Otherwise the viewer cannot tell an
  empty yard from a broken render.
- **Labels move, geometry rarely does.** When a scene breaks, it is almost always a caption
  that has wandered onto a neighbour at one particular slider position.

---

## 13. Rebuild checklist

1. Copy §2 verbatim into `lib/iso.ts`. Do not re-derive the projection.
2. Set `OX`, `OY`, `GW`, `GD`; draw the ground plane and confirm the diamond is centred.
3. Lay out plan constants for each object. Place boxes; check the three-face shading.
4. Add arrays with `panelGrid`, driven by an installed/maximum ratio.
5. Add discrete units (containers, turbines) with a legibility cap and an empty state.
6. Project every anchor; add labels with the §7 component.
7. Add routes and flows with the §6 scaling, hiding and reversal rules.
8. Add the control layer as a floating panel with dashed leaders, visually separate.
9. Wire the motion in §9 — and make every animation conditional on a real value.
10. Add `role="img"`, the table fallback and reduced-motion handling.
11. Run the §11 sweep across every slider and several days. Fix everything it finds.
12. Re-read §12 and check you have not re-committed any of it.
