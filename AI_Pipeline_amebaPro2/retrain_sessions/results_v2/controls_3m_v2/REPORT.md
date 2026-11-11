# Held-out 3 m control evaluation

UTC run: 2026-11-08T03:48:32.718803+00:00

CPU-only host inference; no training, serial access, or firmware execution. Labels come only from capture TX ground truth with `payload_mode == test`; frame IDs and frame hex are checked against replay records. No CRC or error-based filtering is applied.

The exact stored INT8 windows are read via `export_dataset.replay_input`. The existing import-safe helper is reused without modification. Raw windows are regenerated with the existing `PreambleTracker.align` and `manchester_phase.refine`, then checked byte-for-byte using `benchmark_precrc.normalize`. All three host models use `train_eval.tflite_predict` and model-specific output quantization.

Acquisition: 30 FPS; resampling: 20 Hz; link: 5 bit/s. Error bit positions are zero-based (0–47). Window identifiers are `session:frame_id`, not row numbers.

95% intervals are exact two-sided Clopper–Pearson, imported from `train_eval.ci`. BER intervals pool bit errors within each group; exact-window intervals use whole-window successes. Bits and windows may be correlated, so these binomial intervals do not establish independent-day generalization. New candidates have host-only results and still require board CPU/NPU deployment verification.

## Skipped batches

None.

## Session map and exclusions

The replacement rule was fixed at 12:08 before s02 was scored. Primary tight-ROI repeats are 1 = s01, 2 = s04, 3 = s03.

| Group | Display repeat | Real replay session |
|---|---:|---|
| wide_off | 1 | goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107_100f_s01 |
| wide_off | 2 | goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107_100f_s02 |
| wide_off | 3 | goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107_100f_s03 |
| wide_on | 1 | goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s01 |
| wide_on | 2 | goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s02 |
| wide_on | 3 | goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s03 |
| tight_on_primary | 1 | goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s01 |
| tight_on_primary | 2 | goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s04 |
| tight_on_primary | 3 | goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s03 |

- Flagged and excluded from primary: `goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s02`; reason: person crossed room during capture. Replacement: `goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s04` (display repeat 2). Retained in `tight_on_excluded` and both CSVs.

## wide_off

| Decoder | Errors / bits | BER (95% CP) | Exact / windows | Exact rate 95% CP |
|---|---:|---|---:|---|
| classic | 0/14400 | 0 [0, 0.00025613938] | 300/300 | [0.98777903, 1] |
| original_ch4 | 0/14400 | 0 [0, 0.00025613938] | 300/300 | [0.98777903, 1] |
| candidate | 0/14400 | 0 [0, 0.00025613938] | 300/300 | [0.98777903, 1] |
| augmented | 0/14400 | 0 [0, 0.00025613938] | 300/300 | [0.98777903, 1] |
| board_cpu | 0/14400 | 0 [0, 0.00025613938] | 300/300 | [0.98777903, 1] |
| board_npu | 0/14400 | 0 [0, 0.00025613938] | 300/300 | [0.98777903, 1] |

### Per session

| Session | Decoder | Errors / 4800 bits | Exact / 100 windows |
|---|---|---:|---:|
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107_100f_s01 | classic | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107_100f_s01 | original_ch4 | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107_100f_s01 | candidate | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107_100f_s01 | augmented | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107_100f_s01 | board_cpu | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107_100f_s01 | board_npu | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107_100f_s02 | classic | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107_100f_s02 | original_ch4 | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107_100f_s02 | candidate | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107_100f_s02 | augmented | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107_100f_s02 | board_cpu | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107_100f_s02 | board_npu | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107_100f_s03 | classic | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107_100f_s03 | original_ch4 | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107_100f_s03 | candidate | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107_100f_s03 | augmented | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107_100f_s03 | board_cpu | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107_100f_s03 | board_npu | 0/4800 | 100/100 |

### Paired missed windows

Complete per-session and per-group pairwise identities are in `summary.json`; each window and error position is in `per_window.csv`. 

- classic misses: none.
- original_ch4 misses: none.
- candidate misses: none.
- augmented misses: none.
- board_cpu misses: none.
- board_npu misses: none.

