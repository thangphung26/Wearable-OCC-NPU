'use strict';

const $ = id => document.getElementById(id);
const put = (id, value) => {
  const el = $(id);
  const text = String(value);
  if (el && el.textContent !== text) el.textContent = text;
};

// State 3 is shown as its encoded flag only; no diagnostic inference.
const IMU_LABELS = ['still', 'moving', 'fall flag', 'protocol flag'];

class MedicalMonitorApp {
  constructor() {
    this.mode = null;
    this.current = null;
    this.stats = {};
    this.alarms = {};
    this.history = [];
    this.historyEpoch = 0;
    this.historyRequest = 0;
    this.comparisons = [];
    this.connected = false;
    this.ageAnchor = null;
    this.soundEnabled = false;
    this.audio = null;
    this.lastTone = 0;
    this.prevValues = {};
    this.chart = new VitalTrendChart('trendCanvas');
    this.timeline = new PacketTimelineChart('packetTimelineCanvas');

    const params = new URLSearchParams(location.search);
    this.figure = params.get('view') === 'figure';
    document.documentElement.dataset.view = this.figure ? 'figure' : 'dashboard';

    // Figure view is always forced to light mode for publication print.
    // Dashboard defaults to dark mode unless ?theme=light is explicitly requested.
    this.theme(this.figure ? 'light' : params.get('theme') === 'light' ? 'light' : 'dark');
    this.bindControls();
    this.connect();
    this.refreshComparison().catch(() => {});
    this.render();
    setInterval(() => {
      this.renderFreshness();
      this.chart.draw();
      this.timeline?.draw();
      this.sound();
    }, 1000);
    window.addEventListener('beforeprint', () => {
      this.chart.draw();
      this.timeline?.draw();
    });
    window.addEventListener('afterprint', () => {
      this.chart.draw();
      this.timeline?.draw();
    });
    matchMedia('print').addEventListener('change', () => {
      this.chart.draw();
      this.timeline?.draw();
    });
  }

  theme(value) {
    document.documentElement.dataset.theme = value;
    const label = value === 'light' ? 'Dark theme' : 'Light theme';
    if ($('themeButtonText')) put('themeButtonText', label);
    else put('themeButton', label);
    this.chart.draw();
    this.timeline?.draw();
  }

  notice(message) {
    const host = document.querySelector?.('dialog[open]') || document.body;
    if (host) host.append($('actionNotice'));
    put('actionNotice', message);
    $('actionNotice').hidden = false;
  }

