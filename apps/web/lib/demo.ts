/**
 * The client demo's arithmetic. Illustrative, and said so on screen.
 *
 * The demo answers in five seconds, and the optimiser takes minutes, so nothing here is
 * solved. Each lever carries a fixed share of the saving available between today's DISCOM
 * tariff and the blended cost with every lever pulled. With everything selected the result
 * is exactly DEMO_TARGET, and switching a lever off gives some of the saving back. Storage
 * is worth far more with a renewable source to shift than on its own.
 */

export const DEMO_BASELINE = 6.70;          // INR/kWh, all-in DISCOM cost today
export const DEMO_TARGET = 3.89;            // INR/kWh, blended cost with every lever
export const ANNUAL_GWH = 780;              // a 5 GW TOPCon cell line, running round the clock
export const PEAK_MW = 105;
const GRID_T_PER_MWH = 0.716;               // CEA grid emission factor, tCO2/MWh

export type Building = {
  id: string; name: string; role: string;
  x: number; y: number; w: number; d: number; h: number;
  roofMwp: number;
  /** Process step along the wafer flow, for the production spine. */
  step?: number;
  /** Part of the roof kept clear for plant, as a plan rectangle relative to the roof. */
  keepClear?: { x: number; y: number; w: number; d: number };
  /** Roof plant standing in the kept-clear area. */
  roofPlant?: 'stacks' | 'fans' | 'mau';
};

/**
 * A TOPCon/PERC cell fab. The six production halls form one spine in wafer order, from
 * inbound store to test and sort; the support plant a cell line cannot run without sits
 * alongside it.
 */
export const BUILDINGS: Building[] = [
  { id: 'inbound', step: 1, name: 'Wafer inbound', role: 'Incoming inspection · wafer store',
    x: 9.0, y: 2, w: 2.4, d: 6, h: 2.2, roofMwp: 1.5 },
  { id: 'texture', step: 2, name: 'Texturing', role: 'Wet benches: saw-damage etch, texture, clean',
    x: 11.4, y: 2, w: 3.0, d: 6, h: 2.6, roofMwp: 1.9 },
  { id: 'diffusion', step: 3, name: 'Diffusion', role: 'POCl₃ / BCl₃ tube furnaces · LPCVD poly-Si',
    x: 14.4, y: 2, w: 3.0, d: 6, h: 3.0, roofMwp: 1.6, roofPlant: 'stacks',
    keepClear: { x: 0, y: 0, w: 3.0, d: 1.1 } },
  { id: 'coating', step: 4, name: 'ALD · PECVD', role: 'AlOₓ passivation · SiNₓ anti-reflection coating',
    x: 17.4, y: 2, w: 3.0, d: 6, h: 2.8, roofMwp: 1.6, roofPlant: 'mau',
    keepClear: { x: 0, y: 0, w: 3.0, d: 1.1 } },
  { id: 'print', step: 5, name: 'Laser · print', role: 'Laser contact opening · screen-print metallisation',
    x: 20.4, y: 2, w: 2.8, d: 6, h: 2.6, roofMwp: 1.8 },
  { id: 'firing', step: 6, name: 'Firing · test', role: 'Co-firing · LID regeneration · IV test & sort',
    x: 23.2, y: 2, w: 2.6, d: 6, h: 2.6, roofMwp: 1.4, roofPlant: 'stacks',
    keepClear: { x: 0, y: 0, w: 2.6, d: 1.1 } },
  { id: 'chem', name: 'Chemical store', role: 'HF · KOH · HNO₃ storage and distribution',
    x: 11.2, y: 10.0, w: 3.0, d: 2.6, h: 2.0, roofMwp: 0.8 },
  { id: 'upw', name: 'UPW plant', role: 'Ultrapure water for the wet benches',
    x: 17.6, y: 10.0, w: 3.0, d: 3.0, h: 2.6, roofMwp: 0.9 },
  { id: 'chillers', name: 'Chillers · CDA', role: 'Process cooling · compressed dry air',
    x: 22.6, y: 10.0, w: 3.0, d: 3.2, h: 2.4, roofMwp: 0.5, roofPlant: 'fans',
    keepClear: { x: 0, y: 0, w: 3.0, d: 1.9 } },
  { id: 'dispatch', name: 'Cell dispatch', role: 'Finished-cell packing and outbound store',
    x: 14.6, y: 16.0, w: 5.0, d: 3.6, h: 2.2, roofMwp: 1.9 },
  { id: 'admin', name: 'Admin · R&D', role: 'Offices · pilot line · reliability lab',
    x: 21.8, y: 16.0, w: 3.2, d: 3.0, h: 3.2, roofMwp: 0.9 },
];
export const ROOF_TOTAL_MWP = BUILDINGS.reduce((s, b) => s + b.roofMwp, 0);

export const OA_SOLAR_MW = 120;
export const OA_WIND_MW = 90;
export const BESS_MW = 60;
export const BESS_MWH = 240;

export type Selection = {
  roofs: Record<string, boolean>;
  oaSolar: boolean; wind: boolean; bess: boolean;
};

export type DemoResult = {
  costPerKwh: number; reShare: number; annualSavingCr: number; co2Tonnes: number;
  roofMwp: number; savingFrac: number;
};

export function demoResult(sel: Selection): DemoResult {
  const roofMwp = BUILDINGS.reduce((s, b) => s + (sel.roofs[b.id] ? b.roofMwp : 0), 0);
  const roofFrac = roofMwp / ROOF_TOTAL_MWP;
  const paired = sel.oaSolar || sel.wind;
  // Shares of the full saving; they sum to 1 with every lever on.
  const saving = 0.08 * roofFrac + (sel.oaSolar ? 0.42 : 0) + (sel.wind ? 0.32 : 0)
    + (sel.bess ? (paired ? 0.18 : 0.05) : 0);
  const re = 0.03 * roofFrac + (sel.oaSolar ? 0.38 : 0) + (sel.wind ? 0.27 : 0)
    + (sel.bess && paired ? 0.10 : 0);
  const cost = DEMO_BASELINE - (DEMO_BASELINE - DEMO_TARGET) * saving;
  return {
    costPerKwh: cost,
    reShare: re,
    annualSavingCr: ((DEMO_BASELINE - cost) * ANNUAL_GWH * 1e6) / 1e7,
    co2Tonnes: re * ANNUAL_GWH * 1000 * GRID_T_PER_MWH,
    roofMwp,
    savingFrac: saving,
  };
}
