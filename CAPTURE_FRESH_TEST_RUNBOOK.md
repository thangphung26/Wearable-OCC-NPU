# Fresh test campaign: 1/3/5/7 m × lamps ON/OFF, new payload (pre-registered)

Written 2026-11-09, **before any fresh capture**. Fixes what is captured, how it is
scored, and the evaluation criteria, so results cannot steer the procedure.
Commands, firmware restore, ROI GUI, wrapper and acceptance checks are the same as
[CAPTURE_7M_RUNBOOK.md](CAPTURE_7M_RUNBOOK.md) §2–§5. Only the differences are listed here.

## 1. Purpose and frozen models
- Independent-day, unseen-payload test of the **final augmented CNN** (AUG v2,
  `AI_Pipeline_amebaPro2/retrain_sessions/results_v2/repeat_aug/deployment_candidate_aug_int8.tflite`,
  sha256 `ef9076f8f5c08cf17ded2e5b5374f92d40b814ab0ad084462a7fe1eaa9e373ca`; NBG `ameba_deployment/aug/work_v2/vlc_edge_cnn_aug_v2.nb`).
  Classic is the baseline. The non-augmented retrained CNN (`results_v2/repeat/deployment_candidate_int8.tflite`) is a host-only ablation.
- No retraining, recalibration, threshold or architecture change after this point. No
  session from this campaign may enter any training, validation, calibration or model selection.

## 2. Payload
- TX sketch `TX/VLC_OOK_TX_realtime_sensors/` (benchmark sketch, source unchanged),
  built with `-DVLC_BENCHMARK_SEED=20261199UL`, FQBN `Seeeduino:nrf52:xiaonRF52840Sense`.
- The seed was chosen before capture **only from payload properties**. Among seeds
  20261100–20261199 with zero word overlap, it maximises the minimum Hamming distance to
  the 100 training words. Result: 100 distinct words, 0 overlap, minimum distance 8 bits.
  The generator reproduces the recorded training truth exactly (checked).
- Schema unchanged: header 1010, patient ID 1, HR = 30 + index, sequence = index & 15.
  Those 20 bits follow the same index pattern as training; SpO2/temperature/IMU/battery/
  alert and CRC differ. Report this in the paper.
- Before the first batch, verify TX `[TX_GT]` words equal the predicted seed-20261199 words.
- After the campaign, flash the TX back to its previous firmware (`VLC_OOK_TX_realtime_sensors_rt_imu`) for the live demo.

## 3. Conditions, ROI, order
| Distance | ROI size (w×h), same as training campaign | Lamps | Repeats |
|---|---|---|---|
| 1 m | 71×65 | ON, OFF | 3 + 3 |
| 3 m | 32×26 | ON, OFF | 3 + 3 |
| 5 m | 25×28 | ON, OFF | 3 + 3 |
| 7 m | 14×15 | ON, OFF | 3 + 3 |

- ROI rule (fixed; user decision 2026-11-09: keep campaign sizes, Claude selects): Claude takes serial
  JPEG snapshots with lamps ON and computes the brightest hotspot on their max-composite (snapshots can
  land in an LED-off half-bit). The centre is that hotspot and the size is the campaign size above, clipped
  to 320×192. Claude checks on the snapshot that the box covers the LED and not a lamp or reflection, and
  saves the annotated image with the batch. The author only confirms the physical setup.
  **Amendment 2026-11-09 18:14, before any fresh capture:** at 7 m with lamps ON the brightest-hotspot
  rule picked a ceiling lamp (`results/fresh_roi/7m_20261109_181400*`). The check caught it. The centre is
  now the peak of the blurred modulation map (max − min over the snapshots) inside the processed 320×192
  region. The size rule is unchanged, and the brightest-rule box is still recorded for reference. One ROI per
  distance, used for both lamp states. ROI is never re-chosen after seeing any decoder output.
- At each distance: ON batch (s01–s03), then OFF batch (s01–s03), with geometry untouched in between.
  Record lamp state, daylight/window state and any lux reading per batch.
- Total acquisition: 24 × 1010 s ≈ 6.7 h, plus per-session replay and setup.

## 4. Firmware and names
- Capture with the archived original capture bundle (CAPTURE_7M_RUNBOOK §3). Front-end,
  exposure 2 ms, gain 256, JPEG gray8v2, 30 FPS, 20 Hz and `tracker-manchester-v1` are
  identical to the training campaign. The capture firmware's own CNN outputs are **not reported**.
- Names: batch `goal2_gray8v2_phasev1_fresh_seed20261199_<d>m_roi<x>_<y>_<w>_<h>_exp2000_lights_<on|off>_<YYYYMMDD>`,
  capture prefix `goal2_precrc_gray8v2_phasev1_fresh_seed20261199_…_100f`, replay prefix `goal2_replay_…_100f`.
- AUG evaluation: after all captures, flash `RX/Ameba_CPU_NPU_Benchmark_AUG_V2` and run
  `retrain_sessions/onboard_replay_sessions.py` on every replay session. This replays the stored
  192-byte inputs through the board CPU and NPU. Board CPU must equal host TFLite.

## 5. Exclusions (fixed)
- Technical failure (incomplete capture, missing frames, parity failure): keep the directory,
  re-record under a new explicit name. Never decided by BER.
- Physical disturbance (person in the beam, mount bumped, lamp state changed): note it
  immediately with the time, before looking at any result. Keep the session, flag it, and record
  one extra repeat right after. The primary set is the first three undisturbed repeats.
- Nothing else may be removed. All aligned windows and CRC failures stay in the denominator.

## 6. Scoring (fixed)
- `retrain_sessions/eval_fresh.py` with a manifest of the sessions above. Targets are TX
  `frame_hex` bits. Polarity and threshold conventions are as in `eval_controls.py` (results_v2).
- Report per condition (14,400 bits), per distance, per lighting state and pooled (115,200 bits):
  errors, BER with exact 95 % Clopper–Pearson interval, and exact windows. Decoders: Classic,
  AUG board CPU, AUG board NPU (primary), retrained host (ablation). Also report CPU–NPU
  disagreements, board-CPU vs host parity, payload overlap and Hamming statistics, and per-session
  preamble gain and tracker flags.
- Evaluation criteria for the claim "AUG on NPU decodes at least as accurately as Classic on
  independent-day, unseen-payload data":
  (a) AUG NPU errors ≤ Classic errors pooled, and in every condition **or** within the
      overlapping-CP range with the difference stated;
  (b) board CPU == host TFLite on every window;
  (c) every window is scored and the payload overlap is 0.
- Whether or not the criteria are met, every number is reported. If (a) fails, the paper states it
  and the claim is narrowed to what the data show.

## 7. After capture (Claude)
1. Run the CAPTURE_7M_RUNBOOK §5 acceptance audit on each batch.
2. Flash AUG_V2 and run the on-board replay of all fresh sessions.
3. Run `eval_fresh.py` to produce the report.
4. Flash `RX/Ameba_RX_NPU_Receiver_Track_AUG` and TX `rt_imu` for the live demo (≥ 50 frames, about 9 min).
5. Rewrite the paper with AUG only.
