/**
 * A 2:1 isometric projection, carried over from the jouleWise drawing system.
 *
 * Everything on the stage is generated from plan coordinates rather than hand-authored
 * paths — the shed, the panel array that grows with a slider, the battery containers that
 * appear one by one. That is the only way a picture can stay honest while its inputs move.
 *
 * x runs to the lower right, y to the lower left, z straight up.
 */
export const U = 30; // half-width of one plan unit, in screen px
export const V = 15; // half-height
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
    top: poly([P(x, y, z + h), P(x + w, y, z + h), P(x + w, y + d, z + h), P(x, y + d, z + h)]),
    left: poly([P(x, y + d, z + h), P(x + w, y + d, z + h), P(x + w, y + d, z), P(x, y + d, z)]),
    right: poly([P(x + w, y, z + h), P(x + w, y + d, z + h), P(x + w, y + d, z), P(x + w, y, z)]),
  };
}

/** A flat quad on a horizontal plane — roof panels, floor markings, water. */
export function tile(x: number, y: number, w: number, d: number, z: number, ox = 0, oy = 0) {
  const P = (a: number, b: number) => iso(a, b, z, ox, oy);
  return poly([P(x, y), P(x + w, y), P(x + w, y + d), P(x, y + d)]);
}

/**
 * Fill a rectangular roof with panel tiles, row by row. Filling in reading order means
 * the array visibly grows from one corner as a slider moves rather than flickering into
 * a new pattern.
 */
export function panelGrid(o: {
  x: number; y: number; w: number; d: number; z: number;
  cols: number; rows: number; gap?: number; ox?: number; oy?: number;
}) {
  const { x, y, w, d, z, cols, rows, gap = 0.12, ox = 0, oy = 0 } = o;
  const cw = w / cols, ch = d / rows;
  const out: { key: string; d: string; order: number }[] = [];
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      out.push({
        key: `${r}-${c}`,
        d: tile(x + c * cw + gap / 2, y + r * ch + gap / 2, cw - gap, ch - gap, z, ox, oy),
        order: r * cols + c,
      });
    }
  }
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

/** A polyline through plan points at a given height — used for the power routes. */
export function route(points: [number, number][], z: number, ox = 0, oy = 0) {
  const P = points.map(([a, b]) => iso(a, b, z, ox, oy));
  return `M${P.map(pt).join('L')}`;
}

/** Midpoint of a route, for hanging a label on it. */
export function routeMid(points: [number, number][], z: number, ox = 0, oy = 0): Pt {
  const i = Math.floor((points.length - 1) / 2);
  const [a, b] = points[i];
  const [c, d] = points[Math.min(i + 1, points.length - 1)];
  return iso((a + c) / 2, (b + d) / 2, z, ox, oy);
}
