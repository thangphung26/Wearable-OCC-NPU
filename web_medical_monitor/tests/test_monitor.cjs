/* Run with node tests/test_monitor.cjs. No browser or dependencies required. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../frontend/monitor.js'), 'utf8');
const { fieldValid, range, stepped, activity, plotTicks, elapsedTime } = require('../frontend/monitor.js');
// Relative clocks use whole seconds from the first session record.
assert.equal(elapsedTime(1000, 1000), "+0 s");
assert.equal(elapsedTime(494000, 1000), "+493 s");
assert.equal(elapsedTime(247400, 1000), "+246 s");
assert.equal(elapsedTime(0, 1000), "+0 s");
assert.equal(elapsedTime(1000, null), "—");
assert.equal(elapsedTime(NaN, 1000), "—");
// Tick selection is bounded and removes collisions even in undersized plots.
for (const height of [0, 10, 14, 27, 28, 64, 120]) {
 const y = value => height - value / 100 * height;
 const ticks = plotTicks([0, 100], 20, y);
 assert.ok(ticks.length <= 3);
 for (let i = 1; i < ticks.length; i++) assert.ok(Math.abs(y(ticks[i]) - y(ticks[i - 1])) >= 14);
}
assert.deepEqual(plotTicks([0, 100], 20, v => v / 10), [0]);
assert.deepEqual(plotTicks([0, 100], 20, v => v * .2), [0, 100]);
assert.deepEqual(plotTicks([0, 100], 20, v => v), [0, 60, 100]);
assert.equal(fieldValid({ hr: 0 }, 'hr'), false);
assert.equal(fieldValid({ hr: 70, ppg_valid: false }, 'hr'), false);
assert.equal(fieldValid({ temp_c: 36, temp_valid: false }, 'temp_c'), false);
assert.equal(fieldValid({ battery: 0 }, 'battery'), true);
assert.equal(fieldValid({ battery: 8 }, 'battery'), false);
assert.equal(fieldValid({ hr: NaN }, 'hr'), false);
for (const values of [[], [36.6], [30, 45], [10, 150, 9999]]) {
 const [lo, hi] = range(values, 6);
 assert.ok(hi - lo >= 6);
 for (const value of values) assert.ok(value > lo && value < hi);
}
const receipts = [{ timestamp_ms: 0, hr: 70 }, { timestamp_ms: 10, hr: 71 }, { timestamp_ms: 20, hr: 0 }, { timestamp_ms: 30, hr: 72 }, { timestamp_ms: 100, hr: 73 }];
assert.equal(stepped(receipts, 'hr', x => x, y => y, 25), ' M 0 70 H 10 V 71 M 30 72 M 100 73');
class Element {
 constructor() { this.children = []; this.attrs = {}; this.events = {}; this.textContent = ''; this.value = ''; this.clientWidth = 820; this.clientHeight = 274; this.classes = new Set(); this.classList = { add: name => this.classes.add(name), toggle: (name, force) => { const on = force === undefined ? !this.classes.has(name) : force; on ? this.classes.add(name) : this.classes.delete(name); return on; } }; this.dataset = {}; }
 append(...nodes) { this.children.push(...nodes); }
 replaceChildren(...nodes) { this.children = nodes; }
 setAttribute(name, value) { this.attrs[name] = value; }
 addEventListener(name, handler) { this.events[name] = handler; }
 before() {}
 focus() { this.focused = true; }
}
function setup(capture = false, query = '', saved = null, storageBlocked = false, replayData = null) {
 const nodes = new Map();
 const html = fs.readFileSync(path.join(__dirname, '../frontend/monitor.html'), 'utf8');
 for (const match of html.matchAll(/id="([^"]+)"/g)) nodes.set(match[1], new Element());
 const root = new Element(), body = new Element(); let stored = saved;
 nodes.get('themeOptions').hidden = true;
 const localStorage = { getItem: () => { if (storageBlocked) throw Error('blocked'); return stored; }, setItem: (key, value) => { if (storageBlocked) throw Error('blocked'); assert.equal(key, 'monitor-bg'); stored = value; } };
 let clock = 1000, stream; const requests = [];
 class FakeStream { constructor() { stream = this; this.events = {}; } addEventListener(name, handler) { this.events[name] = handler; } }
 const document = { body, documentElement: root, events: {}, addEventListener(name, handler) { this.events[name] = handler; }, getElementById: id => { assert.ok(nodes.has(id), id); return nodes.get(id); }, createElement: () => new Element(), createElementNS: () => new Element() };
 vm.runInNewContext(source, { document, URLSearchParams, localStorage, location: { search: query || (capture ? '?capture=1' : '') }, performance: { now: () => clock }, Date, EventSource: FakeStream, fetch: url => { requests.push(url); return replayData ? Promise.resolve({ ok: true, json: () => Promise.resolve(replayData) }) : new Promise(() => {}); }, setInterval: () => {}, window: { addEventListener() {} } });
 return { nodes, body, root, document, requests, hasStream: () => !!stream, stored: () => stored, event: (name, data) => stream.events[name]({ data: JSON.stringify(data) }), tick: ms => { clock += ms; }, open: () => stream.onopen() };
}
assert.equal(setup().nodes.get('receiptHeading').textContent, 'Receipt');
assert.equal(setup(false, '?clock=invalid').nodes.get('receiptHeading').textContent, 'Receipt');
const now = Date.now();
const packet = { source: 'simulation', patient: 1, seq: 3, timestamp_ms: now - 1000, hr: 71, spo2: 97, temp_c: 36.6, imu: 0, battery: 7, alert: 0, tx_alert_bit: 0, crc_status: 'unverified', decoder: 'simulated' };
const limits = { hr_low: 60, hr_high: 90, spo2_low: 95, temp_high_c: 38 };
const s = { source: 'simulation', thresholds: limits, data_age_sec: 1, valid: 1, rejected_crc: 2, duplicate_seq: 3, counters_since_ms: now - 10000, snapshot_period_s: 2 };
const app = setup(); const text = id => app.nodes.get(id).textContent;
app.event('init', { mode: 'simulation', telemetry: packet, stats: s }); app.open();
assert.equal(text('source'), 'SIM'); assert.equal(text('decoder'), 'simulated'); assert.equal(text('crc'), 'unverified');
assert.equal(text('freshness'), 'CURRENT'); assert.equal(text('value-hr'), '71'); 
assert.equal(app.nodes.has('value-imu'), false);
assert.equal(app.nodes.get('activity').children[1].textContent, 'Not measured');
assert.match(app.nodes.get('activity').children[2].textContent, /IMU not read/);
assert.match(app.nodes.get('limit-hr').title, /60–90/); assert.equal(text('status-hr'), 'OK');
assert.ok(app.nodes.get('limit-hr').children[0].children.some(n => n.attrs.class === 'gauge-marker-halo'));
assert.equal(text('valid'), '1'); assert.equal(text('deviceValid'), 'Not reported'); assert.equal(text('pipe-value-camera'), '30 FPS'); assert.equal(text('pipe-value-resample'), '20 Hz'); assert.equal(text('pipe-value-link'), '5 bit/s'); assert.match(app.nodes.get('pipe-camera').className, /simulated/);
assert.match(app.nodes.get('pipe-tx').className, /simulated/);
assert.doesNotMatch(app.nodes.get('pipe-tx').className, /active/);
assert.match(app.nodes.get('pipe-camera').title, /SIMULATED; no live hardware/);
app.event('telemetry', { ...packet, seq: 4, timestamp_ms: now, hr: 59, spo2: 0, ppg_valid: true });
assert.equal(text('status-hr'), 'Low'); assert.equal(text('status-spo2'), 'No data');
assert.equal(app.nodes.get('limit-spo2').children[0].children.some(n => n.attrs.class === 'gauge-marker'), false);
app.tick(26000); app.event('receiver_status', { ...s, data_age_sec: 26, stale: true });
assert.equal(text('freshness'), 'STALE'); assert.equal(text('status-hr'), 'Stale');
for (const stage of ['tx','link','camera','resample','decoder','crc','monitor']) assert.match(app.nodes.get('pipe-' + stage).className, /unknown/);
app.nodes.get('pause').events.click(); const frozen = text('trendMeta');
app.event('telemetry', { ...packet, timestamp_ms: now + 100, seq: 5 }); assert.equal(text('trendMeta'), frozen);
app.nodes.get('pause').events.click(); assert.ok(!text('trendMeta').includes('PAUSED'));
app.event('mode', { mode: 'serial' });
assert.equal(text('freshness'), 'WAITING'); assert.equal(text('value-hr'), '—'); assert.equal(app.nodes.get('packets').children.length, 1);
const serial = { ...packet, source: 'serial', timestamp_ms: now + 200, decoder: '<img onerror=bad()>', seq: 6, crc_status: 'valid' };
app.event('telemetry', serial);
const match = { rx: serial, patient: 1, seq: 6, is_match: true };
app.event('receiver_status', { ...s, source: 'serial', acquisition_fps: 29.5, serial_connected: true, data_age_sec: 0, receiver_valid_packets: 100, comparison_stats: { tx_connected: true, total_compared: 5, total_matched: 4, latest_match: match } });
assert.equal(text('match'), '4/5'); assert.equal(text('lastMatch'), 'Last matched seq: 6');
assert.equal(text('decoder'), '<img onerror=bad()>'); assert.equal(text('deviceValid'), '100'); assert.equal(text('valid'), '1');
assert.equal(text('fps'), '29.5 FPS'); assert.match(app.nodes.get('pipe-crc').className, /active/); assert.match(app.nodes.get('pipe-camera').className, /active/); assert.equal(app.nodes.get('matchRing').children[0].children[2].attrs['stroke-dasharray'], `${4 / 5 * 163.36} 163.36`);
app.event('receiver_status', { ...s, source: 'serial', data_age_sec: null, counters_since_ms: now + 300 });
assert.equal(text('freshness'), 'WAITING'); assert.equal(text('lastMatch'), 'Last matched seq: —');
app.event('mode', { mode: 'wifi' }); assert.equal(text('source'), 'UDP');
app.event('init', { mode: 'api', telemetry: { ...packet, source: 'api' }, stats: { ...s, source: 'api', data_age_sec: 40, stale: true } });
assert.equal(text('source'), 'API'); assert.equal(text('freshness'), 'STALE');
const paper = setup(true); for (let i = 0; i < 6; i++) { if (i === 0) paper.event('mode', { mode: 'simulation' }); paper.event('telemetry', { ...packet, timestamp_ms: now + i, seq: i }); }
assert.equal(paper.nodes.get('packets').children.length, 2); assert.ok(paper.body.classes.has('capture'));
function walk(node) { return [node, ...node.children.flatMap(walk)]; }
const plots = walk(app.nodes.get('plots'));
// At the CSS minimum, all three capture plotting rectangles retain 64 px.
paper.nodes.get('plots').clientHeight = 278;
paper.event('telemetry', { ...packet, timestamp_ms: now + 10, seq: 6 });
const captureRects = walk(paper.nodes.get('plots')).filter(n => n.attrs.width != null && n.attrs.height != null);
assert.equal(captureRects.length, 3);
assert.ok(captureRects.every(n => n.attrs.height >= 64));
assert.ok(plots.some(n => /^↑?90$/.test(n.textContent)));
for (const node of plots.filter(n => n.attrs.role === 'img')) assert.ok(node.attrs['aria-label']);
for (const row of paper.nodes.get('packets').children) assert.equal(row.children.length, 7);
assert.match(app.nodes.get('pipe-camera').className, /unknown/);
const markup = fs.readFileSync(path.join(__dirname, '../frontend/monitor.html'), 'utf8');
for (const svg of markup.matchAll(/<svg([^>]*)>(.*?)<\/svg>/gs)) { assert.match(svg[1], /aria-label=/); assert.match(svg[2], /<title>.+?<\/title>/); }
assert.match(markup, /id="activity"/);
assert.match(markup, /Research prototype · demo limits, not clinical thresholds · temperature uncalibrated · IMU validity unknown/);
console.log('Monitor tests passed: validity, ranges, gaps, source reset, freshness, CRC, comparisons, capture bounds and inert text.');

// IMU validity must be explicit; RX bits alone never imply an accelerometer reading.
const txValidity = { tx_connected: true, latest_tx: { patient: 1, seq: 6, imu_valid: true } };
for (const [imu, label] of [[0, 'Still'], [1, 'Moving'], [2, 'state 2 (unvalidated)'], [3, 'state 3 (unvalidated)']]) {
 assert.equal(activity({ ...serial, imu, imu_valid: true }).label, label);
 assert.equal(activity({ ...serial, imu }, txValidity).label, label);
 assert.equal(activity({ ...serial, imu }, txValidity).measured, true);
}
for (const imu_valid of [false, undefined, 'true', 1]) {
 const result = activity({ ...serial, imu_valid });
 assert.equal(result.measured, false); assert.equal(result.label, 'Not measured');
}
assert.equal(activity({ ...serial, imu_valid: false }, txValidity).measured, true);
assert.equal(activity(serial, { ...txValidity, tx_connected: false }).measured, true);
assert.equal(activity(serial, { ...txValidity, latest_tx: { patient: 2, imu_valid: true } }).measured, false);
assert.equal(activity(packet, txValidity).measured, false);
assert.equal(activity(null, txValidity).measured, false);
assert.equal(activity({ ...serial, imu: 4, imu_valid: true }).measured, false);
assert.equal(activity({ ...serial, imu: 3 }).label, 'Not measured');
const imuApp = setup(true);
imuApp.event('init', { mode: 'serial', telemetry: serial, stats: { ...s, source: 'serial', data_age_sec: 0, comparison_stats: txValidity } });
assert.equal(imuApp.nodes.get('activity').children[1].textContent, 'Still');
assert.doesNotMatch(imuApp.nodes.get('captureFootnote').textContent, /IMU not read/);
assert.match(imuApp.nodes.get('activity').title, /≥2 of last 10 one-second windows exceed 0.03 g SD; fall\/seizure not detected/);
imuApp.event('telemetry', { ...serial, imu: 1 });
assert.equal(imuApp.nodes.get('activity').children[1].textContent, 'Moving');
imuApp.event('comparison_stats', { ...txValidity, latest_tx: { patient: 1, seq: 6, imu_valid: false } });
assert.equal(imuApp.nodes.get('activity').children[1].textContent, 'Not measured');
assert.match(imuApp.nodes.get('captureFootnote').textContent, /IMU not read/);
imuApp.event('receiver_status', { ...s, source: 'serial', acquisition_fps: 30, resampling_hz: 20, data_age_sec: 0, comparison_stats: txValidity });
for (let i = 0; i < 6; i++) assert.equal(imuApp.nodes.get('pipe-arrow-' + i).attrs.class, 'flowing');
imuApp.event('receiver_status', { ...s, source: 'serial', data_age_sec: 26, stale: true });
for (let i = 0; i < 6; i++) assert.equal(imuApp.nodes.get('pipe-arrow-' + i).attrs.class, '');
for (const row of paper.nodes.get('packets').children) {
 assert.equal(row.children[5].children[0].attrs.role, 'img');
 assert.equal(row.children[5].children[1].textContent, 'SIM');
}
const edgeApp = setup();
edgeApp.event('init', { mode: 'simulation', telemetry: packet, stats: { ...s, thresholds: { ...limits, hr_low: 50, hr_high: 140 } } });
const edges = walk(edgeApp.nodes.get('plots')).filter(n => n.attrs.class === 'edge-limit');
const upper = edges.find(n => n.textContent === '↑140'), lower = edges.find(n => /^↓?50$/.test(n.textContent));
assert.ok(upper && lower); assert.ok(lower.attrs.y - upper.attrs.y >= 40);
const css = fs.readFileSync(path.join(__dirname, '../frontend/monitor.css'), 'utf8');
assert.match(css, /prefers-reduced-motion:reduce/);
assert.match(css, /white-space:nowrap;overflow:hidden;text-overflow:ellipsis/);
assert.match(css, /\.pipeline>svg\.flowing path/);
console.log('Round 3 passed: IMU validity paths, unvalidated states, footnote, connector freshness, decoder order and separated trend markers.');


// Themes: valid URL overrides saved preference without persisting screenshot variants.
assert.equal(setup().root.dataset.bg, 'midnight');
assert.equal(setup(false, '', 'graphite').root.dataset.bg, 'graphite');
assert.equal(setup(false, '?bg=clinical', 'ocean').root.dataset.bg, 'clinical');
assert.equal(setup(false, '?bg=clinical', 'ocean').stored(), 'ocean');
assert.equal(setup(false, '?bg=invalid', 'bogus').root.dataset.bg, 'midnight');
assert.equal(setup(false, '?bg=ocean', null, true).root.dataset.bg, 'ocean');
for (const theme of ['midnight', 'clinical', 'graphite', 'ocean']) {
 const themed = setup(true, '?capture=1&bg=' + theme);
 assert.equal(themed.root.dataset.bg, theme);
 assert.ok(themed.body.classes.has('capture'));
 themed.nodes.get('themeToggle').events.click();
 assert.equal(themed.nodes.get('themeToggle').attrs['aria-expanded'], 'true');
 assert.ok(themed.nodes.get('backgroundTheme').focused);
 themed.nodes.get('backgroundTheme').value = theme;
 themed.nodes.get('backgroundTheme').events.change();
 assert.equal(themed.stored(), theme);
 assert.equal(themed.nodes.get('themeOptions').hidden, true);
 assert.ok(themed.nodes.get('themeToggle').focused);
 themed.nodes.get('themeToggle').events.click();
 themed.document.events.keydown({ key: 'Escape' });
 assert.equal(themed.nodes.get('themeOptions').hidden, true);
 themed.nodes.get('themeToggle').events.click();
 themed.document.events.click({ target: { closest: () => null } });
 assert.equal(themed.nodes.get('themeOptions').hidden, true);
}
const blocked = setup(false, '', null, true);
blocked.nodes.get('backgroundTheme').value = 'clinical';
blocked.nodes.get('backgroundTheme').events.change();
assert.equal(blocked.root.dataset.bg, 'clinical');
assert.match(css, /body.capture \.theme-picker/);
assert.match(css, /body\.capture \.plot\{min-height:278px\}/);
assert.match(css, /body\.replay #ageChip\{display:none\}/);
assert.match(markup, /id="ageChip"/);
assert.match(css, /\.pipeline-node.simulated:after\{content:"SIM"/);
assert.match(css, /--stage\) 45%,var\(--secondary\)/);
assert.match(css, /stroke:var\(--connector-gradient\);stroke-dasharray:2 4/);
assert.match(markup, /color-mix\(in srgb,var\(--pipe-tx\) var\(--flow-strength\),var\(--secondary\)\)/);

// Composite every translucent surface before testing; ocean samples both gradient ends.
// All text roles are tested on every used ground, including hover/pressed/table states.
function rgb(hex) { return [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255); }
function luminance(rgb) { return rgb.map(c => c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4).reduce((n, c, i) => n + c * [.2126, .7152, .0722][i], 0); }
function contrast(a, b) { const [lo, hi] = [luminance(a), luminance(b)].sort((a, b) => a - b); return (hi + .05) / (lo + .05); }
const mix = (a, b, amount) => a.map((v, i) => v * amount + b[i] * (1 - amount));
function color(value, ground) {
 if (value.startsWith('#')) return rgb(value);
 const [, r, g, b, alpha] = value.match(/rgba\((\d+),(\d+),(\d+),([.\d]+)\)/);
 return mix([r, g, b].map(v => Number(v) / 255), ground, Number(alpha));
}
console.log('Theme contrast minima (text / status+trend text / pipeline text active+inactive / pipeline icons / connectors+gauges):');
assert.equal([...css.matchAll(/html\[data-bg="[^"]+"\]\{color-scheme:/g)].length, 4);
for (const [, theme, body] of css.matchAll(/html\[data-bg="([^"]+)"\]\{([^}]+)\}/g)) {
 if (!body.includes('--bg:')) continue;
 const tokens = Object.fromEntries([...body.matchAll(/--([a-z-]+):([^;]+)/g)].map(([, k, v]) => [k, v]));
 const grounds = [rgb(tokens.bg)];
 if (theme === 'ocean') grounds.push(rgb(tokens['ocean-end']));
 if (theme === 'midnight') {
   // Conservative simultaneous maximum of both radial glows.
   grounds.push(mix(rgb('#6366f1'), mix(rgb('#2dd4bf'), grounds[0], .06), .07));
 }
 const minima = { text: Infinity, status: Infinity, pipeline: Infinity, icons: Infinity, marks: Infinity };
 function check(category, a, b, minimum, label) {
   const ratio = contrast(a, b);
   minima[category] = Math.min(minima[category], ratio);
   assert.ok(ratio >= minimum, theme + ' ' + label + ': ' + ratio.toFixed(2));
 }
 for (const ground of grounds) {
   const card = color(tokens.card, ground), muted = rgb(tokens.muted);
   const plot = rgb(tokens.plot);
   const surfaces = [ground, card, muted, plot];
   for (const role of ['text', 'secondary']) for (const surface of surfaces)
     check('text', rgb(tokens[role]), surface, 4.5, role);
   for (const role of ['ok', 'alarm', 'warning', 'accent']) for (const surface of [ground, card, plot])
     check('status', rgb(tokens[role]), surface, 4.5, role);
   for (const surface of [card, plot]) {
     check('marks', rgb(tokens.track), surface, 3, 'gauge/ring track');
     for (const role of ['ok', 'warning', 'accent']) check('marks', rgb(tokens[role]), surface, 3, 'gauge/trend');
   }
   check('text', rgb(tokens['on-primary']), rgb(tokens.primary), 4.5, 'button/selection');
   for (const surface of [ground, card]) check('marks', rgb(tokens.primary), surface, 3, 'focus');
   const hues = Object.entries(tokens).filter(([key]) => key.startsWith('pipe-')).map(([, value]) => rgb(value));
   assert.equal(hues.length, 7);
   for (const hue of hues) for (const active of [true, false]) {
     const ink = active ? hue : mix(hue, rgb(tokens.secondary), .45);
     const badge = mix(ink, card, active ? .14 : .10);
     check('pipeline', ink, card, 4.5, 'pipeline text');
     check('icons', ink, badge, 3, 'pipeline badge');
   }
   // Test all interpolated connector colours, not just endpoints.
   for (let i = 0; i < hues.length - 1; i++) for (const strength of [1, .45])
     for (let f = 0; f <= 1; f += .1) {
       const hue = mix(hues[i], hues[i + 1], f);
       check('marks', mix(hue, rgb(tokens.secondary), strength), card, 3, 'connector');
     }
 }
 console.log(theme + ': ' + Object.values(minima).map(n => n.toFixed(2) + ':1').join(' / '));
}
console.log('Round 4 passed: theme precedence, persistence, unavailable storage, picker keyboard/close behavior, SIM hardware labels, theme contrast.');

// The saved hardware capture uses integer TX validity and latest_tx is one seq ahead.
const fixtureRoot = path.join(__dirname, '../../results/real_sensor_demo/20261108_final_monitor');
const fixture = name => JSON.parse(fs.readFileSync(path.join(fixtureRoot, name + '.json'), 'utf8'));
const savedStatus = fixture('status'), savedComparison = fixture('comparison'), savedHistory = fixture('history');
assert.equal(savedHistory.length, 53);
const hardware = setup(true);
hardware.event('init', savedStatus);
assert.equal(hardware.nodes.get('activity').children[1].textContent, 'Still');
assert.doesNotMatch(hardware.nodes.get('captureFootnote').textContent, /IMU/);
assert.equal(hardware.nodes.get('roi').textContent, '172,92 · 24×24');
assert.equal(hardware.nodes.get('fps').textContent, '29.6 FPS');
assert.equal(hardware.nodes.get('pipe-value-camera').textContent, '29.6 FPS');
const displayed = savedStatus.current_telemetry;
const pairing = savedComparison.history.find(m => m.rx.timestamp_ms === displayed.timestamp_ms);
assert.ok(pairing);
assert.notEqual(savedComparison.stats.latest_tx.seq, displayed.seq);
assert.equal(activity(displayed, { tx_connected: true, latest_tx: savedComparison.stats.latest_tx }).measured, false);
assert.equal(activity(displayed, {}, [pairing]).label, 'Still');
assert.equal(activity({ ...displayed, timestamp_ms: displayed.timestamp_ms + 160000 }, {}, [pairing]).measured, false);
assert.equal(activity(displayed, { tx_connected: true }).detail, 'Field 0 · validity unknown');
assert.equal(activity(displayed, { tx_connected: false }).detail, 'IMU not read');
assert.equal(activity(displayed, { tx_connected: true, latest_tx: { ...displayed, imu_valid: false } }).label, 'Not measured');
hardware.event('comparison_stats', { ...savedComparison.stats, total_matched: 52, match_basis: 'frame_hex' });
assert.equal(hardware.nodes.get('match').textContent, '52/52');
assert.match(hardware.nodes.get('matchRing').title, /bit-exact frame match \(frame_hex\)/);
hardware.event('telemetry', { ...displayed, crc_status: 'verified-by-host' });
assert.match(hardware.nodes.get('pipe-crc').className, /active/);
assert.equal(hardware.nodes.get('pipe-crc').title, 'CRC-8 recomputed by host from frame_hex');
assert.equal(hardware.nodes.get('crcBadge').title, 'CRC-8 recomputed by host from frame_hex');
hardware.event('telemetry', displayed);
assert.match(hardware.nodes.get('pipe-crc').className, /unknown/);
assert.match(css, /#roi,#fps\{white-space:nowrap;overflow-wrap:normal\}/);
console.log('Hardware fixture regressions passed: paired IMU, sequence wrap, frame-match ring, compact values and host CRC evidence.');

// Replay must make exactly one read-only request, freeze the time window, and account for every RX.
(async () => {
 const root = path.join(__dirname, '../../results/real_sensor_demo/20261108_final_track_imu');
 const read = name => JSON.parse(fs.readFileSync(path.join(root, name + '.json'), 'utf8'));
 const history = read('history'), status = read('status'), comparison = read('comparison');
 const txs = comparison.history.map(pair => pair.tx);
 const annotated = history.map(row => ({ ...row, tx_match: txs.some(tx => tx.frame_hex === row.frame_hex), match_basis: 'frame_hex' }));
 const accounting = { history: annotated, stats: { rx_bit_exact: 48, rx_unmatched: 2, rx_unchecked: 0 } };
 const replay = setup(true, '?replay=20261108_final_track_imu&capture=1&bg=clinical', null, false, { history, status, comparison, accounting });
 await new Promise(resolve => setImmediate(resolve));
 assert.deepEqual(replay.requests, ['/api/replay/20261108_final_track_imu']);
 assert.equal(replay.hasStream(), false);
 assert.equal(replay.nodes.get('match').textContent, '48/50');
 assert.match(replay.nodes.get('unmatchedNote').textContent, /2 RX frames not transmitted/);
 assert.match(replay.nodes.get('matchRing').title, /CRC-8 false accept/);
 assert.equal(replay.nodes.get('source').textContent, 'SERIAL · recorded');
 assert.equal(replay.nodes.get('freshness').textContent, '21:35:41–21:43:54');
 assert.equal(replay.nodes.get('age').textContent, '0.0 s');
 assert.ok(replay.body.classes.has('replay'));
 const trendGroups = replay.nodes.get('plots').children[0].children.filter(n => n.attrs.style);
 assert.equal(trendGroups.length, 3);
 const edgePositions = [];
 for (const group of trendGroups) {
   const ticks = group.children.filter(n => n.attrs.class === 'axis-tick');
   assert.ok(ticks.length <= 3);
   const positions = ticks.map(n => n.attrs.y).sort((a, b) => a - b);
   for (let i = 1; i < positions.length; i++) assert.ok(positions[i] - positions[i - 1] >= 14);
   const rect = walk(group).find(n => n.attrs.height != null && n.attrs.width != null);
   for (const label of group.children.filter(n => n.attrs.class === 'edge-limit')) {
     assert.equal(label.attrs['text-anchor'], 'end');
     assert.ok(label.attrs.x > rect.attrs.x + rect.attrs.width);
     assert.ok(label.attrs.y >= rect.attrs.y + 4 && label.attrs.y <= rect.attrs.y + rect.attrs.height + 4);
     edgePositions.push(label.attrs.y);
   }
 }
 edgePositions.sort((a, b) => a - b);
 for (let i = 1; i < edgePositions.length; i++) assert.ok(edgePositions[i] - edgePositions[i - 1] >= 14);
 const meta = replay.nodes.get('trendMeta').textContent;
 replay.tick(1000000);
 replay.nodes.get('pause').events.click();
 assert.match(replay.nodes.get('trendMeta').textContent, /50 snapshots/);
 replay.nodes.get('pause').events.click();
 assert.equal(replay.nodes.get('trendMeta').textContent, meta);
 const points = walk(replay.nodes.get('plots')).filter(n => n.attrs.class === 'point unmatched');
 assert.equal(points.length, 6);
 assert.ok(points.every(n => n.children.some(c => /not transmitted/.test(c.textContent))));
 assert.ok(walk(replay.nodes.get('plots')).some(n => n.attrs.class === 'edge-limit' && n.textContent === '140'));
 assert.equal(walk(replay.nodes.get('activityStrip')).filter(n => n.attrs.class === 'activity-mark' || n.attrs.class === 'unmatched').length, 50);
 const relative = setup(true, '?replay=20261108_final_track_imu&capture=1&bg=clinical&clock=relative', null, false, { history, status, comparison, accounting });
 await new Promise(resolve => setImmediate(resolve));
 assert.deepEqual(relative.requests, ['/api/replay/20261108_final_track_imu']);
 assert.equal(relative.hasStream(), false);
 assert.equal(relative.nodes.get('freshness').textContent, 'recorded · 493 s');
 assert.equal(relative.nodes.get('receipt').textContent, '+493 s');
 assert.equal(relative.nodes.get('receiptHeading').textContent, 'Elapsed');
 assert.equal(relative.nodes.get('counterScope').textContent, 'Dashboard counters · recorded session');
 assert.deepEqual(relative.nodes.get('packets').children.map(r => r.children[0].textContent), ['+493 s', '+483 s']);
 assert.ok(relative.nodes.get('packets').children.every(r => r.children[0].dataset.label === 'Elapsed'));
 assert.deepEqual(relative.nodes.get('plots').children[0].children.filter(n => n.attrs.y === relative.nodes.get('plots').clientHeight - 2).map(n => n.textContent), ['+0 s', '+246 s', '+493 s']);
 const live = setup();
 live.event('init', { mode: 'serial', telemetry: { ...annotated[0], tx_match: false }, stats: { ...s, source: 'serial', data_age_sec: 0, comparison_stats: { tx_connected: true, ...accounting.stats } } });
 const cell = live.nodes.get('packets').children[0].children[8];
 assert.equal(cell.children[1].textContent, 'not transmitted');
 assert.equal(cell.children[0].attrs['aria-label'], 'not transmitted');
 console.log('Replay passed: 48/50, false-accept markers, raw activity, frozen window, edge limit and no live stream.');
})().catch(error => { console.error(error); process.exitCode = 1; });

// The same option works independently of replay, capture, and source mode.
for (const mode of ['simulation', 'serial', 'wifi', 'api']) {
 const relative = setup(false, '?clock=relative');
 relative.event('init', { mode, telemetry: { ...packet, source: mode }, stats: { ...s, source: mode } });
 relative.event('telemetry', { ...packet, source: mode, timestamp_ms: packet.timestamp_ms + 10000 });
 assert.equal(relative.nodes.get('receipt').textContent, '+10 s');
 assert.equal(relative.nodes.get('receiptHeading').textContent, 'Elapsed');
 relative.event('log', { timestamp_ms: packet.timestamp_ms + 10000, time: '12:34:56', text: 'Received' });
 assert.equal(relative.nodes.get('log').children[0].textContent, '+10 s · info · Received');
 relative.event('mode', { mode: mode === 'api' ? 'serial' : 'api' });
 relative.event('telemetry', { ...packet, source: mode === 'api' ? 'serial' : 'api', timestamp_ms: now });
 assert.equal(relative.nodes.get('receipt').textContent, '+0 s');
}