| First / second | Both miss | Only first misses | Only second misses | Both exact |
|---|---:|---:|---:|---:|
| classic / original_ch4 | 0 | 0 | 0 | 300 |
| classic / candidate | 0 | 0 | 0 | 300 |
| classic / augmented | 0 | 0 | 0 | 300 |
| classic / board_cpu | 0 | 0 | 0 | 300 |
| classic / board_npu | 0 | 0 | 0 | 300 |
| original_ch4 / candidate | 0 | 0 | 0 | 300 |
| original_ch4 / augmented | 0 | 0 | 0 | 300 |
| original_ch4 / board_cpu | 0 | 0 | 0 | 300 |
| original_ch4 / board_npu | 0 | 0 | 0 | 300 |
| candidate / augmented | 0 | 0 | 0 | 300 |
| candidate / board_cpu | 0 | 0 | 0 | 300 |
| candidate / board_npu | 0 | 0 | 0 | 300 |
| augmented / board_cpu | 0 | 0 | 0 | 300 |
| augmented / board_npu | 0 | 0 | 0 | 300 |
| board_cpu / board_npu | 0 | 0 | 0 | 300 |

## wide_on

| Decoder | Errors / bits | BER (95% CP) | Exact / windows | Exact rate 95% CP |
|---|---:|---|---:|---|
| classic | 0/14400 | 0 [0, 0.00025613938] | 300/300 | [0.98777903, 1] |
| original_ch4 | 12/14400 | 0.00083333333 [0.00043066727, 0.0014552127] | 292/300 | [0.94813419, 0.98841843] |
| candidate | 0/14400 | 0 [0, 0.00025613938] | 300/300 | [0.98777903, 1] |
| augmented | 0/14400 | 0 [0, 0.00025613938] | 300/300 | [0.98777903, 1] |
| board_cpu | 12/14400 | 0.00083333333 [0.00043066727, 0.0014552127] | 292/300 | [0.94813419, 0.98841843] |
| board_npu | 12/14400 | 0.00083333333 [0.00043066727, 0.0014552127] | 292/300 | [0.94813419, 0.98841843] |

### Per session

| Session | Decoder | Errors / 4800 bits | Exact / 100 windows |
|---|---|---:|---:|
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s01 | classic | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s01 | original_ch4 | 2/4800 | 99/100 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s01 | candidate | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s01 | augmented | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s01 | board_cpu | 2/4800 | 99/100 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s01 | board_npu | 2/4800 | 99/100 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s02 | classic | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s02 | original_ch4 | 2/4800 | 98/100 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s02 | candidate | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s02 | augmented | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s02 | board_cpu | 2/4800 | 98/100 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s02 | board_npu | 2/4800 | 98/100 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s03 | classic | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s03 | original_ch4 | 8/4800 | 95/100 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s03 | candidate | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s03 | augmented | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s03 | board_cpu | 8/4800 | 95/100 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s03 | board_npu | 8/4800 | 95/100 |

### Paired missed windows

Complete per-session and per-group pairwise identities are in `summary.json`; each window and error position is in `per_window.csv`. 

- classic misses: none.
- original_ch4 misses: goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s01:6453, goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s02:6498, goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s02:6544, goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s03:6634, goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s03:6635, goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s03:6679, goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s03:6680, goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s03:6681.
- candidate misses: none.
- augmented misses: none.
- board_cpu misses: goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s01:6453, goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s02:6498, goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s02:6544, goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s03:6634, goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s03:6635, goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s03:6679, goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s03:6680, goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s03:6681.
- board_npu misses: goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s01:6453, goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s02:6498, goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s02:6544, goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s03:6634, goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s03:6635, goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s03:6679, goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s03:6680, goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s03:6681.

| First / second | Both miss | Only first misses | Only second misses | Both exact |
|---|---:|---:|---:|---:|
| classic / original_ch4 | 0 | 0 | 8 | 292 |
| classic / candidate | 0 | 0 | 0 | 300 |
| classic / augmented | 0 | 0 | 0 | 300 |
| classic / board_cpu | 0 | 0 | 8 | 292 |
| classic / board_npu | 0 | 0 | 8 | 292 |
| original_ch4 / candidate | 0 | 8 | 0 | 292 |
| original_ch4 / augmented | 0 | 8 | 0 | 292 |
| original_ch4 / board_cpu | 8 | 0 | 0 | 292 |
| original_ch4 / board_npu | 8 | 0 | 0 | 292 |
| candidate / augmented | 0 | 0 | 0 | 300 |
| candidate / board_cpu | 0 | 0 | 8 | 292 |
| candidate / board_npu | 0 | 0 | 8 | 292 |
| augmented / board_cpu | 0 | 0 | 8 | 292 |
| augmented / board_npu | 0 | 0 | 8 | 292 |
| board_cpu / board_npu | 8 | 0 | 0 | 292 |

