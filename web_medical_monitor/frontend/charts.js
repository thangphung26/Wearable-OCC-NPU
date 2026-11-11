/* Receipt-time trends of actual snapshots; no invented intermediate samples. */
'use strict';

function fieldAvailable(row, key) {
  const value = row?.[key];
  if (!Number.isFinite(value)) return false;
  if (key === 'hr' || key === 'spo2') return value > 0 && row.ppg_valid !== false && row[`${key}_valid`] !== false;
  if (key === 'temp_c') return value > 0 && row.temp_valid !== false;
  return row[`${key}_valid`] !== false;
}

function formatTime(timestamp) {
  return new Date(timestamp).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'});
}

// One ruled column per receipt; no connecting physiological waveform.
function observationWindow(rows, now, period = 10) {
  const valid = rows.filter(row => !row.gap).slice(-30);
  if (!valid.length) return {rows: [], start: now - 300000, end: now};
  const first = valid[0].timestamp_ms, last = valid[valid.length - 1].timestamp_ms;
  const pad = period * 500;
  return {
    rows: valid,
    start: Math.min(first - pad, last - 29 * period * 1000),
    end: Math.max(last + pad, now)
  };
}

// Demo-band context plus padding; received outliers always expand the scale.
function observationSpecs(t) {
  return [
    {key: 'hr', label: 'HR', unit: 'bpm', min: (t?.hr_low ?? 50) - 5, max: (t?.hr_high ?? 140) + 5, low: t?.hr_low, high: t?.hr_high, ink: '--hr'},
    {key: 'spo2', label: 'SpO2', unit: '%', min: (t?.spo2_low ?? 94) - 1, max: 100, low: t?.spo2_low, ink: '--spo2'},
    {key: 'temp_c', label: 'Temp', unit: '°C', min: 36, max: (t?.temp_high_c ?? 39) + 0.5, high: t?.temp_high_c, ink: '--temp'},
    {key: 'imu', label: 'IMU state', unit: '0–3 · encoded', min: 0, max: 3, ink: '--imu'},
    {key: 'battery', label: 'Battery', unit: '0–7 · encoded', min: 0, max: 7, ink: '--battery'}
  ];
}

function observationScale(spec, rows) {
  const values = rows.filter(row => fieldAvailable(row, spec.key)).map(row => row[spec.key]);
  return {lo: Math.min(spec.min, ...values), hi: Math.max(spec.max, ...values)};
}