  async request(path, body) {
    const response = await fetch(path, body === undefined ? {} : {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || `Request failed (${response.status})`);
    return result;
  }

  action(id, callback) {
    $(id).addEventListener('click', async () => {
      $('actionNotice').hidden = true;
      $(id).disabled = true;
      try {
        await callback();
      } catch (error) {
        this.notice(error.message);
      } finally {
        $(id).disabled = false;
        this.updateControls();
        this.renderFreshness();
      }
    });
  }

  openDialog(id) {
    const dialog = $(id), opener = document.activeElement;
    if (dialog.open) return;
    dialog.showModal();
    dialog.querySelector('button,select,input,a')?.focus();
    dialog.addEventListener('close', () => opener?.focus(), {once: true});
  }

  bindControls() {
    document.querySelectorAll('dialog').forEach(dialog => {
      dialog.querySelector('[data-close]').addEventListener('click', () => dialog.close());
      dialog.addEventListener('keydown', event => {
        if (event.key === 'Escape') {
          event.preventDefault();
          dialog.close();
          return;
        }
        if (event.key !== 'Tab') return;
        const nodes = [...dialog.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),a[href]')].filter(el => el.getClientRects().length);
        const first = nodes[0], last = nodes[nodes.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      });
    });

    $('themeButton').onclick = () => this.theme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
    $('settingsButton').onclick = () => this.openDialog('settingsDialog');
    this.action('portsButton', async () => {
      this.openDialog('portsDialog');
      await this.refreshPorts();
    });
    this.action('benchmarkButton', async () => {
      this.openDialog('benchmarkDialog');
      put('benchmarkReference', 'Loading reference…');
      try {
        const ref = await this.request('/api/benchmark');
        put('benchmarkReference', `${ref.description}\n\n${ref.models.map(model => `${model.model}\nCPU ${model.cpu_ms} ms · NPU ${model.npu_ms} ms\nSource: ${model.source}`).join('\n\n')}`);
      } catch (error) {
        put('benchmarkReference', `Reference unavailable: ${error.message}`);
      }
    });
    this.action('verifyButton', async () => {
      this.openDialog('verifyDialog');
      await this.refreshComparison();
    });
    this.action('refreshPortsButton', () => this.refreshPorts());
    const portBody = () => ({port: $('rxPort').value, baudrate: Number($('baudRate').value)});
    const txBody = () => ({tx_port: $('txPort').value, tx_baud: Number($('baudRate').value)});
    this.action('connectRX', async () => {
      await this.request('/api/connect', portBody());
      await this.refreshPorts();
    });
    this.action('connectTX', async () => {
      await this.request('/api/connect_dual', txBody());
      await this.refreshPorts();
    });
    this.action('connectBoth', async () => {
      await this.request('/api/connect_dual', {...txBody(), rx_port: $('rxPort').value, rx_baud: Number($('baudRate').value)});
      await this.refreshPorts();
    });
    this.action('disconnectRX', async () => {
      await this.request('/api/disconnect', {});
      await this.refreshPorts();
    });
    this.action('disconnectTX', async () => {
      await this.request('/api/disconnect_tx', {});
      await this.refreshPorts();
    });
    this.action('soundButton', () => {
      this.soundEnabled = !this.soundEnabled;
      $('soundButton').setAttribute('aria-pressed', String(this.soundEnabled));
      put('soundButton', `Sound: ${this.soundEnabled ? 'on' : 'off'}`);
      if (this.soundEnabled) {
        const Audio = window.AudioContext || window.webkitAudioContext;
        if (Audio) {
          this.audio ||= new Audio();
          this.audio.resume().catch(() => {});
        }
      }
    });
    this.action('testButton', () => this.request('/api/alert', {action: 'trigger'}));
    this.action('clearTestButton', () => this.request('/api/alert', {action: 'clear'}));
    this.action('ackButton', async () => {
      const episode = this.alarms[this.current?.patient];
      if (!episode?.active) return;
      await this.request('/api/alert', {action: 'acknowledge', patient: this.current.patient, episode_id: episode.episode_id});
    });
    $('modeSelect').onchange = async event => {
      try {
        await this.request('/api/mode', {mode: event.target.value});
      } catch (error) {
        event.target.value = this.mode;
        this.notice(error.message);
      }
    };
    $('patientSelector').onchange = async event => {
      try {
        await this.request('/api/patient', {patient: Number(event.target.value), auto_cycle: false});
      } catch (error) {
        this.notice(error.message);
      }
    };
  }

  async refreshPorts() {
    const data = await this.request('/api/ports');
    $('portOptions').replaceChildren();
    for (const port of data.ports || []) {
      const option = document.createElement('option');
      option.value = port.device || port.port || String(port);
      $('portOptions').append(option);
    }
    put('portStatus', `RX: ${data.is_connected ? data.connected_port : 'disconnected'} · TX: ${data.tx_is_connected ? data.tx_connected_port : 'disconnected'}`);
  }

  connect() {
    this.events = new EventSource('/api/stream');
    const handlers = {
      init: data => {
        if (data.mode !== this.mode) this.setMode(data.mode);
        this.alarms = data.alarms || {};
        if (data.alert?.patient != null) this.alarms[data.alert.patient] = data.alert;
        this.stats = data.stats || {};
        if (data.telemetry) this.telemetry(data.telemetry);
        else {
          this.current = null;
          this.ageAnchor = null;
          this.prevValues = {};
          this.chart.updateData([]);
          this.timeline?.updateData([], null, this.stats);
          $('packetRows').replaceChildren();
          this.renderSparklines();
          this.updateFrameFields(null);
        }
        this.status(this.stats);
        this.loadHistory();
      },
      mode: data => {
        if (data.mode !== this.mode) this.setMode(data.mode);
        this.loadHistory();
      },
      telemetry: data => this.telemetry(data),
      receiver_status: data => this.status(data),
      alert: data => {
        if (data.patient != null) this.alarms[data.patient] = data;
        this.renderFreshness();
      },
      log: data => this.appendLog(data),
      comparison: data => {
        this.comparisons.push(data);
        this.comparisons = this.comparisons.slice(-100);
        this.renderComparisons();
      },
      comparison_stats: data => this.comparisonStats(data)
    };
    for (const [type, callback] of Object.entries(handlers)) {
      this.events.addEventListener(type, event => {
        try {
          callback(JSON.parse(event.data));
        } catch (error) {
          this.notice(`Could not read ${type}: ${error.message}`);
        }
      });
    }
    this.events.onopen = () => {
      this.connected = true;
      this.renderFreshness();
    };
    this.events.onerror = () => {
      if (this.connected && this.current) {
        this.history.push({timestamp_ms: Date.now(), patient: this.current.patient, source: this.current.source, gap: true});
        this.history = this.history.slice(-360);
        this.updateChart();
      }
      this.connected = false;
      this.renderFreshness();
    };
  }

  setMode(mode) {
    this.historyEpoch++;
    this.mode = mode;
    this.current = null;
    this.stats = {};
    this.ageAnchor = null;
    this.alarms = {};
    this.history = [];
    this.prevValues = {};
    this.chart.updateData([]);
    this.timeline?.updateData([], null, this.stats);
    $('packetRows').replaceChildren();
    this.renderSparklines();
    this.updateFrameFields(null);
    if (mode) $('modeSelect').value = mode;
    this.render();
  }

  updateControls() {
    const simulation = this.mode === 'simulation';
    $('patientSelector').disabled = !simulation;
    $('testButton').disabled = !simulation;
    $('clearTestButton').disabled = !simulation;
    $('synthetic').hidden = !simulation;
    $('sourceText').hidden = simulation;
  }

  async loadHistory() {
    const mode = this.mode, epoch = this.historyEpoch, request = ++this.historyRequest;
    this.chart.historyLoading = true;
    this.chart.draw();
    try {
      const rows = await this.request(`/api/history?source=${encodeURIComponent(mode || '')}`);
      if (this.mode !== mode || this.historyEpoch !== epoch || request !== this.historyRequest) return;
      if (!Array.isArray(rows)) throw new Error('Invalid history response');
      const unique = new Map();
      for (const row of [...rows, ...this.history]) {
        unique.set(`${row.source}/${row.patient}/${row.timestamp_ms}/${row.seq}/${!!row.gap}`, row);
      }
      this.history = [...unique.values()].sort((a, b) => a.timestamp_ms - b.timestamp_ms).slice(-360);
      this.chart.historyLoading = false;
      this.updateChart();
      this.renderPackets();
      this.renderSparklines();
    } catch (error) {
      if (this.historyEpoch === epoch && request === this.historyRequest) {
        this.chart.historyLoading = false;
        this.chart.draw();
        this.notice(`History unavailable: ${error.message}`);
      }
    }
  }

  triggerFeedback(data) {
    const prior = this.history.filter(row => !row.gap && row.patient === data.patient && row.source === data.source &&
      (row.timestamp_ms !== data.timestamp_ms || row.seq !== data.seq)).slice(-1)[0];
    for (const [id, key] of [['HR', 'hr'], ['SpO2', 'spo2'], ['Temp', 'temp_c'], ['IMU', 'imu'], ['Battery', 'battery']]) {
      const changed = !!prior && fieldAvailable(prior, key) && fieldAvailable(data, key) && prior[key] !== data[key];
      $(`card${id}`).classList.toggle('changed', changed);
    }
  }

  renderSparklines() {
    if (this.current) this.triggerFeedback(this.current);
    else {
      for (const id of ['HR', 'SpO2', 'Temp', 'IMU', 'Battery']) {
        $(`card${id}`).classList.toggle('changed', false);
      }
    }
  }

  telemetry(data) {
    if (data.source !== this.mode) return;
    this.current = data;
    this.ageAnchor = performance.now() - Math.max(0, Date.now() - data.timestamp_ms);
    if (!this.history.some(row => !row.gap && row.source === data.source && row.patient === data.patient && row.timestamp_ms === data.timestamp_ms && row.seq === data.seq)) {
      this.history.push(data);
    }
    this.history = this.history.slice(-360);
    if (this.mode === 'simulation') $('patientSelector').value = data.patient;
    this.render();
    this.updateChart();
    this.renderPackets();
    this.renderSparklines();
    this.triggerFeedback(data);
    this.updateFrameFields(data);
  }

  status(data) {
    this.stats = {...this.stats, ...data};
    if (this.current && Number.isFinite(data.data_age_sec)) {
      this.ageAnchor = performance.now() - data.data_age_sec * 1000;
    }
    this.render();
    if (data.comparison_stats) this.comparisonStats(data.comparison_stats);
  }

  age() {
    return this.current && this.ageAnchor !== null ? Math.max(0, (performance.now() - this.ageAnchor) / 1000) : null;
  }

  unavailable() {
    const data = this.current;
    return !data || !this.connected || this.age() >= 25 || (this.mode === 'serial' && !this.stats.serial_connected) || !['hr', 'spo2', 'temp_c'].every(key => fieldAvailable(data, key));
  }

  render() {
    this.updateControls();
    const d = this.current;
    put('patientName', d ? `Patient #${d.patient}` : 'Patient —');
    const sources = {simulation: 'simulation', serial: `UART ${this.stats.connected_port || ''}`.trim(), api: 'API', wifi: 'UDP'};
    put('sourceText', sources[this.mode] || '—');
    put('decoderText', d?.decoder || '—');
    put('crcText', `CRC ${d?.crc_status || '—'}`);
    const counts = `${this.stats.valid ?? 0} valid / ${this.stats.rejected_crc ?? 0} CRC-rejected / ${this.stats.duplicate_seq ?? 0} duplicate`;
    put('countersText', counts);
    put('counterScope', `since ${Number.isFinite(this.stats.counters_since_ms) ? formatTime(this.stats.counters_since_ms) : '—'} · ${sources[this.mode] || '—'}`);
    
    // Cadence calculation
    const recent = this.history.filter(r => !r.gap && r.patient === d?.patient && r.source === d?.source).slice(-8);
    let cadenceStr = '';
    let periodS = Number.isFinite(this.stats.snapshot_period_s) ? this.stats.snapshot_period_s : 2;
    if (recent.length >= 2) {
      const dts = [];
      for (let i = 1; i < recent.length; i++) {
        dts.push((recent[i].timestamp_ms - recent[i - 1].timestamp_ms) / 1000);
      }
      const mean = dts.reduce((a, b) => a + b, 0) / dts.length;
      periodS = mean;
      cadenceStr = `~${mean.toFixed(1)} s`;
    } else if (Number.isFinite(this.stats.snapshot_period_s)) {
      cadenceStr = `~${this.stats.snapshot_period_s.toFixed(1)} s`;
    } else {
      cadenceStr = '—';
    }

    this.chart.snapshotPeriodS = periodS;
    this.chart.thresholds = this.stats.thresholds || null;
    put('ratesText', `30 FPS acq (nominal) · ${this.mode === 'simulation' ? 'camera FPS n/a (simulation)' : Number.isFinite(this.stats.acquisition_fps) ? `camera FPS ${this.stats.acquisition_fps.toFixed(1)}` : 'camera FPS n/a'}`);
    put('snapshotCadence', `${this.stats.resampling_hz ?? 20} Hz resampling · ${this.stats.link_bit_s ?? 5} bit/s · ${cadenceStr} cadence`);
    put('periodText', `Period ${cadenceStr} · STALE at 25 s`);
    put('seqText', `Seq ${d?.seq ?? '—'}`);
    put('trendContext', d ? `Patient #${d.patient} · ${sources[this.mode]} · wall-clock receipt time` : 'Selected patient & source · wall-clock receipt time');
    const t = this.stats.thresholds;
    put('rangeHR', t ? `Demo ${t.hr_low}–${t.hr_high}` : 'Demo —');
    put('rangeSpO2', t ? `Demo ≥${t.spo2_low}` : 'Demo —');
    put('rangeTemp', t ? `Demo <${t.temp_high_c}` : 'Demo —');
    put('rangeIMU', t ? `Warn ${t.imu_warning_states.join('–')}` : 'Warn —');
    put('imuDescription', d && fieldAvailable(d, 'imu') ? `IMU state ${d.imu} — ${IMU_LABELS[d.imu] || 'unknown'}` : 'Protocol state —');

    // Update Cadence Badges
    put('timelineCadenceBadge', `Cadence: ${cadenceStr}`);
    put('timelineSubtext', `5 bit/s Manchester OOK · ${cadenceStr} packet cadence · 48-bit CRC frame`);

    this.updateFrameFields(d);
    this.renderFreshness();
    this.chart.draw();
    this.timeline?.draw();
  }

  updateFrameFields(d) {
    if (!d) {
      ['framePatient', 'frameHR', 'frameSpO2', 'frameTemp', 'frameIMU', 'frameBattery', 'frameAlert', 'frameSeq', 'frameCRC'].forEach(id => put(id, '--'));
      return;
    }
    const alert = (d.tx_alert_bit ?? d.alert);
    put('framePatient', d.patient != null ? `#${d.patient}` : '--');
    put('frameHR', fieldAvailable(d, 'hr') ? `${d.hr} bpm` : '--');
    put('frameSpO2', fieldAvailable(d, 'spo2') ? `${d.spo2}%` : '--');
    put('frameTemp', fieldAvailable(d, 'temp_c') ? `${d.temp_c.toFixed(1)} °C` : '--');
    put('frameIMU', fieldAvailable(d, 'imu') ? `${d.imu} (${IMU_LABELS[d.imu] || 'state'})` : '--');
    put('frameBattery', fieldAvailable(d, 'battery') ? `${d.battery} / 7` : '--');
    put('frameAlert', alert != null ? `${alert ? '1 (SET)' : '0 (CLEAR)'}` : '--');
    put('frameSeq', d.seq != null ? `Seq ${d.seq}` : '--');
    put('frameCRC', d.crc_status ? String(d.crc_status) : '—');
  }

  renderFreshness() {
    const d = this.current, age = this.age();
    const statusLabel = age >= 25 ? 'STALE' : age >= 15 ? 'aging' : 'current';
    put('ageText', age === null ? '— · waiting' : `${age.toFixed(1)} s · ${statusLabel}`);
    const transport = !this.connected ? 'Stream disconnected' : this.mode === 'serial' ? (this.stats.serial_connected ? 'UART connected' : 'UART disconnected') : 'Stream connected';
    put('connectionText', transport);
    put('linkSummary', d ? `Last seq ${d.seq} · age ${age.toFixed(1)} s · CRC ${d.crc_status || 'unverified'}` : 'Waiting for a distinct snapshot.');
    put('footerState', d ? `Last receipt ${formatTime(d.timestamp_ms)} · ${age >= 25 ? 'STALE' : 'snapshot'}` : 'Waiting for data');
    const missing = this.unavailable();
    const fields = [['HR', 'hr', 'HR'], ['SpO2', 'spo2', 'SpO2'], ['Temp', 'temp_c', 'Temperature'], ['IMU', 'imu', 'IMU'], ['Battery', 'battery', null]];
    for (const [id, key, prefix] of fields) {
      const valid = !!d && fieldAvailable(d, key);
      put(`val${id}`, valid ? (key === 'temp_c' ? d[key].toFixed(1) : d[key]) : '--');
      const warnings = valid && !missing && prefix ? (d.dashboard_warnings || []).filter(reason => reason.startsWith(prefix)) : [];
      const message = !valid ? 'Data unavailable' : missing ? 'Data unavailable · held value' : warnings.length ? `${warnings.join(' · ')}` : key === 'battery' ? 'Encoded level · no demo threshold' : 'No dashboard warning';
      put(`warn${id}`, message);
      $(`card${id}`).classList.toggle('warning', warnings.length > 0);
      $(`card${id}`).classList.toggle('unavailable', !valid || missing);
    }

    const segsWrap = $('batterySegments');
    if (segsWrap) {
      const validBat = !!d && fieldAvailable(d, 'battery');
      const level = validBat ? Math.max(0, Math.min(7, Math.round(d.battery))) : 0;
      const segs = segsWrap.children || [];
      for (let i = 0; i < segs.length; i++) {
        segs[i].classList.toggle('active', validBat && i < level);
      }
      segsWrap.classList.toggle('low', validBat && level <= 1);
    }

    put('txAlertText', d ? `${(d.tx_alert_bit ?? d.alert) ? 'set' : 'clear'} TX alert bit: ${d.tx_alert_bit ?? d.alert}` : 'TX alert bit: —');
    const episode = this.alarms[d?.patient];
    const active = !missing && episode?.active;
    this.chart.episode = episode || null;
    this.chart.active = !!active;
    $('ageText').parentElement.classList.toggle('stale', age !== null && age >= 25);
    put('alarmOnset', episode?.active && episode.timestamp_ms ? `Episode onset ${formatTime(episode.timestamp_ms)}` : '');
    const retained = missing && episode?.active ? ` Retained episode ${episode.episode_id} for Patient #${d.patient}${episode.acknowledged ? ' (acknowledged)' : ' (unacknowledged)'}: ${episode.reasons.join(' · ')}.` : '';
    $('alarmPanel').classList.toggle('warning', !!active);
    put('alarmTitle', missing ? 'Data unavailable' : active ? `Patient #${d.patient} · episode ${episode.episode_id}${episode.acknowledged ? ' · acknowledged' : ' · warning'}` : 'No active dashboard alarm');
    put('alarmDetail', missing ? `${!d ? 'Waiting for a distinct snapshot.' : age >= 25 ? 'Snapshot is STALE.' : !this.connected ? 'Stream disconnected.' : this.mode === 'serial' && !this.stats.serial_connected ? 'UART disconnected.' : 'One or more vital fields are invalid.'} Unresolved episodes are retained.${retained}` : active ? episode.reasons.join(' · ') : 'No active dashboard alarm.');
    $('ackButton').hidden = this.figure || !active;
    $('ackButton').disabled = !!episode?.acknowledged;
    put('ackButton', episode?.acknowledged ? 'Acknowledged' : 'Acknowledge episode');
  }

  updateChart() {
    const rows = this.current ? this.history.filter(row => row.patient === this.current.patient && row.source === this.current.source) : [];
    this.chart.updateData(rows);
    this.timeline?.updateData(rows, this.current, this.stats);
  }

  renderPackets() {
    const rows = this.current ? this.history.filter(row => !row.gap && row.patient === this.current.patient && row.source === this.current.source).slice(-5).reverse() : [];
    $('packetRows').replaceChildren();
    for (const d of rows) {
      const tr = document.createElement('tr');
      const value = key => fieldAvailable(d, key) ? d[key] : '--';
      const warnings = (d.dashboard_warnings || []).join(', ');
      const cells = [
        `${formatTime(d.timestamp_ms)} / #${d.patient}`,
        `${d.seq} / ${d.decoder || '—'}${this.figure ? '' : ` · CRC ${d.crc_status || 'unverified'}`}`,
        `HR ${value('hr')} · SpO2 ${value('spo2')} · Temp ${value('temp_c')} · IMU ${value('imu')} · Battery ${value('battery')}/7 · TX ${d.tx_alert_bit ?? d.alert}${this.figure ? (warnings ? ` · ${warnings}` : '') : ` · ${warnings || 'no dashboard warning'}`}`
      ];
      for (const text of cells) {
        const td = document.createElement('td');
        td.textContent = text;
        tr.append(td);
      }
      $('packetRows').append(tr);
    }
  }

  appendLog(data) {
    const div = document.createElement('div');
    div.textContent = `[${data.time || '—'}] ${data.text || ''}`;
    $('terminalLogs').append(div);
    while ($('terminalLogs').children.length > 200) $('terminalLogs').firstChild.remove();
    $('terminalLogs').scrollTop = $('terminalLogs').scrollHeight;
  }

  async refreshComparison() {
    const data = await this.request('/api/comparison');
    this.comparisons = data.history.slice(-100);
    this.comparisonStats(data.stats);
    this.renderComparisons();
  }

  comparisonStats(stats) {
    put('comparisonStats', `${Number.isFinite(stats.accuracy_pct) ? `${stats.accuracy_pct}% field match` : 'N/A · no comparisons'} · ${stats.total_matched ?? 0} matched / ${stats.total_compared ?? 0} compared · TX ${stats.tx_connected ? 'connected' : 'disconnected'}`);
  }

  renderComparisons() {
    $('comparisonRows').replaceChildren();
    for (const d of this.comparisons.slice(-100).reverse()) {
      const tr = document.createElement('tr');
      for (const value of [d.timestamp, `${d.patient} / ${d.seq}`, d.status]) {
        const td = document.createElement('td');
        td.textContent = value;
        tr.append(td);
      }
      $('comparisonRows').append(tr);
    }
  }

  sound() {
    const episode = this.alarms[this.current?.patient];
    if (!this.soundEnabled || !this.audio || this.unavailable() || !episode?.active || episode.acknowledged || Date.now() - this.lastTone < 3000) return;
    this.lastTone = Date.now();
    const osc = this.audio.createOscillator(), gain = this.audio.createGain();
    osc.frequency.value = 720;
    gain.gain.setValueAtTime(0.04, this.audio.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, this.audio.currentTime + 0.2);
    osc.connect(gain);
    gain.connect(this.audio.destination);
    osc.start();
    osc.stop(this.audio.currentTime + 0.21);
  }
}

window.addEventListener('DOMContentLoaded', () => {
  window.monitorApp = new MedicalMonitorApp();
});
