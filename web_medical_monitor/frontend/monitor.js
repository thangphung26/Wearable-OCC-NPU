/* Source-scoped receipt telemetry. No waveform data is present in this protocol. */
(function () {
  'use strict';
  const finite = Number.isFinite;
  function fieldValid(row, key) {
    if (!row || !finite(row[key]) || row[key + '_valid'] === false) return false;
    if (key === 'hr' || key === 'spo2') return row[key] > 0 && row.ppg_valid !== false;
    if (key === 'temp_c') return row[key] > 0 && row.temp_valid !== false;
    return key === 'battery' ? row[key] >= 0 && row[key] <= 7 : true;
  }
  function range(values, minimumSpan) {
    const lo = values.length ? Math.min(...values) : 0;
    const hi = values.length ? Math.max(...values) : minimumSpan;
    const span = Math.max(minimumSpan, hi - lo);
    const mid = (lo + hi) / 2;
    return [mid - span * .6, mid + span * .6];
  }
  function stepped(rows, key, x, y, maxGap) {
    let previous = null, path = '';
    for (const row of rows) {
      if (!fieldValid(row, key)) { previous = null; continue; }
      const px = x(row.timestamp_ms), py = y(row[key]);
      path += !previous || row.timestamp_ms - previous.timestamp_ms > maxGap
        ? ` M ${px} ${py}` : ` H ${px} V ${py}`;
      previous = row;
    }
    return path;
  }
  // Prefer endpoint ticks; retain the nice midpoint only when every label fits.
  function plotTicks(bounds, step, y, minSpacing = 14) {
    const [lo, hi] = bounds;
    const mid = Math.round((lo + hi) / (2 * step)) * step;
    const kept = [lo];
    for (const value of [hi, mid]) {
      if (value > lo && value <= hi && kept.every(v => Math.abs(y(v) - y(value)) >= minSpacing)) kept.push(value);
    }
    return kept.sort((a, b) => a - b);
  }
  // RX carries two IMU bits; TX validity must belong to the displayed snapshot.
  function activity(row, comparison = {}, matches = []) {
    const paired = [...matches, comparison.latest_match].find(item => item?.tx &&
      item.patient === row?.patient && item.seq === row?.seq &&
      item.rx?.timestamp_ms === row?.timestamp_ms);
    const latest = comparison.latest_tx;
    const tx = row?.source === 'serial' ? paired?.tx ??
      (latest?.patient === row.patient && latest?.seq === row.seq ? latest : null) : null;
    const validity = tx?.imu_valid ?? row?.imu_valid;
    const raw = Number.isInteger(row?.imu) && row.imu >= 0 && row.imu <= 3;
    const measured = !!((validity === true || tx && validity === 1) && raw);
    const unvalidated = raw && row.imu >= 2;
    const unknown = comparison.tx_connected === true && validity == null;
    return {
      measured,
      label: measured ? unvalidated ? `state ${row.imu} (unvalidated)` : row.imu === 0 ? 'Still' : 'Moving' : 'Not measured',
      icon: measured && !unvalidated ? row.imu === 0 ? 'person-still' : 'person-moving' : 'sensor-off',
      detail: measured ? 'Measured' : unknown ? raw ? `Field ${row.imu} · validity unknown` : 'Validity unknown' : 'IMU not read',
      footnote: measured ? '' : unknown ? ' · IMU validity unknown' : ' · IMU not read'
    };
  }
  const crcVerified = row => ['valid', 'verified-by-host'].includes(row?.crc_status);
  const crcDetail = row => row?.crc_status === 'verified-by-host' ? 'CRC-8 recomputed by host from frame_hex' :
    'CRC status: ' + (row?.crc_status ?? 'unknown') + '; RX publishes only frames that passed its on-board CRC-8 check';
  const elapsedTime = (ms, start) => finite(ms) && finite(start) ? `+${Math.round(Math.max(0, ms - start) / 1000)} s` : "—";
  const fpsText = value => Number.isFinite(value) ? `${Number(value.toFixed(1))} FPS` : '—';
  if (typeof module !== 'undefined') module.exports = { fieldValid, range, stepped, activity, plotTicks, elapsedTime };
  if (typeof document === 'undefined') return;
  const $ = id => document.getElementById(id);
  const put = (id, value) => { $(id).textContent = String(value); };
  // Outline SVG adaptations: Phosphor icons.csv entries; specialized symbols use row 105.
  const iconPaths = {"heart": "M20 5a5 5 0 0 0-8 1 5 5 0 0 0-8-1c-5 5 3 11 8 16 5-5 13-11 8-16Z", "drop": "M12 3C9 7 5 11 5 15a7 7 0 0 0 14 0c0-4-4-8-7-12Z", "thermometer": "M9 14V5a3 3 0 0 1 6 0v9a5 5 0 1 1-6 0ZM12 8v10", "sensor-off": "M3 12h4l3-8 4 16 3-8h4M3 3l18 18", "battery": "M3 6h16v12H3ZM22 10v4", "camera": "M3 6h5l2-3h4l2 3h5v15H3ZM16 13a4 4 0 1 1-8 0 4 4 0 0 1 8 0", "cpu": "M6 6h12v12H6ZM9 9h6v6H9ZM9 2v4m6-4v4M9 18v4m6-4v4M2 9h4m-4 6h4m12-6h4m-4 6h4", "shield-check": "M12 3l8 3v6c0 5-8 9-8 9s-8-4-8-9V6ZM8 12l3 3 5-6", "shield-question": "M12 3l8 3v6c0 5-8 9-8 9s-8-4-8-9V6ZM10 9a2 2 0 1 1 3 2l-1 1m0 3h.01", "monitor": "M3 3h18v14H3ZM12 17v4m-5 0h10", "plug": "M8 3v5m8-5v5M6 8h12v4a6 6 0 0 1-12 0ZM12 18v4", "plug-off": "M8 3v5m8-5v5M6 8h12v4a6 6 0 0 1-12 0ZM12 18v4M3 3l18 18", "clock": "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM12 6v6l4 2", "user": "M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0ZM4 21a8 8 0 0 1 16 0", "hash": "M9 3 7 21M17 3l-2 18M3 8h18M3 16h18", "check": "M5 12l4 4L19 6", "check-circle": "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM7 12l3 3 7-7", "x-circle": "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM8 8l8 8m0-8-8 8", "warning": "M12 3 2 21h20ZM12 9v5m0 3h.01", "minus-circle": "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM8 12h8", "minus": "M5 12h14", "copy": "M8 8h13v13H8ZM16 8V3H3v13h5", "crosshair": "M20 12a8 8 0 1 1-16 0 8 8 0 0 1 16 0ZM12 2v5m0 10v5M2 12h5m10 0h5", "gauge": "M4 19a10 10 0 1 1 16 0ZM12 14l5-6M7 8h.01M5 13h.01M12 5h.01", "info": "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM12 11v6m0-10h.01", "reset": "M3 10a9 9 0 1 1 2 9M3 4v6h6", "led": "M8 14V8a4 4 0 0 1 8 0v6ZM6 14h12M9 14v7m6-7v7M2 5l3 2m14 0 3-2", "resample": "M3 7h18l-4-4m4 4-4 4M21 17H3l4 4m-4-4 4-4", "wave": "M2 12c4-14 6 14 10 0s6 14 10 0", "sim": "M8 3h8v3H8ZM4 6h16v15H4ZM8 11h.01M16 11h.01M8 16h8", "dot": "M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0", "arrow": "M3 12h18m-6-6 6 6-6 6"};
  // Specialized person poses: local outline adaptations of the catalog's user symbol.
  iconPaths['person-still'] = 'M14 4a2 2 0 1 1-4 0 2 2 0 0 1 4 0M8 12V9h8v3M10 9v12m4-12v12';
  iconPaths['person-moving'] = 'M16 4a2 2 0 1 1-4 0 2 2 0 0 1 4 0M5 12l4-4 4 1 3 5h4M13 9l-3 7-5 5m5-5 6 5M3 7h3M2 16h3';
  function icon(name, label) { const n = document.createElementNS("http://www.w3.org/2000/svg", "svg"); n.setAttribute("viewBox", "0 0 24 24"); n.setAttribute("role", "img"); n.setAttribute("aria-label", label); svgNode("title", {}, n, label); svgNode("path", { d: iconPaths[name] }, n); return n; }
  const params = new URLSearchParams(location.search);
  const capture = params.get('capture') === '1';
  const replay = params.get('replay');
  const relativeClock = params.get('clock') === 'relative';
  let replayEnd = null, sessionStart = null;
  const themes = ['midnight', 'clinical', 'graphite', 'ocean'];
  let savedTheme;
  try { savedTheme = localStorage.getItem('monitor-bg'); } catch (_) { /* Storage may be disabled. */ }
  function applyTheme(theme, persist = false) {
    const selected = themes.includes(theme) ? theme : 'midnight';
    document.documentElement.dataset.bg = selected;
    $('backgroundTheme').value = selected;
    if (persist) {
      try { localStorage.setItem('monitor-bg', selected); } catch (_) { /* Picker still works. */ }
    }
  }
  applyTheme(themes.includes(params.get('bg')) ? params.get('bg') : savedTheme);
  function closeThemePicker() {
    $('themeOptions').hidden = true;
    $('themeToggle').setAttribute('aria-expanded', 'false');
  }
  $('themeToggle').addEventListener('click', () => {
    const open = $('themeOptions').hidden;
    $('themeOptions').hidden = !open;
    $('themeToggle').setAttribute('aria-expanded', String(open));
    if (open) $('backgroundTheme').focus();
  });
  $('backgroundTheme').addEventListener('change', () => {
    applyTheme($('backgroundTheme').value, true);
    closeThemePicker();
    $('themeToggle').focus();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !$('themeOptions').hidden) {
      closeThemePicker(); $('themeToggle').focus();
    }
  });
  document.addEventListener('click', event => {
    if (!event.target.closest('.theme-picker')) closeThemePicker();
  });
  document.body.classList.toggle('capture', capture);
  const state = { mode: null, t: null, stats: {}, history: [], logs: [], comparison: {}, matches: [], anchor: null, paused: null, online: false };
  let generation = 0, historyRequest = 0;
  const sourceName = mode => ({ simulation: 'SIM', serial: 'SERIAL', wifi: 'UDP', api: 'API' }[mode] || 'WAITING');
  const time = ms => new Date(ms).toLocaleTimeString('en-GB', { hour12: false });
  const clockTime = ms => relativeClock ? elapsedTime(ms, sessionStart) : time(ms);
  put('receiptHeading', relativeClock ? 'Elapsed' : 'Receipt');
  const fmt = (row, key) => fieldValid(row, key) ? key === 'temp_c' ? row[key].toFixed(1) : String(row[key]) : '—';
  const age = () => replayEnd != null ? Math.max(0, (replayEnd - (state.t?.timestamp_ms ?? replayEnd)) / 1000) : state.anchor == null || !state.t ? null : Math.max(0, (performance.now() - state.anchor) / 1000);
  const rows = () => state.history.filter(r => r.source === state.mode && r.patient === state.t?.patient);
  function setMode(mode) {
    if (!mode || mode === state.mode) return false;
    generation++; historyRequest++; sessionStart = null;
    Object.assign(state, { mode, t: null, stats: {}, history: [], comparison: {}, matches: [], anchor: null, paused: null });
    $('pause').textContent = 'Pause trends'; $('pause').setAttribute('aria-pressed', 'false');
    return true;
  }
  function mergeHistory(incoming) {
    const unique = new Map();
    for (const r of [...state.history, ...incoming]) {
      if (r.source === state.mode && finite(r.timestamp_ms)) {
        sessionStart = sessionStart == null ? r.timestamp_ms : Math.min(sessionStart, r.timestamp_ms);
        unique.set(`${r.patient}/${r.seq}/${r.timestamp_ms}`, r);
      }
    }
    state.history = [...unique.values()].sort((a, b) => a.timestamp_ms - b.timestamp_ms).slice(-200);
  }
  function receive(t) {
    if (t && t.source !== state.mode) return;
    const changed = t && t.timestamp_ms !== state.t?.timestamp_ms;
    state.t = t;
    if (!t) state.anchor = null;
    else { if (changed) state.anchor = performance.now(); mergeHistory([t]); }
  }
  function stats(s) {
    if (!s || s.source && s.source !== state.mode) return;
    if (state.stats.counters_since_ms && s.counters_since_ms !== state.stats.counters_since_ms) {
      state.history = []; state.matches = []; state.t = null; state.anchor = null; sessionStart = null;
      generation++; loadHistory();
    }
    state.stats = s;
    if (finite(s.data_age_sec)) state.anchor = performance.now() - s.data_age_sec * 1000;
    state.comparison = s.comparison_stats || state.comparison;
    rememberMatch(state.comparison.latest_match);
  }
  function rememberMatch(item) {
    if (!item?.rx || item.rx.source !== state.mode || item.rx.timestamp_ms < (state.stats.counters_since_ms || 0)) return;
    const key = item.rx.timestamp_ms;
    state.matches = state.matches.filter(r => r.rx.timestamp_ms !== key);
    state.matches.push(item); state.matches = state.matches.slice(-60);
  }
  function sync(data) {
    setMode(data.mode);
    stats(data.receiver_stats || data.stats);
    receive(Object.hasOwn(data, 'current_telemetry') ? data.current_telemetry : data.telemetry);
    // Server monotonic age takes precedence over browser receipt time on initial/reconnect state.
    if (finite(state.stats.data_age_sec)) state.anchor = performance.now() - state.stats.data_age_sec * 1000;
  }
  async function get(url) {
    const r = await fetch(url, { cache: 'no-store' });
    if (!r.ok) throw new Error(`Request failed (${r.status}). Retry when the server is available.`);
    return r.json();
  }
  async function loadHistory() {
    const g = generation, request = ++historyRequest;
    try {
      const incoming = await get('/api/history?source=' + encodeURIComponent(state.mode));
      if (g !== generation || request !== historyRequest) return;
      mergeHistory(Array.isArray(incoming) ? incoming : []); render();
    } catch (e) { log({ time: time(Date.now()), text: e.message, level: 'error' }); }
  }
  async function loadComparisons() {
    if (state.mode !== 'serial') return;
    const g = generation;
    try {
      const data = await get('/api/comparison');
      if (g !== generation) return;
      for (const item of data.history || []) rememberMatch(item);
      render();
    } catch (e) { log({ text: 'Comparison history: ' + e.message, level: 'error' }); }
  }
  function log(item) {
    state.logs.push({ ...item, timestamp_ms: item.timestamp_ms ?? Date.now() }); state.logs = state.logs.slice(-100);
    const list = $('log'); list.replaceChildren();
    for (const entry of state.logs.slice(-30).reverse()) {
      const li = document.createElement('li');
      // Strip emoji from backend log decoration; all log text remains inert text.
      li.textContent = `${relativeClock ? clockTime(entry.timestamp_ms) : entry.time || '—'} · ${entry.level || 'info'} · ${String(entry.text || '').replace(/[\p{Extended_Pictographic}\uFE0F\u200D]/gu, '')}`;
      list.append(li);
    }
  }
  function status(key, valid, text, tone) {
    const node = $('status-' + key);
    node.textContent = !valid ? 'No data' : text;
    node.className = 'chip ' + (!valid ? '' : tone || '');
    node.title = !valid ? 'Reading unavailable' : text === 'OK' ? 'Within demo limits; not clinical thresholds' : text === 'Stale' ? 'Stale snapshot; held value' : text;
    node.append(icon(!valid ? 'minus-circle' : tone === 'alarm' ? 'warning' : tone === 'warning' ? 'clock' : 'check-circle', node.title));
  }
  function pipeline(id, value, active, detail, simulated = false) {
    put('pipe-value-' + id, String(value).split(/\s+/).slice(0, 2).join(' '));
    $('pipe-' + id).className = 'pipeline-node ' + (simulated ? 'simulated' : active ? 'active' : 'unknown');
    const node = $('pipe-' + id);
    node.title = simulated ? detail + ' · SIMULATED; no live hardware' : detail + (active ? '' : ' · unknown or stale');
    const svg = node.children[0];
    if (svg?.children[0]) {
      svg.setAttribute('aria-label', node.title); svg.children[0].textContent = node.title;
      if (id === 'crc') svg.children[1].setAttribute('d', iconPaths[active ? 'shield-check' : 'shield-question']);
      if (id === 'decoder') svg.children[1].setAttribute('d', iconPaths[state.mode === 'simulation' ? 'sim' : /classic/i.test(state.t?.decoder || '') ? 'wave' : 'cpu']);
    }
    return active && !simulated;
  }
  function gauge(key, value, valid, limits) {
    const host = $('limit-' + key); host.replaceChildren();
    const demo = !limits ? [] : key === 'hr' ? [limits.hr_low, limits.hr_high] : key === 'spo2' ? [limits.spo2_low, 100] : [limits.temp_high_c];
    const ds = demo.filter(finite);
    host.title = ds.length ? 'Demo limits, not clinical thresholds: ' + (key === 'hr' ? ds.join('–') + ' bpm' : key === 'spo2' ? '≥' + ds[0] + '%' : '<' + ds[0] + ' °C; uncalibrated') : 'Demo limits unavailable';
    host.setAttribute('aria-label', host.title);
    const bounds = range([...ds, ...(valid ? [value] : [])], key === 'hr' ? 30 : key === 'spo2' ? 10 : 3);
    const x = v => 5 + (v - bounds[0]) / (bounds[1] - bounds[0]) * 190;
    const svg = svgNode('svg', { viewBox: '0 0 200 35', role: 'img', 'aria-label': host.title }, host);
    svgNode('title', {}, svg, host.title);
    svgNode('line', { x1: 5, x2: 195, y1: 8, y2: 8, class: 'gauge-track' }, svg);
    if (ds.length) {
      const low = key === 'temp_c' ? bounds[0] : ds[0], high = key === 'hr' || key === 'spo2' ? ds[1] : ds[0];
      svgNode('line', { x1: x(low), x2: x(high), y1: 8, y2: 8, class: 'gauge-band' }, svg);
      ds.forEach((v, i) => svgNode('text', { x: i ? 195 : 5, y: 31, 'text-anchor': i ? 'end' : 'start' }, svg, (key === 'temp_c' ? '<' : key === 'spo2' && !i ? '≥' : '') + v));
    } else svgNode('text', { x: 5, y: 31 }, svg, 'Limits unavailable');
    if (valid) {
      const d = `M ${x(value)} 1 V 16`;
      // A themed backing keeps the marker readable where it crosses the demo band.
      svgNode('path', { d, class: 'gauge-marker-halo' }, svg);
      svgNode('path', { d, class: 'gauge-marker' }, svg);
    }
  }

  function render() {
    const t = state.t, s = state.stats, limits = s.thresholds, a = age();
    const stale = a != null && (a >= 25 || s.stale === true);
    put('source', replay ? sourceName(state.mode) + ' · recorded' : sourceName(state.mode));
    put('freshness', replay && rows().length ? relativeClock ? 'recorded · ' + elapsedTime(replayEnd, sessionStart).slice(1) : time(rows()[0].timestamp_ms) + '–' + time(replayEnd) : !t ? 'WAITING' : stale ? 'STALE' : 'CURRENT');
    $('freshness').className = '' + (!t || stale ? 'warning' : 'ok');
    put('age', a == null ? '—' : `${a.toFixed(1)} s`);
    $('age').title = a == null ? 'No snapshot received' : 'Data age' + (stale ? '; held values' : '');
    put('transport', replay ? 'Frozen recording · read-only' : !state.online ? 'Stream disconnected · reconnecting' : state.mode === 'serial' ? s.serial_connected ? 'RX connected · event stream open' : 'RX disconnected · event stream open' : 'Event stream open');
    put('snapshot', t ? `#${t.patient}` : '—'); put('sequence', t ? `seq ${t.seq}` : '—');
    put('receipt', t ? clockTime(t.timestamp_ms) : '—');
    const current = !!t && !stale;
    const simulated = current && state.mode === 'simulation';
    const stages = [
      pipeline('tx', 'TX', current && state.comparison.tx_connected === true, 'Wearable transmitter connection', simulated),
      pipeline('link', `${s.link_bit_s ?? 5} bit/s`, current && state.mode !== 'simulation', 'Optical link rate; about 10 s/snapshot; simulation does not measure the optical link'),
      pipeline('camera', finite(s.acquisition_fps) ? fpsText(s.acquisition_fps) : '30 FPS', current && finite(s.acquisition_fps), finite(s.acquisition_fps) ? 'Reported acquisition FPS' : '30 FPS nominal acquisition; not reported', simulated),
      pipeline('resample', `${s.resampling_hz ?? 20} Hz`, current && finite(s.resampling_hz) && state.mode !== 'simulation', 'Resampling rate; nominal 20 Hz when not reported'),
      pipeline('decoder', t ? state.mode === 'simulation' ? 'simulated' : t.decoder ?? '—' : '—', current && !!t.decoder, 'Last decoder: ' + (t?.decoder ?? 'not reported')),
      pipeline('crc', 'CRC-8', current && crcVerified(t), crcDetail(t)),
      pipeline('monitor', 'RX', current, 'Received snapshot freshness')
    ];
    for (let i = 0; i < stages.length - 1; i++) {
      $('pipe-arrow-' + i).setAttribute('class', stages[i] && stages[i + 1] ? 'flowing' : '');
    }
    const imu = activity(t, state.comparison, state.matches);
    const imuHost = $('activity');
    const imuTooltip = 'On-board accelerometer: moving if ≥2 of last 10 one-second windows exceed 0.03 g SD; fall/seizure not detected';
    imuHost.replaceChildren(icon(imu.icon, imu.label));
    const label = document.createElement('strong'); label.textContent = imu.label;
    const detail = document.createElement('span'); detail.textContent = imu.detail + (stale ? ' · stale snapshot' : '');
    imuHost.append(label, detail); imuHost.title = imuTooltip;
    imuHost.className = 'sensor-off' + (imu.measured && !stale ? ' measured' : '');
    const activityHeading = $('activityHeading');
    const activityTitle = document.createElement('span'); activityTitle.textContent = 'Activity';
    activityHeading.replaceChildren(icon(imu.icon, imu.label), activityTitle);
    activityHeading.title = imuTooltip;
    put('captureFootnote', 'Research prototype · demo limits, not clinical thresholds · temperature uncalibrated' + imu.footnote);
    for (const key of ['hr', 'spo2', 'temp_c']) {
      const valid = fieldValid(t, key), v = t?.[key];
      put('value-' + key, fmt(t, key));
      const hasLimits = limits && (key === 'hr' ? finite(limits.hr_low) && finite(limits.hr_high) : finite(limits[key === 'spo2' ? 'spo2_low' : 'temp_high_c']));
      let cue = 'No limits', tone = '';
      if (hasLimits) {
        cue = key === 'hr' ? v < limits.hr_low ? 'Low' : v > limits.hr_high ? 'High' : 'OK' : key === 'spo2' ? v < limits.spo2_low ? 'Low' : 'OK' : v >= limits.temp_high_c ? 'High' : 'OK';
        tone = cue === 'OK' ? 'ok' : 'alarm';
      }
      gauge(key, v, valid, limits);
      status(key, valid, stale ? 'Stale' : cue, stale ? 'warning' : tone);
    }
    put('value-battery', fmt(t, 'battery'));
    const battery = $('batterySegments'); battery.replaceChildren();
    battery.title = 'TX battery level, 0–7; no demo threshold';
    const batterySvg = svgNode('svg', { viewBox: '0 0 130 30', role: 'img', 'aria-label': 'Battery level ' + fmt(t, 'battery') + ' of 7' }, battery);
    svgNode('title', {}, batterySvg, battery.title);
    svgNode('rect', { x: 2, y: 2, width: 118, height: 26, rx: 3 }, batterySvg);
    svgNode('path', { d: 'M125 10v10' }, batterySvg);
    for (let i = 0; i < 7; i++) svgNode('rect', { x: 7 + i * 16, y: 7, width: 11, height: 16, class: fieldValid(t, 'battery') && i < t.battery ? 'filled' : 'empty' }, batterySvg);
    status('battery', fieldValid(t, 'battery'), stale ? 'Stale' : 'Received', stale ? 'warning' : '');
    put('decoder', state.mode === 'simulation' && t ? 'simulated' : t?.decoder ?? '—');
    $('decoder').title = $('decoder').textContent;
    put('crc', t?.crc_status === 'verified-by-host' ? 'Host verified' : t?.crc_status ?? '—');
    $('crcBadge').title = crcDetail(t);
    $('crcBadge').replaceChildren(icon(crcVerified(t) ? 'shield-check' : 'shield-question', crcDetail(t)), $('crc'));
    put('valid', s.valid ?? '—'); put('rejected', s.rejected_crc ?? '—'); put('duplicates', s.duplicate_seq ?? '—');
    put('counterScope', relativeClock ? 'Dashboard counters · recorded session' : `Dashboard counters · ${sourceName(state.mode)}${s.counters_since_ms ? ' since ' + time(s.counters_since_ms) : ''}`);
    put('fps', fpsText(s.acquisition_fps));
    put('roi', Array.isArray(s.roi_box) && s.roi_box.length === 4 ? `${s.roi_box[0]},${s.roi_box[1]} · ${s.roi_box[2]}×${s.roi_box[3]}` : s.roi_box == null ? 'Not reported' : JSON.stringify(s.roi_box));
    put('deviceValid', s.receiver_valid_packets ?? 'Not reported');
    const c = state.comparison;
    const matched = c.rx_bit_exact ?? c.total_matched ?? 0;
    const unmatched = c.rx_unmatched ?? Math.max(0, (c.total_compared ?? 0) - matched);
    const compared = matched + unmatched;
    const falseAccepts = `${unmatched} RX frames not transmitted (CRC-8 false accept)`;
    put('match', !c.tx_connected ? 'TX not connected' : `${matched}/${compared}`);
    const ring = $('matchRing'); ring.replaceChildren();
    const basis = (c.match_basis ?? c.latest_match?.match_basis) === 'frame_hex' ? 'bit-exact frame match (frame_hex)' : 'field match (fields)';
    ring.title = c.tx_connected ? `${matched} matched / ${compared} compared · ${basis} · ${falseAccepts} · ${c.rx_unchecked ?? 0} unchecked` : 'TX not connected; comparison unavailable';
    if (c.tx_connected) {
      const svg = svgNode('svg', { viewBox: '0 0 64 64', role: 'img', 'aria-label': ring.title }, ring);
      svgNode('title', {}, svg, ring.title);
      svgNode('circle', { cx: 32, cy: 32, r: 26, class: 'ring-track' }, svg);
      svgNode('circle', { cx: 32, cy: 32, r: 26, class: 'ring-progress', 'stroke-dasharray': `${compared ? Math.min(1, matched / compared) * 163.36 : 0} 163.36`, transform: 'rotate(-90 32 32)' }, svg);
      svgNode('circle', { cx: 32, cy: 32, r: 26, class: 'ring-unmatched', 'stroke-dasharray': `${compared ? unmatched / compared * 163.36 : 0} 163.36`, 'stroke-dashoffset': compared ? -matched / compared * 163.36 : 0, transform: 'rotate(-90 32 32)' }, svg);
      svgNode('path', { d: compared && matched === compared ? 'M22 32l7 7 14-15' : 'M24 32h16', class: 'ring-check' }, svg);
    } else ring.append(icon('plug-off', ring.title));
    const last = [...state.matches].sort((a, b) => b.rx.timestamp_ms - a.rx.timestamp_ms).find(r => r.is_match === true);
    put('lastMatch', `Last matched seq: ${last?.seq ?? '—'}${c.tx_connected && !compared ? ' · awaiting pairs' : ''}`);
    put('unmatchedNote', c.tx_connected ? falseAccepts : 'TX coverage unavailable');
    $('unmatchedNote').title = ring.title;
    renderFeed(); renderTrends();
    document.body.dataset.ready = 'true';
  }
  function txMatch(row) {
    if (typeof row.tx_match === 'boolean') return row.tx_match;
    if (Object.hasOwn(row, 'tx_match')) return null;
    const pair = state.matches.find(m => m.rx.timestamp_ms === row.timestamp_ms && m.patient === row.patient);
    return pair?.match_basis === 'frame_hex' ? pair.is_match : null;
  }
  function renderFeed() {
    const body = $('packets'); body.replaceChildren();
    const visible = rows().slice(capture ? -2 : -12).reverse();
    put('feedMeta', `${visible.length} of ${rows().length} retained · ${sourceName(state.mode)}`);
    for (const row of visible) {
      const comparison = state.matches.find(m => m.rx.timestamp_ms === row.timestamp_ms && m.patient === row.patient);
      const values = [clockTime(row.timestamp_ms), row.seq, fmt(row, 'hr'), fmt(row, 'spo2'), fmt(row, 'temp_c'), row.tx_alert_bit ?? row.alert ?? '—', row.source === 'simulation' ? 'simulated' : row.decoder ?? '—', row.crc_status ?? 'unverified', comparison ? `${comparison.match_basis === 'frame_hex' ? 'bit-exact frame' : 'field'} ${comparison.is_match ? 'match' : 'mismatch'}` : 'N/A'];
      const labels = [relativeClock ? 'Elapsed' : 'Receipt', 'Seq', 'HR bpm', 'SpO2 %', 'Temp °C', 'Alert bit', 'Decoder', 'CRC', 'TX match'];
      const tr = document.createElement('tr');
      values.forEach((v, i) => {
        if (capture && (i === 5 || i === 7)) return;
        const td = document.createElement('td'); td.dataset.label = labels[i];
        if (i === 6) {
          const simulated = row.source === 'simulation', npu = !simulated && /npu/i.test(row.decoder || '');
          td.textContent = simulated ? 'SIM' : npu ? 'NPU' : /classic/i.test(row.decoder || '') ? 'Classic' : row.decoder ?? '—';
          const decoderLabel = document.createElement('span'); decoderLabel.textContent = td.textContent;
          td.replaceChildren(icon(simulated ? 'sim' : npu ? 'cpu' : 'wave', 'Decoder: ' + v), decoderLabel); td.title = String(v);
        } else if (i === 7) { td.append(icon(crcVerified(row) ? 'shield-check' : 'shield-question', crcDetail(row))); td.title = crcDetail(row); }
        else if (i === 8) {
          const match = txMatch(row), label = match === true ? 'bit-exact' : match === false ? 'not transmitted' : 'unchecked';
          td.append(icon(match === true ? 'check' : match === false ? 'x-circle' : 'minus', label));
          const text = document.createElement('span'); text.textContent = label; td.append(text);
          td.title = match === false ? 'RX frame not transmitted (CRC-8 false accept)' : label;
          if (match === false) td.className = 'unmatched';
        }
        else td.textContent = String(v);
        tr.append(td);
      }); body.append(tr);
    }
    if (!visible.length) { const tr = document.createElement('tr'), td = document.createElement('td'); td.colSpan = capture ? 7 : 9; td.textContent = 'Waiting for accepted snapshots.'; tr.append(td); body.append(tr); }
  }
  function svgNode(tag, attrs, parent, text) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, val] of Object.entries(attrs)) node.setAttribute(key, val);
    if (text != null) node.textContent = text;
    parent.append(node); return node;
  }
  function renderTrends() {
    const container = $('plots'), width = Math.max(240, container.clientWidth), height = container.clientHeight;
    const frozen = state.paused;
    const all = frozen ? frozen.rows : rows();
    const end = frozen ? frozen.end : replayEnd ?? Date.now();
    const start = end - Math.min(600000, Math.max(60000, all.length ? end - all[0].timestamp_ms : 60000));
    const data = all.filter(r => r.timestamp_ms >= start && r.timestamp_ms <= end);
    const limits = frozen ? frozen.limits : state.stats.thresholds;
    const defs = [ ['hr', 'Heart rate · bpm', '--ok', 20, limits ? [limits.hr_low, limits.hr_high] : []], ['spo2', 'SpO2 · %', '--accent', 6, limits ? [limits.spo2_low] : []], ['temp_c', 'Temperature · °C, uncalibrated', '--warning', 1.5, limits ? [limits.temp_high_c] : []] ];
    put('trendMeta', `${frozen ? 'PAUSED · ' : ''}${data.length} snapshots · ${Math.round((end - start) / 1000)} s receipt window · patient #${state.t?.patient ?? '—'}`);
    container.replaceChildren();
    const svg = svgNode('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': 'Heart rate, SpO2 and uncalibrated temperature by receipt time. Values are also available in the packet feed.' }, container);
    const rowHeight = (height - 20) / 3, left = 44, right = width - 62;
    const x = ms => left + (ms - start) / (end - start) * (right - left);
    defs.forEach(([key, title, color, minSpan, demo], i) => {
      const top = i * rowHeight + (capture ? 18 : 23), bottom = (i + 1) * rowHeight - (capture ? 4 : 10);
      const valid = data.filter(r => fieldValid(r, key));
      const rawBounds = range(valid.map(r => r[key]), minSpan);
      const extrema = valid.length ? [Math.min(...valid.map(r => r[key])), Math.max(...valid.map(r => r[key]))] : rawBounds;
      const tickBounds = key === 'hr' && extrema[1] - extrema[0] >= minSpan ? extrema : rawBounds;
      const rough = (tickBounds[1] - tickBounds[0]) / 4;
      const magnitude = 10 ** Math.floor(Math.log10(rough));
      const step = [1, 2, 5, 10].find(n => n * magnitude >= rough) * magnitude;
      const bounds = [Math.floor(tickBounds[0] / step) * step, Math.ceil(tickBounds[1] / step) * step];
      const y = value => bottom - (value - bounds[0]) / (bounds[1] - bounds[0]) * (bottom - top);
      const group = svgNode('g', { style: `--series:var(${color})` }, svg);
      svgNode('text', { x: left, y: top - 8, class: 'series-title' }, group, title);
      plotTicks(bounds, step, y).forEach(v => {
        const py = y(v);
        svgNode('line', { x1: left, x2: right, y1: py, y2: py, class: 'grid' }, group);
        svgNode('text', { x: left - 8, y: py + 4, 'text-anchor': 'end', class: 'axis-tick' }, group, key === 'temp_c' ? v.toFixed(1) : Math.round(v));
      });
      const clip = svgNode('clipPath', { id: 'clip-' + key }, svgNode('defs', {}, group));
      svgNode('rect', { x: left, y: top, width: right - left, height: bottom - top }, clip);
      const trace = svgNode('g', { 'clip-path': `url(#clip-${key})` }, group);
      let lastLabelY = top - 10;
      const demoValues = demo.filter(finite).sort((a, b) => b - a);
      for (const [index, v] of demoValues.entries()) {
        if (v >= bounds[0] && v <= bounds[1]) {
          svgNode('line', { x1: left, x2: right, y1: y(v), y2: y(v), class: 'demo' }, trace);
        }
        const labelY = Math.max(lastLabelY + 14, Math.min(bottom + 4 - (demoValues.length - index - 1) * 14, Math.max(top + 4, y(v) + 4)));
        svgNode('text', { x: width - 6, y: labelY, 'text-anchor': 'end', class: 'edge-limit' }, group, (v > bounds[1] ? '↑' : v < bounds[0] ? '↓' : '') + v);
        lastLabelY = labelY;
      }
      svgNode('path', { d: stepped(data, key, x, y, 1500 * (state.stats.snapshot_period_s || 10)), class: 'trace', 'data-series': key }, trace);
      for (const row of valid) {
        const unmatched = txMatch(row) === false;
        const dot = svgNode('circle', { cx: x(row.timestamp_ms), cy: y(row[key]), r: unmatched ? 4 : 2.5, class: unmatched ? 'point unmatched' : 'point', tabindex: unmatched ? 0 : -1 }, trace);
        svgNode('title', {}, dot, `${time(row.timestamp_ms)} · ${row[key]}${unmatched ? ' · RX frame not transmitted (CRC-8 false accept)' : ''}`);
      }
      if (!valid.length) svgNode('text', { x: (left + right) / 2, y: (top + bottom) / 2 + 4, 'text-anchor': 'middle' }, group, 'No available readings');
    });
    renderActivity(data, start, end);
    [start, (start + end) / 2, end].forEach((ms, i) => svgNode('text', { x: x(ms), y: height - 2, 'text-anchor': ['start', 'middle', 'end'][i] }, svg, clockTime(ms)));
  }
  function renderActivity(data, start, end) {
    const host = $('activityStrip'); host.replaceChildren();
    const legend = document.createElement('p'); legend.textContent = 'Received activity · 0 Still · 1 Moving · 2/3 unvalidated'; host.append(legend);
    const width = Math.max(240, $('plots').clientWidth), left = 44, right = width - 62;
    const x = ms => left + (ms - start) / (end - start) * (right - left);
    const laneStep = capture ? 10.5 : 12, stripHeight = capture ? 32 : 36;
    const svg = svgNode('svg', { viewBox: `0 0 ${width} ${stripHeight}`, role: 'img', 'aria-label': 'Received activity bits: 0 Still, 1 Moving, 2/3 unvalidated. Gaps mean no received snapshot; TX validity is shown in the Activity card.' }, host);
    svgNode('title', {}, svg, 'Received IMU fields, not inferred activity between missing snapshots');
    const labels = ['0 Still', '1 Moving', '2/3 unvalidated'];
    labels.forEach((_, i) => svgNode('line', { x1: left, x2: right, y1: 5 + i * laneStep, y2: 5 + i * laneStep, class: 'activity-lane' }, svg));
    labels.forEach((label, i) => svgNode('text', { x: left - 8, y: 9 + i * laneStep, 'text-anchor': 'end' }, svg, ['0', '1', '2/3'][i]));
    const period = (state.stats.snapshot_period_s || 10) * 1000;
    data.forEach((row, i) => {
      if (!Number.isInteger(row.imu) || row.imu < 0 || row.imu > 3) return;
      const lane = Math.min(row.imu, 2), next = data[i + 1];
      const until = Math.min(end, row.timestamp_ms + period, next?.timestamp_ms ?? end);
      const mark = svgNode('rect', { x: x(row.timestamp_ms), y: 2 + lane * laneStep, width: Math.max(2, x(until) - x(row.timestamp_ms)), height: capture ? 5 : 7, class: txMatch(row) === false ? 'unmatched' : 'activity-mark' }, svg);
      svgNode('title', {}, mark, `${time(row.timestamp_ms)} · ${labels[lane]}${txMatch(row) === false ? ' · not transmitted' : ''} · received field; validity may be unknown`);
    });
  }
  async function post(url, body) {
    if (replay) throw new Error('Replay is read-only');
    const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await res.json();
    if (!res.ok || data.status === 'error') throw new Error(data.message || `Request failed (${res.status}). Check the connection and retry.`);
    return data;
  }
  async function action(control, operation) {
    control.disabled = true; put('controlMessage', 'Working…');
    try { const message = await operation(); put('controlMessage', message); }
    catch (e) { put('controlMessage', e.message); }
    finally { control.disabled = false; }
  }
  $('mode').addEventListener('change', () => action($('mode'), async () => {
    try { await post('/api/mode', { mode: $('mode').value }); sync(await get('/api/status')); await loadHistory(); render(); return 'Source updated. Waiting for fresh snapshots.'; }
    finally { $('mode').value = state.mode; }
  }));
  $('controls').addEventListener('submit', e => {
    e.preventDefault(); action(e.submitter, async () => {
      const data = await post('/api/connect_dual', { rx_port: $('rxPort').value.trim(), tx_port: $('txPort').value.trim() });
      sync(await get('/api/status')); $('mode').value = state.mode; await loadHistory(); render();
      return data.message || 'Connection request completed.';
    });
  });
  $('reset').addEventListener('click', () => action($('reset'), async () => {
    await post('/api/reset', {}); historyRequest++; generation++; state.history = []; state.logs = []; sessionStart = null;
    state.paused = null; $('pause').textContent = 'Pause trends'; $('pause').setAttribute('aria-pressed', 'false');
    $('log').replaceChildren(); render(); return 'History and receiver log cleared.';
  }));
  $('pause').addEventListener('click', () => {
    state.paused = state.paused ? null : { rows: rows().map(r => ({ ...r })), end: replayEnd ?? Date.now(), limits: state.stats.thresholds };
    $('pause').textContent = state.paused ? 'Resume trends' : 'Pause trends'; $('pause').setAttribute('aria-pressed', String(!!state.paused)); renderTrends();
  });
  const toggle = document.createElement('button'); toggle.className = 'mobile-controls-toggle'; toggle.textContent = 'Source & connection controls'; toggle.setAttribute('aria-expanded', 'false'); toggle.setAttribute('aria-controls', 'controls');
  if (!capture) $('controls').before(toggle);
  toggle.addEventListener('click', () => { const open = $('controls').classList.toggle('mobile-open'); toggle.setAttribute('aria-expanded', String(open)); });
  window.addEventListener('resize', renderTrends);
  if (replay) {
    document.body.classList.add('replay');
    get('/api/replay/' + encodeURIComponent(replay)).then(data => {
      replayEnd = Math.max(...data.history.map(r => r.timestamp_ms));
      sync(data.status);
      sessionStart = Math.min(...data.history.map(r => r.timestamp_ms));
      state.history = [];
      mergeHistory(data.accounting.history);
      state.t = rows().find(r => r.timestamp_ms === data.status.current_telemetry?.timestamp_ms) ?? state.history[state.history.length - 1];
      state.comparison = { ...data.comparison.stats, ...data.accounting.stats };
      for (const item of data.comparison.history) rememberMatch(item);
      render();
    }).catch(e => { put('transport', e.message); });
    render();
    return;
  }
  const stream = new EventSource('/api/stream');
  function listen(event, callback) { stream.addEventListener(event, e => { try { callback(JSON.parse(e.data)); render(); } catch (error) { log({ text: error.message, level: 'error' }); } }); }
  stream.onopen = () => { state.online = true; render(); };
  stream.onerror = () => { state.online = false; render(); };
  listen('init', data => { sync(data); $('mode').value = state.mode; loadHistory(); loadComparisons(); });
  listen('mode', data => { if (setMode(data.mode)) loadHistory(); $('mode').value = state.mode; });
  listen('telemetry', receive); listen('receiver_status', stats);
  listen('comparison_stats', data => { state.comparison = data; rememberMatch(data.latest_match); loadHistory(); });
  listen('comparison', rememberMatch); listen('log', log);
  window.addEventListener('resize', renderTrends);
  setInterval(render, 1000);
  // SSE init is authoritative; status is a fallback if that initial event is delayed.
  const initialGeneration = generation;
  get('/api/status').then(data => { if (generation === initialGeneration && !state.mode) { sync(data); $('mode').value = state.mode; loadHistory(); render(); } }).catch(e => log({ text: e.message, level: 'error' }));
  render();
})();