## tight_on_primary

| Decoder | Errors / bits | BER (95% CP) | Exact / windows | Exact rate 95% CP |
|---|---:|---|---:|---|
| classic | 0/14400 | 0 [0, 0.00025613938] | 300/300 | [0.98777903, 1] |
| original_ch4 | 0/14400 | 0 [0, 0.00025613938] | 300/300 | [0.98777903, 1] |
| candidate | 0/14400 | 0 [0, 0.00025613938] | 300/300 | [0.98777903, 1] |
| augmented | 0/14400 | 0 [0, 0.00025613938] | 300/300 | [0.98777903, 1] |
| board_cpu | 0/14400 | 0 [0, 0.00025613938] | 300/300 | [0.98777903, 1] |
| board_npu | 0/14400 | 0 [0, 0.00025613938] | 300/300 | [0.98777903, 1] |

### Per session

| Session | Decoder | Errors / 4800 bits | Exact / 100 windows |
|---|---|---:|---:|
| goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s01 | classic | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s01 | original_ch4 | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s01 | candidate | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s01 | augmented | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s01 | board_cpu | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s01 | board_npu | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s04 | classic | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s04 | original_ch4 | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s04 | candidate | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s04 | augmented | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s04 | board_cpu | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s04 | board_npu | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s03 | classic | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s03 | original_ch4 | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s03 | candidate | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s03 | augmented | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s03 | board_cpu | 0/4800 | 100/100 |
| goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s03 | board_npu | 0/4800 | 100/100 |

### Paired missed windows

Complete per-session and per-group pairwise identities are in `summary.json`; each window and error position is in `per_window.csv`. 

- classic misses: none.
- original_ch4 misses: none.
- candidate misses: none.
- augmented misses: none.
- board_cpu misses: none.
- board_npu misses: none.

| First / second | Both miss | Only first misses | Only second misses | Both exact |
|---|---:|---:|---:|---:|
| classic / original_ch4 | 0 | 0 | 0 | 300 |
| classic / candidate | 0 | 0 | 0 | 300 |
| classic / augmented | 0 | 0 | 0 | 300 |
| classic / board_cpu | 0 | 0 | 0 | 300 |
| classic / board_npu | 0 | 0 | 0 | 300 |
| original_ch4 / candidate | 0 | 0 | 0 | 300 |
| original_ch4 / augmented | 0 | 0 | 0 | 300 |
| original_ch4 / board_cpu | 0 | 0 | 0 | 300 |
| original_ch4 / board_npu | 0 | 0 | 0 | 300 |
| candidate / augmented | 0 | 0 | 0 | 300 |
| candidate / board_cpu | 0 | 0 | 0 | 300 |
| candidate / board_npu | 0 | 0 | 0 | 300 |
| augmented / board_cpu | 0 | 0 | 0 | 300 |
| augmented / board_npu | 0 | 0 | 0 | 300 |
| board_cpu / board_npu | 0 | 0 | 0 | 300 |

## tight_on_excluded

| Decoder | Errors / bits | BER (95% CP) | Exact / windows | Exact rate 95% CP |
|---|---:|---|---:|---|
| classic | 3/4800 | 0.000625 [0.00012890858, 0.0018254182] | 98/100 | [0.92961607, 0.99756866] |
| original_ch4 | 3/4800 | 0.000625 [0.00012890858, 0.0018254182] | 98/100 | [0.92961607, 0.99756866] |
| candidate | 3/4800 | 0.000625 [0.00012890858, 0.0018254182] | 98/100 | [0.92961607, 0.99756866] |
| augmented | 3/4800 | 0.000625 [0.00012890858, 0.0018254182] | 98/100 | [0.92961607, 0.99756866] |
| board_cpu | 3/4800 | 0.000625 [0.00012890858, 0.0018254182] | 98/100 | [0.92961607, 0.99756866] |
| board_npu | 3/4800 | 0.000625 [0.00012890858, 0.0018254182] | 98/100 | [0.92961607, 0.99756866] |