class VitalTrendChart {
  constructor(id) {
    this.canvas = document.getElementById(id);
    if (!this.canvas) return;
    this.ctx = this.canvas.getContext('2d');
    this.data = [];
    this.snapshotPeriodS = 10;
    this.thresholds = null;
    this.episode = null;
    this.active = false;
    this.historyLoading = false;
    this.observer = new ResizeObserver(() => this.draw());
    this.observer.observe(this.canvas.parentElement);
  }
  updateData(rows) {
    this.data = rows.filter(row => Number.isFinite(row.timestamp_ms)).slice(-360);
    this.draw();
  }
  draw() {
    if (!this.canvas) return;
    const rect = this.canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const ratio = window.devicePixelRatio || 1;
    this.canvas.width = Math.round(rect.width * ratio);
    this.canvas.height = Math.round(rect.height * ratio);
    const c = this.ctx;
    c.setTransform(ratio, 0, 0, ratio, 0, 0);
    const css = getComputedStyle(document.documentElement);
    const color = name => css.getPropertyValue(name).trim();
    const w = rect.width, h = rect.height;

    c.fillStyle = color('--canvas-bg') || '#0b0f16';
    c.fillRect(0, 0, w, h);

    const left = 152, right = w - 26, top = 38, bottom = h - 36;
    const availH = bottom - top;
    const imuH = 34;
    const battH = 32;
    const vitalH = Math.max(50, (availH - imuH - battH) / 3);

    const windowData = observationWindow(this.data, Date.now(), this.snapshotPeriodS);
    const {rows, start, end} = windowData;
    const x = time => left + (time - start) / Math.max(1, end - start) * (right - left);
    const line = (x1, y1, x2, y2, paint = '--grid') => {
      c.strokeStyle = color(paint) || '#1e2634';
      c.lineWidth = 1;
      c.beginPath();
      c.moveTo(Math.round(x1) + 0.5, Math.round(y1) + 0.5);
      c.lineTo(Math.round(x2) + 0.5, Math.round(y2) + 0.5);
      c.stroke();
    };
    const text = (value, xx, yy, paint = '--muted', size = 12, align = 'left') => {
      c.fillStyle = color(paint) || '#94a3b8';
      c.font = `${size}px system-ui, -apple-system, sans-serif`;
      c.textAlign = align;
      c.textBaseline = 'middle';
      c.fillText(String(value), xx, yy);
    };

    const t = this.thresholds;
    const specs = observationSpecs(t);
    const latest = rows[rows.length - 1], previous = rows[rows.length - 2];

    const laneLayouts = [
      { y0: top, y1: top + vitalH, hLane: vitalH },
      { y0: top + vitalH, y1: top + 2 * vitalH, hLane: vitalH },
      { y0: top + 2 * vitalH, y1: top + 3 * vitalH, hLane: vitalH },
      { y0: top + 3 * vitalH, y1: top + 3 * vitalH + imuH, hLane: imuH },
      { y0: top + 3 * vitalH + imuH, y1: bottom, hLane: battH }
    ];

    specs.forEach((s, i) => {
      const {y0, y1, hLane} = laneLayouts[i];
      const validRows = rows.filter(r => fieldAvailable(r, s.key));
      const vals = validRows.map(r => r[s.key]);

      let lo, hi, ticks = [], limitNote = '';
      if (s.key === 'hr') {
        const dMin = vals.length ? Math.min(...vals) : 75;
        const dMax = vals.length ? Math.max(...vals) : 75;
        const span = Math.max(20, Math.ceil((dMax - dMin + 4) / 5) * 5);
        const mid = Math.round((dMin + dMax) / 2);
        lo = Math.floor((mid - span / 2) / 5) * 5;
        hi = lo + span;
        if (dMin < lo) { lo = Math.floor(dMin / 5) * 5; hi = lo + span; }
        if (dMax > hi) { hi = Math.ceil(dMax / 5) * 5; lo = hi - span; }
        ticks = [lo, lo + Math.round(span / 2), hi];
        const notes = [];
        const limHigh = t ? t.hr_high : 140, limLow = t ? t.hr_low : 50;
        if (limHigh > hi) notes.push(`limit ${limHigh}↑`);
        if (limLow < lo) notes.push(`limit ${limLow}↓`);
        limitNote = notes.join(' · ');
      } else if (s.key === 'spo2') {
        const dMin = vals.length ? Math.min(...vals) : 98;
        const dMax = vals.length ? Math.max(...vals) : 98;
        const span = Math.max(6, Math.ceil((dMax - dMin + 2) / 2) * 2);
        hi = Math.min(100, Math.ceil(Math.max(100, dMax) / 2) * 2);
        lo = hi - span;
        if (dMin < lo) { lo = Math.floor(dMin / 2) * 2; hi = lo + span; }
        ticks = [lo, lo + Math.round(span / 2), hi];
        const thresh = t ? t.spo2_low : 94;
        if (thresh < lo) limitNote = `limit ≥${thresh}↓`;
        else if (thresh > hi) limitNote = `limit ≥${thresh}↑`;
      } else if (s.key === 'temp_c') {
        const dMin = vals.length ? Math.min(...vals) : 37.0;
        const dMax = vals.length ? Math.max(...vals) : 37.0;
        const span = Math.max(1.5, Math.ceil((dMax - dMin + 0.4) / 0.5) * 0.5);
        const mid = (dMin + dMax) / 2;
        lo = Math.floor((mid - span / 2) * 2) / 2;
        hi = Number((lo + span).toFixed(1));
        if (dMin < lo) { lo = Math.floor(dMin * 2) / 2; hi = Number((lo + span).toFixed(1)); }
        if (dMax > hi) { hi = Math.ceil(dMax * 2) / 2; lo = Number((hi - span).toFixed(1)); }
        ticks = [lo, Number(((lo + hi) / 2).toFixed(1)), hi];
        const thresh = t ? t.temp_high_c : 39.0;
        if (thresh > hi) limitNote = `limit <${thresh.toFixed(1)}↑`;
        else if (thresh < lo) limitNote = `limit <${thresh.toFixed(1)}↓`;
      } else if (s.key === 'imu') {
        lo = 0; hi = 3;
        ticks = [0, 1, 2, 3];
      } else {
        lo = 0; hi = 7;
        ticks = [0, 2, 4, 7];
      }

      const y = v => {
        if (s.key === 'imu') return y1 - (v + 0.5) * hLane / 4;
        if (s.key === 'battery') return y1 - 4 - (v / 7) * (hLane - 8);
        return y1 - 10 - ((v - lo) / Math.max(0.001, hi - lo)) * (hLane - 20);
      };

      if (t && i < 3) {
        const goodLo = Math.max(lo, Math.min(hi, s.low ?? lo));
        const goodHi = Math.min(hi, Math.max(lo, s.high ?? hi));
        if (goodHi > goodLo) {
          c.fillStyle = color('--band-good');
          c.fillRect(left, y(goodHi), right - left, y(goodLo) - y(goodHi));
        }
      }

      if (s.key === 'imu') {
        for (let state = 0; state < 4; state++) {
          const upper = y1 - (state + 1) * hLane / 4;
          if ((t?.imu_warning_states || [2, 3]).includes(state)) {
            c.fillStyle = color('--band-low');
            c.fillRect(left, upper, right - left, hLane / 4);
          }
          line(left, upper, right, upper);
          text(state, left - 9, y(state), '--muted', 11, 'right');
        }
      } else {
        for (const tick of ticks) {
          line(left, y(tick), right, y(tick));
          text(Number(tick.toFixed(2)), left - 9, y(tick), '--muted', 11, 'right');
        }
      }

      if (latest) {
        const xx = x(latest.timestamp_ms);
        const half = Math.min(12, (right - left) * this.snapshotPeriodS * 500 / (end - start));
        c.fillStyle = color('--newest');
        c.fillRect(xx - half, y0, half * 2, hLane);
      }

      // Small colored text labels (no saturated solid box)
      c.fillStyle = color(s.ink);
      c.font = 'bold 12px system-ui, -apple-system, sans-serif';
      c.textAlign = 'left';
      c.textBaseline = 'top';
      c.fillText(s.label, 14, y0 + 6);

      c.fillStyle = color('--muted');
      c.font = '11px system-ui, -apple-system, sans-serif';
      c.fillText(s.unit, 14, y0 + 20);

      if (limitNote) {
        c.fillStyle = color('--muted');
        c.font = '10px system-ui, -apple-system, sans-serif';
        c.fillText(limitNote, 14, y0 + 34);
      }

      // Draw faint vertical line for each receipt column
      for (const row of rows) line(x(row.timestamp_ms), y0, x(row.timestamp_ms), y1);
      line(0, y0, w, y0);

      // Connect points with clean stepped lines (stepped trends with visible gaps)
      let prevValid = null;
      for (const row of rows) {
        if (!fieldAvailable(row, s.key)) {
          prevValid = null;
          continue;
        }
        const currX = x(row.timestamp_ms);
        const currY = y(row[s.key]);
        if (prevValid) {
          const dt = (row.timestamp_ms - prevValid.time) / 1000;
          if (dt <= 15) {
            c.strokeStyle = color(s.ink);
            c.lineWidth = 1.2;
            c.beginPath();
            c.moveTo(Math.round(prevValid.x) + 0.5, Math.round(prevValid.y) + 0.5);
            c.lineTo(Math.round(currX) + 0.5, Math.round(prevValid.y) + 0.5);
            c.lineTo(Math.round(currX) + 0.5, Math.round(currY) + 0.5);
            c.stroke();
          }
        }
        prevValid = {x: currX, y: currY, time: row.timestamp_ms};
      }

      // Draw discrete observation points: only when points in view <= 60
      const drawDots = validRows.length <= 60;
      for (const row of rows) {
        if (!fieldAvailable(row, s.key)) {
          text('×', x(row.timestamp_ms), y0 + hLane / 2, '--muted', 14, 'center');
          continue;
        }
        const xx = x(row.timestamp_ms), yy = y(row[s.key]);
        const outside = t && (i < 3 ? ((s.low != null && row[s.key] < s.low) || (s.high != null && (s.key === 'temp_c' ? row[s.key] >= s.high : row[s.key] > s.high))) :
          s.key === 'imu' && (t.imu_warning_states || []).includes(row.imu));
        if (drawDots) {
          c.fillStyle = color(this.active && row === latest && outside ? '--warning' : s.ink);
          c.beginPath();
          c.arc(xx, yy, 2.5, 0, Math.PI * 2);
          c.fill();
        }
        if (row === latest && previous && fieldAvailable(previous, s.key) && row[s.key] !== previous[s.key]) {
          text('Δ', xx + 7, Math.max(y0 + 8, yy - 10), s.ink, 11, 'left');
        }
      }

      if (this.active && this.episode?.timestamp_ms >= start && this.episode.timestamp_ms <= end) {
        line(x(this.episode.timestamp_ms), y0, x(this.episode.timestamp_ms), y1, '--warning');
      }
    });

    line(0, bottom, w, bottom);
    line(left, top, left, bottom);
    line(right, top, right, bottom);
    text('Observation / scale', 12, 18, '--muted', 12);
    text(this.historyLoading ? 'Loading received history…' : rows.length ? `${rows.length} ${rows.length === 1 ? 'received snapshot' : 'received snapshots'} · true receipt time` : 'Waiting for first snapshot', left, 18, '--text', 13);
    if (this.active && this.episode?.timestamp_ms) {
      const within = this.episode.timestamp_ms >= start;
      text((within ? 'Alert onset ' : 'Alert onset before window ') + formatTime(this.episode.timestamp_ms), right, 18, '--warning', 12, 'right');
    }
    for (let i = 0; i <= 4; i++) {
      const time = start + (end - start) * i / 4;
      text(formatTime(time), x(time), h - 16, '--muted', 12, i === 0 ? 'left' : i === 4 ? 'right' : 'center');
    }
  }
}

