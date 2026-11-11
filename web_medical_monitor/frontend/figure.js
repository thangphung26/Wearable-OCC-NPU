/** Publication figure: discrete receipts, source-scoped state, and a frozen capture. */
(function () {
  'use strict';
  const params = new URLSearchParams(window.location.search);
  const isSingle = params.get('size') === 'single';
  const isFreeze = ['1', 'true'].includes(params.get('freeze'));
  if (isSingle) document.body.classList.add('single-column');
  const IMU_LABELS = ['still', 'moving', 'fall flag', 'protocol flag'];
  const appState = {
    mode: null,
    currentTelemetry: null,
    receiverStats: null,
    history: [],
    thresholds: null,
    alarms: {},
    ageAnchor: null,
    captureTime: Date.now()
  };
  let generation = 0, historyRequest = 0, receiptVersion = 0;
  const el = id => document.getElementById(id);
  const put = (id, value) => {
    const node = el(id);
    if (node) node.textContent = String(value);
  };
  const period = () => appState.receiverStats?.snapshot_period_s > 0 ? appState.receiverStats.snapshot_period_s : 10;

  function isFieldValid(row, key) {
    if (!row || !Number.isFinite(row[key]) || row[`${key}_valid`] === false) return false;
    if (key === 'hr' || key === 'spo2') return row[key] > 0 && row.ppg_valid !== false;
    if (key === 'temp_c') return row[key] > 0 && row.temp_valid !== false;
    return true;
  }

  function age() {
    if (!appState.currentTelemetry || appState.ageAnchor == null) return null;
    return Math.max(0, ((isFreeze ? appState.captureTime : Date.now()) - appState.ageAnchor) / 1000);
  }

  function held() {
    return age() == null || age() >= 25 || appState.receiverStats?.stale ||
      (appState.mode === 'serial' && !appState.receiverStats?.serial_connected);
  }

  function setMode(mode) {
    if (!mode || mode === appState.mode) return false;
    generation++;
    historyRequest++;
    Object.assign(appState, {
      mode,
      currentTelemetry: null,
      receiverStats: null,
      history: [],
      thresholds: null,
      alarms: {},
      ageAnchor: null
    });
    return true;
  }

  function stats(data) {
    if (!data || (data.source && data.source !== appState.mode)) return;
    appState.receiverStats = data;
    appState.thresholds = data.thresholds || null;
    if (Number.isFinite(data.data_age_sec)) {
      appState.ageAnchor = (isFreeze ? appState.captureTime : Date.now()) - data.data_age_sec * 1000;
    }
  }

  function receive(t) {
    if (t && t.source !== appState.mode) return;
    receiptVersion++;
    appState.currentTelemetry = t;
    if (!t) {
      appState.ageAnchor = null;
      return;
    }
    appState.ageAnchor = Date.now();
    mergeHistory([t]);
  }

  function mergeHistory(rows) {
    const unique = new Map();
    for (const r of [...rows, ...appState.history]) {
      if (r.source === appState.mode && Number.isFinite(r.timestamp_ms)) {
        unique.set(`${r.patient}/${r.timestamp_ms}/${r.seq}`, r);
      }
    }
    appState.history = [...unique.values()].sort((a, b) => a.timestamp_ms - b.timestamp_ms).slice(-250);
  }

  async function loadHistory() {
    const g = generation, request = ++historyRequest, mode = appState.mode;
    try {
      const res = await fetch(`/api/history?source=${encodeURIComponent(mode)}`);
      if (!res.ok) return;
      const data = await res.json();
      if (g !== generation || request !== historyRequest) return;
      mergeHistory(Array.isArray(data) ? data : data.history || []);
      updateUI();
    } catch (error) {
      console.warn('History unavailable', error);
    }
  }

  function sync(data) {
    const changed = setMode(data.mode);
    if (Object.hasOwn(data, 'current_telemetry') || Object.hasOwn(data, 'telemetry')) {
      receive(Object.hasOwn(data, 'current_telemetry') ? data.current_telemetry : data.telemetry);
    }
    stats(data.receiver_stats || data.stats);
    appState.alarms = data.alarms || appState.alarms;
    if (data.alert?.patient != null) appState.alarms[data.alert.patient] = data.alert;
    return changed;
  }

  function initLiveStreaming() {
    const sse = new EventSource('/api/stream');
    const listen = (name, fn) => sse.addEventListener(name, e => {
      try {
        fn(JSON.parse(e.data));
        updateUI();
      } catch (error) {
        console.warn(name, error);
      }
    });
    listen('init', data => {
      sync(data);
      loadHistory();
    });
    listen('mode', data => {
      if (setMode(data.mode)) loadHistory();
    });
    listen('telemetry', receive);
    listen('receiver_status', stats);
    listen('alert', data => {
      if (data.patient != null) appState.alarms[data.patient] = data;
    });
    setInterval(() => {
      updateUI();
    }, 1000);
    setInterval(async () => {
      const g = generation, version = receiptVersion;
      try {
        const res = await fetch('/api/status');
        if (!res.ok) return;
        const data = await res.json();
        if (g !== generation || version !== receiptVersion) return;
        if (sync(data)) loadHistory();
        updateUI();
      } catch (error) {
        console.warn('Status unavailable', error);
      }
    }, 2000);
  }

  function setStatusCue(id, valid, inRange) {
    const node = el(id);
    if (!node) return;
    const unavailable = !valid || !appState.thresholds;
    node.className = `vital-status status-${unavailable || held() ? 'neutral' : inRange ? 'normal' : 'alert'}`;
    node.textContent = unavailable ? 'unavailable' : held() ? 'stale · held value' :
      inRange ? '● in demo range' : 'outside demo range';
  }

  function updateUI() {
    const t = appState.currentTelemetry, s = appState.receiverStats, limits = appState.thresholds;
    const a = age();
    put('sourceBadge', appState.mode === 'simulation' ? (isSingle ? 'SIMULATION' : 'SOURCE: SIMULATION') : `SOURCE: ${appState.mode ? appState.mode.toUpperCase() : 'WAITING'}`);
    put('snapshotMeta', t ? `${isSingle ? 'Pt' : 'Patient'} #${t.patient} · Seq ${t.seq} · Age ${a == null ? '--' : a.toFixed(1)} s` : 'Waiting for snapshot');
    
    const ratesP1 = `${appState.mode === 'simulation' ? 'camera n/a (simulation)' : Number.isFinite(s?.acquisition_fps) ? `${s.acquisition_fps} FPS acquisition` : '30 FPS acquisition (nominal)'} · 20 Hz resampling`;
    const ratesP2 = `5 bit/s · ~${period()} s/snapshot${appState.mode === 'simulation' ? ' (simulated)' : ''}`;
    put('ratesSpec', isSingle ? `${ratesP1}\n${ratesP2}` : `${ratesP1} · ${ratesP2}`);

    const specs = [
      ['hr', 'Hr', limits && t?.hr >= limits.hr_low && t?.hr <= limits.hr_high],
      ['spo2', 'Spo2', limits && t?.spo2 >= limits.spo2_low],
      ['temp_c', 'Temp', limits && t?.temp_c < limits.temp_high_c],
      ['imu', 'Imu', limits && !limits.imu_warning_states.includes(t?.imu)]
    ];
    for (const [key, suffix, good] of specs) {
      const valid = isFieldValid(t, key);
      put(`val${suffix}`, !valid ? '--' : key === 'imu' ? `${t.imu} · ${IMU_LABELS[t.imu]}` : key === 'temp_c' ? t[key].toFixed(1) : t[key]);
      setStatusCue(`status${suffix}`, valid, good);
    }
    put('limitHr', limits ? `Demo: ${limits.hr_low}–${limits.hr_high} bpm` : 'Limits unavailable');
    put('limitSpo2', limits ? `Demo: ≥${limits.spo2_low}%` : 'Limits unavailable');
    put('limitTemp', limits ? `Demo: <${limits.temp_high_c}°C` : 'Limits unavailable');
    put('limitImu', limits ? `${limits.imu_warning_states.join('–')}: demo warning` : 'Limits unavailable');
    put('valBattery', isFieldValid(t, 'battery') ? `Level ${t.battery} / 7` : '--');
    put('statusBattery', !isFieldValid(t, 'battery') ? 'unavailable' : held() ? 'stale · held value' : 'encoded 0–7');
    put('valAlertBit', t?.tx_alert_bit ?? '--');
    put('countValid', s?.valid ?? s?.valid_packets ?? '--');
    put('countRejected', s?.rejected_crc ?? '--');
    put('countDuplicate', s?.duplicate_seq ?? s?.duplicate_packets ?? '--');
    put('valAge', a == null ? '-- s' : `${a.toFixed(1)} s`);
    put('valDecoder', t?.decoder || '--');
    put('valCrcStatus', appState.mode !== 'simulation' && t?.crc_status === 'valid' ? 'Valid' :
      appState.mode !== 'simulation' && t?.crc_status === 'rejected' ? 'Rejected' : 'Unverified');
    put('linkStateMeta', !t ? 'WAITING' : held() ? 'STALE / HELD' : 'CURRENT');
    if (el('linkStateMeta')) {
      el('linkStateMeta').className = `panel-meta link-state-${!t ? 'waiting' : held() ? 'stale' : 'active'}`;
    }
    const episode = appState.alarms[t?.patient];
    if (el('episodeState')) {
      put('episodeState', episode?.active ? `Patient #${t.patient} · Ep #${episode.episode_id}` : '—');
    }
    renderTrends();
  }

  function createSvgElement(tag, attrs, parent) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    if (parent) parent.appendChild(node);
    return node;
  }

  function vitalRange(vals, minimumSpan, fallback, steps) {
    const dMin = vals.length ? Math.min(...vals) : fallback;
    const dMax = vals.length ? Math.max(...vals) : fallback;
    const dataSpan = dMax - dMin;
    const step = steps.find(value => Math.max(minimumSpan, dataSpan * 1.16) / 4 <= value) ?? steps[steps.length - 1];
    const padding = Math.max(dataSpan * 0.08, step);
    const span = Math.max(minimumSpan, dataSpan + 2 * padding);
    const mid = (dMin + dMax) / 2;
    const yMin = Math.floor((mid - span / 2) / step) * step;
    const yMax = Math.ceil((mid + span / 2) / step) * step;
    return { yMin, yMax, step };
  }

  function renderTrends() {
    const svg = el('trendSvg'), container = el('trendPlotContainer');
    if (!svg || !container) return;
    const rect = container.getBoundingClientRect();
    const W = (rect && rect.width > 0) ? Math.round(rect.width) : (isSingle ? 484 : 586);
    const H = (rect && rect.height > 0) ? Math.round(rect.height) : (isSingle ? 408 : 640);
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.replaceChildren();

    const t = appState.currentTelemetry, limits = appState.thresholds;
    const end = isFreeze ? t?.timestamp_ms ?? appState.captureTime : Date.now();
    const start = end - 600000;
    const rows = appState.history.filter(r => t && r.patient === t.patient && r.source === t.source &&
      r.source === appState.mode && r.timestamp_ms >= start && r.timestamp_ms <= end);

    // Subtitle of (b): received count and the ACTUAL span from first to last receipt in view
    let spanText = '0.0 min';
    if (rows.length > 1) {
      const spanMin = Math.max(0.1, (rows[rows.length - 1].timestamp_ms - rows[0].timestamp_ms) / 60000);
      spanText = `${spanMin.toFixed(1)} min`;
    } else if (rows.length === 1) {
      spanText = '0.1 min';
    }
    put('trendSummaryMeta', `${rows.length} snapshots · ${spanText}`);

    const left = isSingle ? 48 : 54;
    const right = W - 10;
    const plotW = Math.max(10, right - left);
    const x = ms => left + ((ms - start) / 600000) * plotW;

    const timeAxisH = 22;
    const stripH = 36;
    const stripHeaderH = isSingle ? 14 : 16;
    const headerH = isSingle ? 16 : 20;
    const gap = isSingle ? 4 : 8;

    const fixedH = timeAxisH + 2 * (stripH + stripHeaderH) + 3 * headerH + 5 * gap;
    const availVitalH = Math.max(3 * (isSingle ? 70 : 110), H - fixedH);
    const vitalPlotH = Math.max(isSingle ? 70 : 110, Math.floor(availVitalH / 3));

    // Vital plots configuration
    const vitalDefs = [
      { key: 'hr', title: 'Heart Rate (bpm)', ink: '#84344e', minimumSpan: 20, fallback: 75, steps: [5, 10] },
      { key: 'spo2', title: isSingle ? 'SpO2 (%)' : 'Oxygen Saturation SpO2 (%)', ink: '#185f91', minimumSpan: 6, fallback: 98, steps: [2, 5] },
      { key: 'temp_c', title: isSingle ? 'Body Temp. (°C)' : 'Body Temperature (°C)', ink: '#805019', minimumSpan: 1.5, fallback: 37.5, steps: [0.5] }
    ];

    let curY = 0;

    vitalDefs.forEach(spec => {
      const validRows = rows.filter(r => isFieldValid(r, spec.key));
      const vals = validRows.map(r => r[spec.key]);

      const { yMin, yMax, step } = vitalRange(vals, spec.minimumSpan, spec.fallback, spec.steps);
      const ticks = [];
      // Thin labels only for unusually wide ranges; keep every tick on the nice-step grid.
      const tickStride = Math.max(1, Math.ceil(((yMax - yMin) / step - 1) / Math.max(1, Math.floor(vitalPlotH / 22))));
      for (let tick = yMin + step; tick < yMax; tick += step * tickStride) ticks.push(tick);
      let demoNote = '';
      const guidelines = [];

      if (spec.key === 'hr') {
        const limHigh = limits ? limits.hr_high : 140;
        const limLow = limits ? limits.hr_low : 50;
        const notes = [];
        if (limHigh > yMax) notes.push(`demo limit ${limHigh} ↑`);
        else if (limHigh >= yMin) guidelines.push(limHigh);
        if (limLow < yMin) notes.push(`demo limit ${limLow} ↓`);
        else if (limLow <= yMax) guidelines.push(limLow);
        demoNote = notes.length === 2 ? (isSingle ? `demo ${limLow} ↓, ${limHigh} ↑` : `demo limits: ${limLow} ↓, ${limHigh} ↑`) :
          notes.length === 1 ? notes[0] : limits ? `Demo ${limLow} / ${limHigh}` : 'Limits unavailable';

      } else if (spec.key === 'spo2') {
        const thresh = limits ? limits.spo2_low : 94;
        if (thresh < yMin) demoNote = `demo limit ${thresh}% ↓`;
        else if (thresh > yMax) demoNote = `demo limit ${thresh}% ↑`;
        else {
          guidelines.push(thresh);
          demoNote = `Demo limit (≥${thresh}%)`;
        }

      } else if (spec.key === 'temp_c') {
        const thresh = limits ? limits.temp_high_c : 39.0;
        if (thresh > yMax) demoNote = `demo limit ${thresh.toFixed(1)}°C ↑`;
        else if (thresh < yMin) demoNote = `demo limit ${thresh.toFixed(1)}°C ↓`;
        else {
          guidelines.push(thresh);
          demoNote = `Demo limit (<${thresh.toFixed(1)}°C)`;
        }
      }

      // 1. Reserved header row above plot (never touches y-ticks or traces)
      const tTitle = createSvgElement('text', {
        x: left,
        y: curY + (isSingle ? 12 : 15),
        'font-size': 15,
        'font-weight': 700,
        fill: spec.ink
      }, svg);
      tTitle.textContent = spec.title;

      const tNote = createSvgElement('text', {
        x: right,
        y: curY + (isSingle ? 12 : 15),
        'text-anchor': 'end',
        'font-size': 15,
        fill: '#4c5c69'
      }, svg);
      tNote.textContent = demoNote;

      // 2. Plot box
      const topY = curY + headerH;
      const botY = topY + vitalPlotH;
      createSvgElement('rect', {
        x: left,
        y: topY,
        width: plotW,
        height: vitalPlotH,
        fill: '#f8fafc',
        stroke: '#a8bac8',
        'stroke-width': 1
      }, svg);

      const yMap = val => botY - ((val - yMin) / (yMax - yMin)) * vitalPlotH;

      // Guidelines inside plot
      for (const gVal of guidelines) {
        const gy = yMap(gVal);
        createSvgElement('line', {
          x1: left,
          y1: gy,
          x2: right,
          y2: gy,
          stroke: '#cbd5e1',
          'stroke-dasharray': '4 3',
          'stroke-width': 1
        }, svg);
      }

      // Ticks & grid lines
      for (const tick of ticks) {
        const ty = yMap(tick);
        createSvgElement('line', {
          x1: left,
          y1: ty,
          x2: right,
          y2: ty,
          stroke: '#e2e8f0',
          'stroke-width': 0.8
        }, svg);

        const tickText = createSvgElement('text', {
          x: left - 6,
          y: ty + 5,
          'text-anchor': 'end',
          'font-size': 15,
          fill: '#4c5c69',
          'font-weight': 500
        }, svg);
        tickText.textContent = tick % 1 === 0 ? String(tick) : tick.toFixed(1);
      }

      // Clip the stepped trace and markers to this vital plot in both figure sizes.
      const clipId = 'trend-clip-' + spec.key;
      const defs = createSvgElement('defs', {}, svg);
      const clip = createSvgElement('clipPath', { id: clipId, clipPathUnits: 'userSpaceOnUse' }, defs);
      createSvgElement('rect', { x: left, y: topY, width: plotW, height: vitalPlotH }, clip);
      const traceGroup = createSvgElement('g', { 'clip-path': 'url(#' + clipId + ')' }, svg);

      // Stepped path
      let prev = null, pathD = '';
      const maxGap = appState.receiverStats?.snapshot_period_s > 0 ? 1500 * appState.receiverStats.snapshot_period_s : 25000;
      for (const r of rows) {
        if (!isFieldValid(r, spec.key)) { prev = null; continue; }
        const px = x(r.timestamp_ms);
        const py = yMap(r[spec.key]);
        if (!prev || r.timestamp_ms - prev.timestamp_ms > maxGap) {
          pathD += ` M ${px.toFixed(1)} ${py.toFixed(1)}`;
        } else {
          pathD += ` H ${px.toFixed(1)} V ${py.toFixed(1)}`;
        }
        prev = r;
      }

      createSvgElement('path', {
        d: pathD.trim(),
        fill: 'none',
        stroke: spec.ink,
        'stroke-width': 1.5,
        'stroke-linecap': 'round',
        'stroke-linejoin': 'round',
        'data-series': spec.key
      }, traceGroup);

      // Points: small 3-px dots when points in view <= 60
      if (validRows.length <= 60) {
        for (const r of validRows) {
          createSvgElement('circle', {
            cx: x(r.timestamp_ms).toFixed(1),
            cy: yMap(r[spec.key]).toFixed(1),
            r: 1.5,
            fill: spec.ink
          }, traceGroup);
        }
      }

      curY = botY + gap;
    });

    // -------------------------------------------------------------------------
    // Subplot 4: Activity Strip (~36 px height)
    // -------------------------------------------------------------------------
    const actTitle = createSvgElement('text', {
      x: left,
      y: curY + (isSingle ? 11 : 12),
      'font-size': 15,
      'font-weight': 700,
      fill: '#4d527c'
    }, svg);
    actTitle.textContent = 'Activity';

    const actLegend = createSvgElement('text', {
      x: right,
      y: curY + (isSingle ? 11 : 12),
      'text-anchor': 'end',
      'font-size': 15,
      fill: '#4c5c69'
    }, svg);
    actLegend.textContent = '0: Still · 1: Move · 2–3: demo warning';

    const actTopY = curY + stripHeaderH;
    const actBotY = actTopY + stripH;

    createSvgElement('rect', {
      x: left,
      y: actTopY,
      width: plotW,
      height: stripH,
      fill: '#f8fafc',
      stroke: '#a8bac8',
      'stroke-width': 1
    }, svg);

    const laneH = stripH / 4; // 9px
    // Lane 3 (top) pale alert tint
    createSvgElement('rect', {
      x: left,
      y: actTopY,
      width: plotW,
      height: laneH,
      fill: 'rgba(162, 37, 33, 0.08)'
    }, svg);
    // Lane 2 pale amber tint
    createSvgElement('rect', {
      x: left,
      y: actTopY + laneH,
      width: plotW,
      height: laneH,
      fill: 'rgba(128, 80, 25, 0.08)'
    }, svg);

    // Dividers between lanes
    for (let l = 1; l <= 3; l++) {
      createSvgElement('line', {
        x1: left,
        y1: actTopY + l * laneH,
        x2: right,
        y2: actTopY + l * laneH,
        stroke: '#e2e8f0',
        'stroke-width': 0.8
      }, svg);
    }

    const imuLaneY = state => actBotY - (state + 0.5) * laneH;
    let imuPrev = null, imuPath = '';
    const maxGap = appState.receiverStats?.snapshot_period_s > 0 ? 1500 * appState.receiverStats.snapshot_period_s : 25000;
    for (const r of rows) {
      if (!isFieldValid(r, 'imu')) { imuPrev = null; continue; }
      const px = x(r.timestamp_ms);
      const state = Math.max(0, Math.min(3, r.imu));
      const py = imuLaneY(state);
      if (!imuPrev || r.timestamp_ms - imuPrev.timestamp_ms > maxGap) {
        imuPath += ` M ${px.toFixed(1)} ${py.toFixed(1)}`;
      } else {
        imuPath += ` H ${px.toFixed(1)} V ${py.toFixed(1)}`;
      }
      imuPrev = r;
    }

    createSvgElement('path', {
      d: imuPath.trim(),
      fill: 'none',
      stroke: '#4d527c',
      'stroke-width': 1.5,
      'stroke-linecap': 'round',
      'stroke-linejoin': 'round',
      'data-series': 'imu'
    }, svg);

    // Thin marks in lanes 0..3
    for (const r of rows) {
      if (!isFieldValid(r, 'imu')) continue;
      const px = x(r.timestamp_ms);
      const state = Math.max(0, Math.min(3, r.imu));
      const py = imuLaneY(state);
      const markColor = state >= 3 ? '#84344e' : state === 2 ? '#805019' : state === 1 ? '#185f91' : '#4d527c';
      createSvgElement('line', {
        x1: px.toFixed(1),
        y1: (py - 3.5).toFixed(1),
        x2: px.toFixed(1),
        y2: (py + 3.5).toFixed(1),
        stroke: markColor,
        'stroke-width': 1.2
      }, svg);
    }

    curY = actBotY + gap;

    // -------------------------------------------------------------------------
    // Subplot 5: Transmitter Battery Strip (~36 px height)
    // -------------------------------------------------------------------------
    const battTitle = createSvgElement('text', {
      x: left,
      y: curY + (isSingle ? 11 : 12),
      'font-size': 15,
      'font-weight': 700,
      fill: '#176c63'
    }, svg);
    battTitle.textContent = 'TX Battery';

    const battLegend = createSvgElement('text', {
      x: right,
      y: curY + (isSingle ? 11 : 12),
      'text-anchor': 'end',
      'font-size': 15,
      fill: '#4c5c69'
    }, svg);
    battLegend.textContent = 'Level 0–7';

    const battTopY = curY + stripHeaderH;
    const battBotY = battTopY + stripH;

    createSvgElement('rect', {
      x: left,
      y: battTopY,
      width: plotW,
      height: stripH,
      fill: '#f8fafc',
      stroke: '#a8bac8',
      'stroke-width': 1
    }, svg);

    // Faint guideline for Level 7
    createSvgElement('line', {
      x1: left,
      y1: battTopY + 4,
      x2: right,
      y2: battTopY + 4,
      stroke: '#e2e8f0',
      'stroke-dasharray': '3 3',
      'stroke-width': 0.8
    }, svg);

    const battY = lvl => battBotY - 5 - (lvl / 7) * (stripH - 10);
    let battPrev = null, battPath = '';
    for (const r of rows) {
      if (!isFieldValid(r, 'battery')) { battPrev = null; continue; }
      const px = x(r.timestamp_ms);
      const lvl = Math.max(0, Math.min(7, r.battery));
      const py = battY(lvl);
      if (!battPrev || r.timestamp_ms - battPrev.timestamp_ms > maxGap) {
        battPath += ` M ${px.toFixed(1)} ${py.toFixed(1)}`;
      } else {
        battPath += ` H ${px.toFixed(1)} V ${py.toFixed(1)}`;
      }
      battPrev = r;
    }

    createSvgElement('path', {
      d: battPath.trim(),
      fill: 'none',
      stroke: '#176c63',
      'stroke-width': 1.5,
      'stroke-linecap': 'round',
      'stroke-linejoin': 'round',
      'data-series': 'battery'
    }, svg);

    const validBatt = rows.filter(r => isFieldValid(r, 'battery'));
    if (validBatt.length <= 60) {
      for (const r of validBatt) {
        const px = x(r.timestamp_ms);
        const py = battY(Math.max(0, Math.min(7, r.battery)));
        createSvgElement('circle', {
          cx: px.toFixed(1),
          cy: py.toFixed(1),
          r: 1.5,
          fill: '#176c63'
        }, svg);
      }
    }

    curY = battBotY + gap;

    // -------------------------------------------------------------------------
    // Time Axis Ticks at bottom
    // -------------------------------------------------------------------------
    const timeAxisY = curY + 2;
    createSvgElement('line', {
      x1: left,
      y1: timeAxisY,
      x2: right,
      y2: timeAxisY,
      stroke: '#a8bac8',
      'stroke-width': 1
    }, svg);

    const timeSteps = isSingle ? [10, 5, 0] : [10, 8, 6, 4, 2, 0];
    for (const min of timeSteps) {
      const tx = x(end - min * 60000);
      createSvgElement('line', {
        x1: tx,
        y1: timeAxisY,
        x2: tx,
        y2: timeAxisY + 4,
        stroke: '#a8bac8',
        'stroke-width': 1
      }, svg);

      const label = min === 0 ? 'Now' : `-${min}m`;
      const anchor = min === 10 ? 'start' : min === 0 ? 'end' : 'middle';
      const tText = createSvgElement('text', {
        x: tx,
        y: timeAxisY + 16,
        'text-anchor': anchor,
        'font-size': 15,
        fill: '#4c5c69',
        'font-weight': 500
      }, svg);
      tText.textContent = label;
    }
  }

  async function loadData() {
    try {
      const res = await fetch('/api/status');
      if (res.ok) {
        const data = await res.json();
        appState.captureTime = Date.now();
        sync(data);
      }
      await loadHistory();
    } catch (error) {
      console.warn('Initial status unavailable', error);
    }
    updateUI();
    document.body.setAttribute('data-ready', 'true');
    if (!isFreeze) initLiveStreaming();
  }

  window.addEventListener('resize', renderTrends);
  loadData();
})();