### Per session

| Session | Decoder | Errors / 4800 bits | Exact / 100 windows |
|---|---|---:|---:|
| goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s02 | classic | 3/4800 | 98/100 |
| goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s02 | original_ch4 | 3/4800 | 98/100 |
| goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s02 | candidate | 3/4800 | 98/100 |
| goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s02 | augmented | 3/4800 | 98/100 |
| goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s02 | board_cpu | 3/4800 | 98/100 |
| goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s02 | board_npu | 3/4800 | 98/100 |

### Paired missed windows

Complete per-session and per-group pairwise identities are in `summary.json`; each window and error position is in `per_window.csv`. 

- classic misses: goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s02:6951, goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s02:6968.
- original_ch4 misses: goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s02:6951, goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s02:6968.
- candidate misses: goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s02:6951, goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s02:6968.
- augmented misses: goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s02:6951, goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s02:6968.
- board_cpu misses: goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s02:6951, goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s02:6968.
- board_npu misses: goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s02:6951, goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s02:6968.

| First / second | Both miss | Only first misses | Only second misses | Both exact |
|---|---:|---:|---:|---:|
| classic / original_ch4 | 2 | 0 | 0 | 98 |
| classic / candidate | 2 | 0 | 0 | 98 |
| classic / augmented | 2 | 0 | 0 | 98 |
| classic / board_cpu | 2 | 0 | 0 | 98 |
| classic / board_npu | 2 | 0 | 0 | 98 |
| original_ch4 / candidate | 2 | 0 | 0 | 98 |
| original_ch4 / augmented | 2 | 0 | 0 | 98 |
| original_ch4 / board_cpu | 2 | 0 | 0 | 98 |
| original_ch4 / board_npu | 2 | 0 | 0 | 98 |
| candidate / augmented | 2 | 0 | 0 | 98 |
| candidate / board_cpu | 2 | 0 | 0 | 98 |
| candidate / board_npu | 2 | 0 | 0 | 98 |
| augmented / board_cpu | 2 | 0 | 0 | 98 |
| augmented / board_npu | 2 | 0 | 0 | 98 |
| board_cpu / board_npu | 2 | 0 | 0 | 98 |

## Erroneous windows

Amplitude = mean expected ON samples minus mean expected OFF samples, using all 48 TX truth bits and Manchester half-bit assignments. `amplitude_before_normalize` uses the regenerated raw float input (capture intensity units); `amplitude_normalized_input` uses the exact training-scale input `(q+128)/255`. This reports both interpretations of normalized-before input. Fitted gain is `signed_optical_gain`, falling back to `tracker.gain`; absent values are null/blank.

| Session:frame_id | Decoder errors (bit positions in CSV) | Amplitude before normalize | Amplitude normalized input | Fitted preamble gain |
|---|---|---:|---:|---:|
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s01:6453 | original_ch4: 2, board_cpu: 2, board_npu: 2 | 8.4508349 | 0.68231213 | 7.897659970682284 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s02:6498 | original_ch4: 1, board_cpu: 1, board_npu: 1 | 11.401476 | 0.66850489 | 12.79611120783179 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s02:6544 | original_ch4: 1, board_cpu: 1, board_npu: 1 | 13.418432 | 0.74448526 | 14.893427477437463 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s03:6634 | original_ch4: 1, board_cpu: 1, board_npu: 1 | 7.4865858 | 0.69734472 | 10.360058807872976 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s03:6635 | original_ch4: 1, board_cpu: 1, board_npu: 1 | 5.6692112 | 0.71670759 | 7.387822385127643 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s03:6679 | original_ch4: 2, board_cpu: 2, board_npu: 2 | 13.580122 | 0.69044113 | 14.23904642882931 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s03:6680 | original_ch4: 2, board_cpu: 2, board_npu: 2 | 10.217275 | 0.61017156 | 9.680904909974677 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s03:6681 | original_ch4: 2, board_cpu: 2, board_npu: 2 | 6.9302574 | 0.61695254 | 8.191144307404699 |
| goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s02:6951 | classic: 1, original_ch4: 1, candidate: 1, augmented: 1, board_cpu: 1, board_npu: 1 | 152.02884 | 0.92642975 | 162.55283865153962 |
| goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s02:6968 | classic: 2, original_ch4: 2, candidate: 2, augmented: 2, board_cpu: 2, board_npu: 2 | 121.06823 | 0.85861933 | 143.97395301505185 |