class PacketTimelineChart {
  constructor(id) {
    this.canvas = document.getElementById(id);
    if (!this.canvas) return;
    this.ctx = this.canvas.getContext('2d');
    this.data = [];
    this.current = null;
    this.stats = null;
    this.observer = new ResizeObserver(() => this.draw());
    this.observer.observe(this.canvas.parentElement);
  }
  updateData(rows, current, stats) {
    this.data = rows.filter(r => !r.gap && Number.isFinite(r.timestamp_ms)).slice(-30);
    this.current = current;
    this.stats = stats;
    this.draw();
  }
  draw() {
    if (!this.canvas) return;
    const rect = this.canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const ratio = window.devicePixelRatio || 1;
    this.canvas.width = Math.round(rect.width * ratio);
    this.canvas.height = Math.round(rect.height * ratio);
    const c = this.ctx;
    c.setTransform(ratio, 0, 0, ratio, 0, 0);
    const css = getComputedStyle(document.documentElement);
    const color = name => css.getPropertyValue(name).trim();
    const w = rect.width, h = rect.height;

    c.fillStyle = color('--canvas-bg') || '#0b0f16';
    c.fillRect(0, 0, w, h);

    const now = Date.now();
    const windowMs = 120000; // 120-second rolling window
    const start = now - windowMs;
    const end = now;
    const left = 36, right = w - 30, top = 20, bottom = h - 28;
    const chartW = right - left;
    const x = t => left + (t - start) / windowMs * chartW;

    // Draw 10s nominal cadence grid
    c.strokeStyle = color('--grid') || '#1e2634';
    c.lineWidth = 1;
    for (let t = Math.ceil(start / 10000) * 10000; t <= end; t += 10000) {
      const xx = Math.round(x(t)) + 0.5;
      c.beginPath();
      c.moveTo(xx, top);
      c.lineTo(xx, bottom);
      c.stroke();
    }

    // Baseline ruler
    c.strokeStyle = color('--border') || '#2d3748';
    c.beginPath();
    c.moveTo(left, bottom);
    c.lineTo(right, bottom);
    c.stroke();

    // Time axis ticks
    c.fillStyle = color('--muted') || '#94a3b8';
    c.font = '11px system-ui, -apple-system, sans-serif';
    c.textAlign = 'center';
    c.textBaseline = 'top';
    [-120, -90, -60, -30, 0].forEach(sec => {
      const t = now + sec * 1000;
      const xx = x(t);
      if (xx >= left && xx <= right) {
        c.fillText(sec === 0 ? 'Now' : `${sec}s`, xx, bottom + 6);
      }
    });

    const rows = this.data;
    if (!rows.length) {
      c.fillStyle = color('--muted');
      c.font = '13px system-ui, -apple-system, sans-serif';
      c.textAlign = 'center';
      c.textBaseline = 'middle';
      c.fillText('Waiting for optical telemetry snapshots…', w / 2, (top + bottom) / 2);
      return;
    }

    // Draw packet ticks: thin fixed-width ticks (2 px) that never merge
    const tickW = 2;
    const tickH = bottom - top - 18;
    const tickPaint = color('--spo2') || '#00e5ff';
    for (let i = 0; i < rows.length; i++) {
      const row = rows[i];
      const xx = x(row.timestamp_ms);
      if (xx < left - tickW || xx > right + tickW) continue;
      const isLatest = (i === rows.length - 1);

      // Packet tick in uniform single color
      c.fillStyle = tickPaint;
      const bx = Math.round(xx - tickW / 2);
      const by = top + 10;
      c.fillRect(bx, by, tickW, tickH);

      // Label only the newest sequence number
      if (isLatest) {
        c.fillStyle = color('--text') || '#f1f5f9';
        c.font = '10px system-ui, -apple-system, sans-serif';
        c.textAlign = 'center';
        c.textBaseline = 'bottom';
        c.fillText(`S${row.seq}`, xx, by - 3);
      }
    }
  }
}
