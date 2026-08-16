// Runtime smoke test for the template JS: mock the browser, load the script,
// and actually invoke the main render functions with realistic payloads so a
// ReferenceError (undefined variable) surfaces instead of hiding until runtime.
const fs = require('fs');
const vm = require('vm');
const path = process.argv[2];
const html = fs.readFileSync(path, 'utf8');
const js = html.match(/<script>([\s\S]*)<\/script>/)[1];

// A permissive mock: any property/method access returns another mock.
function mock() {
  const fn = function () { return mock(); };
  return new Proxy(fn, {
    get(_t, p) {
      if (p === 'style') return {};
      if (p === 'classList') return { toggle(){}, add(){}, remove(){}, contains(){return false} };
      if (p === 'dataset') return {};
      if (p === 'value') return '';
      if (p === 'textContent') return '';
      if (p === 'children' || p === 'files') return [];
      if (p === 'length') return 0;
      if (p === Symbol.toPrimitive) return () => 0;
      if (p === 'offsetWidth' || p === 'width' || p === 'height') return 300;
      return mock();
    },
    set() { return true; },
    apply() { return mock(); },
  });
}
const doc = {
  getElementById: () => mock(),
  createElement: () => mock(),
  querySelector: () => mock(),
  querySelectorAll: () => [],
  body: mock(),
  addEventListener() {},
};

const MOCK = {
  '/nutrition_data': { totals: { calories: 800, protein_g: 40, carbs_g: 60, fat_g: 20, count: 1 },
    targets: { calories: 2300, protein_g: 194, carbs_g: 250, fat_g: 64 },
    burned: 500, adjusted_calories: 2800, net_calories: 300,
    meals: [{ description: 'Dal', calories: 400, protein_g: 20, carbs_g: 55, fat_g: 9 }],
    week: [{ date: '2026-08-10', calories: 1000 }, { date: '2026-08-11', calories: 2000 }],
    protein_fix: { gap: 40, options: [{ name: 'Paneer', protein_g: 30 }] } },
  '/water': { ml: 1500, goal: 3500, count: 6, glass_ml: 250, week: [] },
  '/meal_quick': { frequent: [{ description: 'Dal', calories: 400, protein_g: 20, carbs_g: 55, fat_g: 9, count: 3 }], yesterday: [] },
  '/dashboard': { ready: true, name: 'K', date: '2026-08-16',
    week_activity: [{ date: '2026-08-16', dow: 'S', trained: true, rest: false, today: true }],
    workout: { day: 'A', name: 'Push', focus: 'chest', exercises: 6, done_today: false, rest_day: false },
    water: { ml: 1500, goal: 3500 },
    nutrition: { calories: 800, protein_g: 40, cal_target: 2300, adjusted_target: 2800, burned: 500, protein_target: 194, meals: 1 },
    habits: [{ name: 'Log meals', done_today: false, streak: 3 }],
    health: { steps: 9000, active_kcal: 500, resting_hr: 56, sleep_hours: 7, energy_score: 74 },
    recovery: { score: 7, label: 'train as planned', reasons: ['watch energy score 74/100'], source: 'watch' },
    streak: 3, consistent_weeks: 2, sessions_this_week: 3, days_per_week: 6, last_weight: 96.5 },
  '/money_data': { month: '2026-08', is_current: true, total: 700, avg_per_day: 350,
    by_category: [{ category: 'Food', amount: 500, budget: 8000 }],
    day_series: [{ date: '2026-08-03', amount: 700 }], months: [{ month: '2026-08', total: 700 }],
    recent: [{ id: 'e1', date: '2026-08-03', amount: 500, category: 'Food', description: 'dal' }] },
  '/stats': { total_sessions: 10, last_weight: 96.5, weight_delta_week: -0.3, next_day: 'A', next_name: 'Push',
    sessions_this_week: 3, days_per_week: 6, streak: 3, consistent_weeks: 2,
    recent_sessions: [{ day: 'A', name: 'Push', date: '2026-08-16', exercises: 3, detail: [] }],
    plateaus: [], volume: { this_week: 5000, last_week: 4000 },
    heatmap: [{ date: '2026-08-16', volume: 200, dow: 5 }] },
  '/habits': { habits: [] }, '/goals_data': { goals: [] }, '/records': { best: [], recent_prs: [] },
  '/measurements': { series: {}, latest: {} }, '/photos': { photos: [] },
  '/achievements': { badges: [] }, '/chart_data': { weight: [], volume: [], exercises: {} },
  '/cardio': { types: ['Run'], today: [], recent: [] },
};
const fetch = async (url) => {
  const key = url.split('?')[0];
  return { ok: true, status: 200, json: async () => MOCK[key] || {}, blob: async () => mock() };
};

const sandbox = {
  document: doc, window: { addEventListener() {}, matchMedia: () => ({ matches: false }) },
  navigator: { serviceWorker: { register: async () => {} }, vibrate() {}, mediaDevices: {} },
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  fetch, setTimeout: (f) => { try { f(); } catch (e) {} }, clearTimeout() {}, setInterval() {}, clearInterval() {},
  performance: { now: () => 0 }, requestAnimationFrame() {}, AbortController: function(){ this.abort=()=>{}; this.signal={}; },
  URL: { createObjectURL: () => 'blob:x', revokeObjectURL() {} }, alert() {}, confirm: () => true, prompt: () => null,
  console, Math, Date, JSON, Object, Array, Number, String, Boolean, parseInt, parseFloat, isNaN, Promise, Symbol,
  encodeURIComponent, decodeURIComponent,
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(js, sandbox);

(async () => {
  const fns = ['loadFuel', 'loadDashboard', 'loadMoney', 'loadStatsView', 'loadCardio', 'loadWater'];
  let failed = false;
  for (const name of fns) {
    try {
      if (typeof sandbox[name] === 'function') { await sandbox[name](); }
      else { console.error('MISSING fn:', name); failed = true; }
    } catch (e) {
      console.error(`RUNTIME ERROR in ${name}(): ${e.name}: ${e.message}`);
      failed = true;
    }
  }
  if (failed) process.exit(1);
  console.log('UI smoke OK — all render functions ran without ReferenceError');
})();
