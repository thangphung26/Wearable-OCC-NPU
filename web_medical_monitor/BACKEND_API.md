# Backend API handoff

Research demonstration only. Existing HTTP/SSE endpoint names and the UART
`[TELEMETRY:<DECODER>] {json}` wire format remain supported. The frontend also supports a publication figure view.

| Area | Fields / behavior for the frontend task |
| --- | --- |
| Initial state / source changes | `current_telemetry` (status) and `telemetry` (SSE init) can be `null`. Clear old cards/trends and display `receiver_stats.state`, e.g. `waiting for serial`. |
| CRC | Packets retain an explicitly supplied boolean `crc_valid`. Missing CRC stays absent; `crc_status` is `unverified`. Accepted explicit true reports `valid`. False packets are counted and never published. Simulation supplies no hardware CRC assertion. |
| Shared thresholds | `receiver_stats.thresholds` (SSE `stats.thresholds`) contains `hr_low`, `hr_high`, `spo2_low`, `temp_high_c`, and `imu_warning_states` from the shared backend rules. |
| Warning rules | `tx_alert_bit` is the transmitted 0/1 `alert` flag (legacy `alert` retained). `dashboard_warnings` is a list computed independently of that bit, using `backend/thresholds.py`. `alert_reasons` is the computed warnings plus an asserted TX-bit reason. Card/feed warnings should use the computed fields. |
| Alarms | Current `alert` has `episode_id` when active; new patient/reason set starts a new unacknowledged episode with a fresh timestamp. `alarms` on status/init maps patient IDs to their episode state. Acknowledgment requires `{action:"acknowledge", patient:<int>, episode_id:<int>}`; a missing identity returns 400 and a changed/resolved episode returns 409. Only the matching patient episode is acknowledged. Invalid/unavailable readings cannot resolve an episode; a complete available normal snapshot can. |
| Freshness | `data_age_sec` is null before the first distinct snapshot, then monotonic age since the last accepted distinct patient/sequence. `stale` becomes true at 25 s (about 2.5 times the nominal 10 s period). Duplicate patient/seq packets never refresh this age or enter history. Sequence wraps at 15 to 0. |
| Status counters | `valid` counts accepted distinct snapshots (including CRC-unverified); `rejected_crc` counts explicitly CRC-failed input; `duplicate_seq` counts suppressed repeats. These dashboard counters are scoped to the current source: mode changes and forced UART reconnects reset them. `counters_since_ms` records the scope start as Unix milliseconds; `source` identifies the scope. Inputs from inactive sources do not affect these totals. `valid_packets`/`duplicate_packets` are compatibility aliases. Device totals are separate `receiver_valid_packets`/`receiver_rejected_crc` fields, present only after serial status. Do not add these to dashboard counters: observations may overlap. |
| Rates | `acquisition_fps` is null until RX reports FPS (nominal acquisition 30 FPS); actual reported values are preserved. `resampling_hz:20`, `link_bit_s:5`, `snapshot_period_s:10.0` (simulation can override the last value via `--sim-period`). Former fake `fps`, signal and ROI defaults are removed. Hardware ROI fields exist only after receiver reports them. |
| Simulation | Packets use `source:"simulation"`, `decoder:"simulated"`. Default cadence 10 s; selector/test controls can produce an immediate snapshot. `/api/patient` and alert `trigger`/`clear` return 409 outside simulation with no publication. The selector is disabled outside simulation. |
| SSE | No `ecg`/waveform events. Existing telemetry, alert, mode, receiver status, logs and comparison status events remain. Queues are capped at 100 events/client. |
| History | Maximum 200 accepted snapshots, each with server receipt `timestamp_ms`, source, patient and validity flags. `/api/history?patient=1&source=serial` can filter traces. Plot invalid readings as gaps. The frontend plots receipt-time axes with invalid/outage gaps and filters by current patient/source. |
| Comparison | `accuracy_pct:null` at zero comparisons: display N/A. `compared_fields` explicitly lists patient, seq, hr, spo2, temp_c, imu, battery, alert. Exact normalized field equality is used (temperature represented to 0.1 C), described as `field match`/`field mismatch`. No bit-level claim. TX cache uses `(patient,seq)`, expires after 25 s, consumes matches, and clears on mode/source or TX connection changes. |
| CSV / retention | `/api/export_csv` streams one CSV row per comparison from the last 60 comparison entries in this process. Headers: timestamp, patient, seq, status, then tx_/rx_ for each compared field. No persistent recording/append. Existing CSV file is untouched and no longer exported. Up to 100 backend logs, 200 snapshots, 60 comparisons and 256 TX cache keys remain in RAM; restart clears them. `/api/reset` clears snapshot history/logs as before. |
| JSON input | JSON objects only, max 16 KiB; malformed/non-object bodies return 400, oversized bodies 413. Protocol integers must be JSON integers, not strings, booleans or fractions. Validity/CRC flags and auto_cycle must be JSON booleans. `seq` is 0–15. Invalid requests do not change clinical state. CRC-failed input changes only rejection counters. |
| Serial controls | `/api/connect_dual` opens RX only when `rx_port` is explicitly supplied. A TX-only request preserves RX. Connect/disconnect transitions share a lock; port/baud validation precedes opening either port. |
| Benchmark | New `GET /api/benchmark` loads `benchmark_reference.json`: `description`, `models[]` with model, cpu_ms, npu_ms, source. Original 24.996/3.019 ms verified in paper table; augmented 24.958/2.793 ms rounded from full-API microsecond means in summary JSON. Hardware benchmark latency is not live channel performance. |
| Network | HTTP and UDP default to 127.0.0.1. `--host 0.0.0.0` warns about unauthenticated exposure. Wildcard CORS removed; cross-origin browser POSTs return 403. Authentication remains out of scope; use a trusted lab network. |

Shared research-demonstration thresholds: HR <50 or >140 bpm, SpO2 <94%,
temperature >=39 C, IMU states 2/3. Zero or false-validity readings are unavailable,
not recoveries. Reason strings describe protocol values, not diagnoses.

Minimal frontend compatibility changes consume nullable FPS/accuracy, display CRC
status/counters, use computed warning fields, clear snapshots on mode change,
disable the simulation selector in hardware modes, remove waveform subscription,
escape comparison sequence values, and fetch measured benchmark references.
The redesigned frontend uses the snapshot panel for validity-aware time trends and
packet diagnostics, with responsive layout, light/print themes and accessible dialogs.