## Acceptance checks

- PASS `goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107_100f_s01:100_test_windows_4800_bits`: {"windows": 100, "bits": 4800, "replay_rows": 100}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107_100f_s02:100_test_windows_4800_bits`: {"windows": 100, "bits": 4800, "replay_rows": 100}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107_100f_s03:100_test_windows_4800_bits`: {"windows": 100, "bits": 4800, "replay_rows": 100}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s01:100_test_windows_4800_bits`: {"windows": 100, "bits": 4800, "replay_rows": 100}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s02:100_test_windows_4800_bits`: {"windows": 100, "bits": 4800, "replay_rows": 100}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s03:100_test_windows_4800_bits`: {"windows": 100, "bits": 4800, "replay_rows": 100}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s01:100_test_windows_4800_bits`: {"windows": 100, "bits": 4800, "replay_rows": 100}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s02:100_test_windows_4800_bits`: {"windows": 100, "bits": 4800, "replay_rows": 100}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s03:100_test_windows_4800_bits`: {"windows": 100, "bits": 4800, "replay_rows": 100}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s04:100_test_windows_4800_bits`: {"windows": 100, "bits": 4800, "replay_rows": 100}
- PASS `repeat:held_out_session_membership`: {"overlapping_sessions": []}
- PASS `repeat_aug:held_out_session_membership`: {"overlapping_sessions": []}
- PASS `held_out_from_corrected_export`: {"overlapping_sessions": []}
- PASS `regenerated_normalization_byte_exact`: {"mismatching_windows": 0}
- PASS `wide_off:3x100x48`: {"sessions": 3, "windows": 300, "bits": 14400}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107_100f_s01:classic_direct_summary_parity`: {"errors": 0, "expected_errors": 0, "exact_windows": 100, "expected_exact_windows": 100}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107_100f_s01:cpu_direct_summary_parity`: {"errors": 0, "expected_errors": 0, "exact_windows": 100, "expected_exact_windows": 100}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107_100f_s01:npu_direct_summary_parity`: {"errors": 0, "expected_errors": 0, "exact_windows": 100, "expected_exact_windows": 100}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107_100f_s02:classic_direct_summary_parity`: {"errors": 0, "expected_errors": 0, "exact_windows": 100, "expected_exact_windows": 100}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107_100f_s02:cpu_direct_summary_parity`: {"errors": 0, "expected_errors": 0, "exact_windows": 100, "expected_exact_windows": 100}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107_100f_s02:npu_direct_summary_parity`: {"errors": 0, "expected_errors": 0, "exact_windows": 100, "expected_exact_windows": 100}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107_100f_s03:classic_direct_summary_parity`: {"errors": 0, "expected_errors": 0, "exact_windows": 100, "expected_exact_windows": 100}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107_100f_s03:cpu_direct_summary_parity`: {"errors": 0, "expected_errors": 0, "exact_windows": 100, "expected_exact_windows": 100}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107_100f_s03:npu_direct_summary_parity`: {"errors": 0, "expected_errors": 0, "exact_windows": 100, "expected_exact_windows": 100}
- PASS `wide_off:original_host_board_cpu_bit_exact`: {"mismatch_bits": 0, "mismatch_windows": 0}
- PASS `wide_off:expected_classic_cpu_npu_errors`: {"actual": [0, 0, 0], "expected": [0, 0, 0]}
- PASS `wide_on:3x100x48`: {"sessions": 3, "windows": 300, "bits": 14400}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s01:classic_direct_summary_parity`: {"errors": 0, "expected_errors": 0, "exact_windows": 100, "expected_exact_windows": 100}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s01:cpu_direct_summary_parity`: {"errors": 2, "expected_errors": 2, "exact_windows": 99, "expected_exact_windows": 99}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s01:npu_direct_summary_parity`: {"errors": 2, "expected_errors": 2, "exact_windows": 99, "expected_exact_windows": 99}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s02:classic_direct_summary_parity`: {"errors": 0, "expected_errors": 0, "exact_windows": 100, "expected_exact_windows": 100}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s02:cpu_direct_summary_parity`: {"errors": 2, "expected_errors": 2, "exact_windows": 98, "expected_exact_windows": 98}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s02:npu_direct_summary_parity`: {"errors": 2, "expected_errors": 2, "exact_windows": 98, "expected_exact_windows": 98}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s03:classic_direct_summary_parity`: {"errors": 0, "expected_errors": 0, "exact_windows": 100, "expected_exact_windows": 100}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s03:cpu_direct_summary_parity`: {"errors": 8, "expected_errors": 8, "exact_windows": 95, "expected_exact_windows": 95}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s03:npu_direct_summary_parity`: {"errors": 8, "expected_errors": 8, "exact_windows": 95, "expected_exact_windows": 95}
- PASS `wide_on:original_host_board_cpu_bit_exact`: {"mismatch_bits": 0, "mismatch_windows": 0}
- PASS `wide_on:expected_classic_cpu_npu_errors`: {"actual": [0, 12, 12], "expected": [0, 12, 12]}
- PASS `tight_on_primary:3x100x48`: {"sessions": 3, "windows": 300, "bits": 14400}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s01:classic_direct_summary_parity`: {"errors": 0, "expected_errors": 0, "exact_windows": 100, "expected_exact_windows": 100}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s01:cpu_direct_summary_parity`: {"errors": 0, "expected_errors": 0, "exact_windows": 100, "expected_exact_windows": 100}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s01:npu_direct_summary_parity`: {"errors": 0, "expected_errors": 0, "exact_windows": 100, "expected_exact_windows": 100}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s04:classic_direct_summary_parity`: {"errors": 0, "expected_errors": 0, "exact_windows": 100, "expected_exact_windows": 100}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s04:cpu_direct_summary_parity`: {"errors": 0, "expected_errors": 0, "exact_windows": 100, "expected_exact_windows": 100}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s04:npu_direct_summary_parity`: {"errors": 0, "expected_errors": 0, "exact_windows": 100, "expected_exact_windows": 100}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s03:classic_direct_summary_parity`: {"errors": 0, "expected_errors": 0, "exact_windows": 100, "expected_exact_windows": 100}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s03:cpu_direct_summary_parity`: {"errors": 0, "expected_errors": 0, "exact_windows": 100, "expected_exact_windows": 100}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s03:npu_direct_summary_parity`: {"errors": 0, "expected_errors": 0, "exact_windows": 100, "expected_exact_windows": 100}
- PASS `tight_on_primary:original_host_board_cpu_bit_exact`: {"mismatch_bits": 0, "mismatch_windows": 0}
- PASS `tight_on_excluded:1x100x48`: {"sessions": 1, "windows": 100, "bits": 4800}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s02:classic_direct_summary_parity`: {"errors": 3, "expected_errors": 3, "exact_windows": 98, "expected_exact_windows": 98}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s02:cpu_direct_summary_parity`: {"errors": 3, "expected_errors": 3, "exact_windows": 98, "expected_exact_windows": 98}
- PASS `goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s02:npu_direct_summary_parity`: {"errors": 3, "expected_errors": 3, "exact_windows": 98, "expected_exact_windows": 98}
- PASS `tight_on_excluded:original_host_board_cpu_bit_exact`: {"mismatch_bits": 0, "mismatch_windows": 0}
- PASS `git_status_not_used`: {"git_commands_used": 0}

Held-out status is checked against both candidates’ train, validation, calibration and prior test session lists and the corrected export session manifest. This audits recorded session membership, not undocumented training outside these artifacts. Source/model hashes are in `summary.json`.

## Files created

- `1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/results_v2/controls_3m_v2/summary.json`
- `1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/results_v2/controls_3m_v2/per_session.csv`
- `1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/results_v2/controls_3m_v2/per_window.csv`
- `1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/results_v2/controls_3m_v2/REPORT.md`

No git commands were used. Datasets and pre-existing results were not written. The running capture was not accessed through a serial port.
