# Fresh campaign evaluation

All listed non-excluded windows, no CRC/error/weak-fit filtering. TX frame_hex is 48 bits MSB first. Reuse eval_controls/export_dataset bits_hex, replay_input, decision_bytes and train_eval.ci/metric. Host inference uses builtin kernels without default delegates, one thread. Polarity is explicit: direct or per-decoder preamble-only correction; never truth-selected inversion. Host probabilities use model output scale/zero-point and >=0.5; AUG INT8 >=0 maps to 1 (scale 1/256, zero -128). Exact two-sided 95% Clopper-Pearson; pooled bits/windows may be correlated. Capture CPU/NPU values are ORIGINAL CNN and never used as AUG. 30 FPS acquisition, 20 Hz resampling, 5 bit/s link.

Manifest schema (version 1):

```json
{
  "version": 1,
  "conditions": [
    {
      "name": "unique condition name",
      "distance_m": 3,
      "lighting": "on or off",
      "sessions": [
        {
          "session": "benchmark replay directory basename",
          "repeat": 1
        }
      ]
    }
  ],
  "excluded": [
    {
      "session": "excluded replay basename",
      "reason": "required nonempty reason"
    }
  ]
}
```

Exclusions are explicit and applied before scoring; all other windows are retained.
Board metrics are unavailable in host-only mode. No capture-firmware CPU/NPU output is scored.

| Group | Decoder | Errors / bits | BER [95% CP] | Exact / windows |
|---|---|---:|---|---:|
| 7m_on | classic | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| 7m_on | aug_cpu | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| 7m_on | aug_npu | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| 7m_on | aug_host | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| 7m_on | retrained_host | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| 7m_off | classic | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| 7m_off | aug_cpu | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| 7m_off | aug_npu | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| 7m_off | aug_host | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| 7m_off | retrained_host | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| 5m_on | classic | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| 5m_on | aug_cpu | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| 5m_on | aug_npu | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| 5m_on | aug_host | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| 5m_on | retrained_host | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| 5m_off | classic | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| 5m_off | aug_cpu | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| 5m_off | aug_npu | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| 5m_off | aug_host | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| 5m_off | retrained_host | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| 3m_off | classic | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| 3m_off | aug_cpu | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| 3m_off | aug_npu | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| 3m_off | aug_host | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| 3m_off | retrained_host | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| 3m_on | classic | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| 3m_on | aug_cpu | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| 3m_on | aug_npu | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| 3m_on | aug_host | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| 3m_on | retrained_host | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| 1m_off | classic | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| 1m_off | aug_cpu | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| 1m_off | aug_npu | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| 1m_off | aug_host | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| 1m_off | retrained_host | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| 1m_on | classic | 3/14400 | 0.00020833333 [4.2965403e-05, 0.0006087165] | 297/300 |
| 1m_on | aug_cpu | 2/14400 | 0.00013888889 [1.6820531e-05, 0.00050162341] | 298/300 |
| 1m_on | aug_npu | 2/14400 | 0.00013888889 [1.6820531e-05, 0.00050162341] | 298/300 |
| 1m_on | aug_host | 2/14400 | 0.00013888889 [1.6820531e-05, 0.00050162341] | 298/300 |
| 1m_on | retrained_host | 3/14400 | 0.00020833333 [4.2965403e-05, 0.0006087165] | 297/300 |
| all | classic | 3/115200 | 2.6041667e-05 [5.3704499e-06, 7.6102896e-05] | 2397/2400 |
| all | aug_cpu | 2/115200 | 1.7361111e-05 [2.102518e-06, 6.2712881e-05] | 2398/2400 |
| all | aug_npu | 2/115200 | 1.7361111e-05 [2.102518e-06, 6.2712881e-05] | 2398/2400 |
| all | aug_host | 2/115200 | 1.7361111e-05 [2.102518e-06, 6.2712881e-05] | 2398/2400 |
| all | retrained_host | 3/115200 | 2.6041667e-05 [5.3704499e-06, 7.6102896e-05] | 2397/2400 |
| lighting:off | classic | 0/57600 | 0 [0, 6.4040995e-05] | 1200/1200 |
| lighting:off | aug_cpu | 0/57600 | 0 [0, 6.4040995e-05] | 1200/1200 |
| lighting:off | aug_npu | 0/57600 | 0 [0, 6.4040995e-05] | 1200/1200 |
| lighting:off | aug_host | 0/57600 | 0 [0, 6.4040995e-05] | 1200/1200 |
| lighting:off | retrained_host | 0/57600 | 0 [0, 6.4040995e-05] | 1200/1200 |
| lighting:on | classic | 3/57600 | 5.2083333e-05 [1.0740964e-05, 0.00015220198] | 1197/1200 |
| lighting:on | aug_cpu | 2/57600 | 3.4722222e-05 [4.2050499e-06, 0.00012542292] | 1198/1200 |
| lighting:on | aug_npu | 2/57600 | 3.4722222e-05 [4.2050499e-06, 0.00012542292] | 1198/1200 |
| lighting:on | aug_host | 2/57600 | 3.4722222e-05 [4.2050499e-06, 0.00012542292] | 1198/1200 |
| lighting:on | retrained_host | 3/57600 | 5.2083333e-05 [1.0740964e-05, 0.00015220198] | 1197/1200 |
| distance_m:1 | classic | 3/28800 | 0.00010416667 [2.1482186e-05, 0.00030438872] | 597/600 |
| distance_m:1 | aug_cpu | 2/28800 | 6.9444444e-05 [8.410155e-06, 0.00025083446] | 598/600 |
| distance_m:1 | aug_npu | 2/28800 | 6.9444444e-05 [8.410155e-06, 0.00025083446] | 598/600 |
| distance_m:1 | aug_host | 2/28800 | 6.9444444e-05 [8.410155e-06, 0.00025083446] | 598/600 |
| distance_m:1 | retrained_host | 3/28800 | 0.00010416667 [2.1482186e-05, 0.00030438872] | 597/600 |
| distance_m:3 | classic | 0/28800 | 0 [0, 0.00012807789] | 600/600 |
| distance_m:3 | aug_cpu | 0/28800 | 0 [0, 0.00012807789] | 600/600 |
| distance_m:3 | aug_npu | 0/28800 | 0 [0, 0.00012807789] | 600/600 |
| distance_m:3 | aug_host | 0/28800 | 0 [0, 0.00012807789] | 600/600 |
| distance_m:3 | retrained_host | 0/28800 | 0 [0, 0.00012807789] | 600/600 |
| distance_m:5 | classic | 0/28800 | 0 [0, 0.00012807789] | 600/600 |
| distance_m:5 | aug_cpu | 0/28800 | 0 [0, 0.00012807789] | 600/600 |
| distance_m:5 | aug_npu | 0/28800 | 0 [0, 0.00012807789] | 600/600 |
| distance_m:5 | aug_host | 0/28800 | 0 [0, 0.00012807789] | 600/600 |
| distance_m:5 | retrained_host | 0/28800 | 0 [0, 0.00012807789] | 600/600 |
| distance_m:7 | classic | 0/28800 | 0 [0, 0.00012807789] | 600/600 |
| distance_m:7 | aug_cpu | 0/28800 | 0 [0, 0.00012807789] | 600/600 |
| distance_m:7 | aug_npu | 0/28800 | 0 [0, 0.00012807789] | 600/600 |
| distance_m:7 | aug_host | 0/28800 | 0 [0, 0.00012807789] | 600/600 |
| distance_m:7 | retrained_host | 0/28800 | 0 [0, 0.00012807789] | 600/600 |

## Polarity and host kernels

Polarity: preamble. Flips use decoded 1010/0101 distances only; ties stay direct.
Host models use BUILTIN_WITHOUT_DEFAULT_DELEGATES, num_threads=1. Diagnostic disagreements are before polarity correction.

| Group | Decoder | Flipped windows | Reference vs XNNPACK bit disagreements |
|---|---|---:|---:|
| 7m_on | classic | 0 | unavailable |
| 7m_on | aug_cpu | 0 | unavailable |
| 7m_on | aug_npu | 0 | unavailable |
| 7m_on | aug_host | 0 | 0 |
| 7m_on | retrained_host | 0 | 0 |
| 7m_off | classic | 0 | unavailable |
| 7m_off | aug_cpu | 0 | unavailable |
| 7m_off | aug_npu | 0 | unavailable |
| 7m_off | aug_host | 0 | 0 |
| 7m_off | retrained_host | 0 | 0 |
| 5m_on | classic | 0 | unavailable |
| 5m_on | aug_cpu | 0 | unavailable |
| 5m_on | aug_npu | 0 | unavailable |
| 5m_on | aug_host | 0 | 0 |
| 5m_on | retrained_host | 0 | 0 |
| 5m_off | classic | 0 | unavailable |
| 5m_off | aug_cpu | 0 | unavailable |
| 5m_off | aug_npu | 0 | unavailable |
| 5m_off | aug_host | 0 | 0 |
| 5m_off | retrained_host | 0 | 0 |
| 3m_off | classic | 0 | unavailable |
| 3m_off | aug_cpu | 0 | unavailable |
| 3m_off | aug_npu | 0 | unavailable |
| 3m_off | aug_host | 0 | 0 |
| 3m_off | retrained_host | 0 | 0 |
| 3m_on | classic | 300 | unavailable |
| 3m_on | aug_cpu | 300 | unavailable |
| 3m_on | aug_npu | 300 | unavailable |
| 3m_on | aug_host | 300 | 0 |
| 3m_on | retrained_host | 300 | 0 |
| 1m_off | classic | 0 | unavailable |
| 1m_off | aug_cpu | 0 | unavailable |
| 1m_off | aug_npu | 0 | unavailable |
| 1m_off | aug_host | 0 | 0 |
| 1m_off | retrained_host | 0 | 0 |
| 1m_on | classic | 0 | unavailable |
| 1m_on | aug_cpu | 0 | unavailable |
| 1m_on | aug_npu | 0 | unavailable |
| 1m_on | aug_host | 0 | 0 |
| 1m_on | retrained_host | 0 | 0 |
| all | classic | 300 | unavailable |
| all | aug_cpu | 300 | unavailable |
| all | aug_npu | 300 | unavailable |
| all | aug_host | 300 | 0 |
| all | retrained_host | 300 | 0 |
| lighting:off | classic | 0 | unavailable |
| lighting:off | aug_cpu | 0 | unavailable |
| lighting:off | aug_npu | 0 | unavailable |
| lighting:off | aug_host | 0 | 0 |
| lighting:off | retrained_host | 0 | 0 |
| lighting:on | classic | 300 | unavailable |
| lighting:on | aug_cpu | 300 | unavailable |
| lighting:on | aug_npu | 300 | unavailable |
| lighting:on | aug_host | 300 | 0 |
| lighting:on | retrained_host | 300 | 0 |
| distance_m:1 | classic | 0 | unavailable |
| distance_m:1 | aug_cpu | 0 | unavailable |
| distance_m:1 | aug_npu | 0 | unavailable |
| distance_m:1 | aug_host | 0 | 0 |
| distance_m:1 | retrained_host | 0 | 0 |
| distance_m:3 | classic | 300 | unavailable |
| distance_m:3 | aug_cpu | 300 | unavailable |
| distance_m:3 | aug_npu | 300 | unavailable |
| distance_m:3 | aug_host | 300 | 0 |
| distance_m:3 | retrained_host | 300 | 0 |
| distance_m:5 | classic | 0 | unavailable |
| distance_m:5 | aug_cpu | 0 | unavailable |
| distance_m:5 | aug_npu | 0 | unavailable |
| distance_m:5 | aug_host | 0 | 0 |
| distance_m:5 | retrained_host | 0 | 0 |
| distance_m:7 | classic | 0 | unavailable |
| distance_m:7 | aug_cpu | 0 | unavailable |
| distance_m:7 | aug_npu | 0 | unavailable |
| distance_m:7 | aug_host | 0 | 0 |
| distance_m:7 | retrained_host | 0 | 0 |

## Payload audit

{
  "training_words": 100,
  "windows": 2400,
  "distinct_words": 100,
  "overlap_count": 0,
  "distinct_overlap_count": 0,
  "overlap_fraction": 0.0,
  "nearest_hamming_min": 8,
  "nearest_hamming_mean": 10.56,
  "hamming_weighting": "one observation per included window",
  "passed": true
}

Unseen-payload requirement: True. Old-seed controls are expected to overlap 100%.

## Board agreement

Threshold disagreements and raw equality (per condition, session and pooled) are recorded in summary.json.

## Per-session tracker audit

| Session | Gain median | Weak fit | Fallback | Phase boundary | Missing gain / tracker / phase |
|---|---:|---:|---:|---:|---|
| goal2_replay_gray8v2_phasev1_fresh_seed20261199_7m_roi233_106_14_15_exp2000_lights_on_20261109_r2_100f_s01 | 7.589888451580743 | 8 | 0 | 12 | 0/0/0 |
| goal2_replay_gray8v2_phasev1_fresh_seed20261199_7m_roi233_106_14_15_exp2000_lights_on_20261109_r2_100f_s02 | 8.084698848146607 | 5 | 0 | 17 | 0/0/0 |
| goal2_replay_gray8v2_phasev1_fresh_seed20261199_7m_roi233_106_14_15_exp2000_lights_on_20261109_r2_100f_s03 | 8.882999672497782 | 0 | 0 | 11 | 0/0/0 |
| goal2_replay_gray8v2_phasev1_fresh_seed20261199_7m_roi233_106_14_15_exp2000_lights_off_20261109_r2_100f_s01 | 40.46592199594718 | 0 | 0 | 5 | 0/0/0 |
| goal2_replay_gray8v2_phasev1_fresh_seed20261199_7m_roi233_106_14_15_exp2000_lights_off_20261109_r2_100f_s02 | 40.0561732372634 | 0 | 0 | 3 | 0/0/0 |
| goal2_replay_gray8v2_phasev1_fresh_seed20261199_7m_roi233_106_14_15_exp2000_lights_off_20261109_r2_100f_s03 | 39.7715527833779 | 0 | 0 | 15 | 0/0/0 |
| goal2_replay_gray8v2_phasev1_fresh_seed20261199_5m_roi116_102_25_28_exp2000_lights_on_20261110_100f_s01 | 10.887873469933506 | 0 | 0 | 5 | 0/0/0 |
| goal2_replay_gray8v2_phasev1_fresh_seed20261199_5m_roi116_102_25_28_exp2000_lights_on_20261110_100f_s02 | 10.507810345855784 | 0 | 0 | 16 | 0/0/0 |
| goal2_replay_gray8v2_phasev1_fresh_seed20261199_5m_roi116_102_25_28_exp2000_lights_on_20261110_100f_s03 | 9.838764378836704 | 0 | 0 | 17 | 0/0/0 |
| goal2_replay_gray8v2_phasev1_fresh_seed20261199_5m_roi116_102_25_28_exp2000_lights_off_20261110_100f_s01 | 60.797412899771345 | 0 | 0 | 23 | 0/0/0 |
| goal2_replay_gray8v2_phasev1_fresh_seed20261199_5m_roi116_102_25_28_exp2000_lights_off_20261110_100f_s02 | 60.54171849645954 | 0 | 0 | 17 | 0/0/0 |
| goal2_replay_gray8v2_phasev1_fresh_seed20261199_5m_roi116_102_25_28_exp2000_lights_off_20261110_100f_s03 | 60.55854624936758 | 0 | 0 | 20 | 0/0/0 |
| goal2_replay_gray8v2_phasev1_fresh_seed20261199_3m_roi149_87_32_26_exp2000_lights_off_20261110_100f_s01 | 24.730148606045866 | 0 | 0 | 19 | 0/0/0 |
| goal2_replay_gray8v2_phasev1_fresh_seed20261199_3m_roi149_87_32_26_exp2000_lights_off_20261110_100f_s02 | 24.877246561397655 | 0 | 0 | 16 | 0/0/0 |
| goal2_replay_gray8v2_phasev1_fresh_seed20261199_3m_roi149_87_32_26_exp2000_lights_off_20261110_100f_s03 | 24.36560851209117 | 0 | 0 | 5 | 0/0/0 |
| goal2_replay_gray8v2_phasev1_fresh_seed20261199_3m_roi149_87_32_26_exp2000_lights_on_20261110_100f_s01 | -4.297124804366407 | 1 | 0 | 28 | 0/0/0 |
| goal2_replay_gray8v2_phasev1_fresh_seed20261199_3m_roi149_87_32_26_exp2000_lights_on_20261110_100f_s02 | -4.286644979327787 | 5 | 0 | 28 | 0/0/0 |
| goal2_replay_gray8v2_phasev1_fresh_seed20261199_3m_roi149_87_32_26_exp2000_lights_on_20261110_100f_s03 | -4.318736938339104 | 9 | 0 | 32 | 0/0/0 |
| goal2_replay_gray8v2_phasev1_fresh_seed20261199_1m_roi152_127_71_65_exp2000_lights_off_20261110_100f_s01 | 48.76826621486771 | 0 | 0 | 22 | 0/0/0 |
| goal2_replay_gray8v2_phasev1_fresh_seed20261199_1m_roi152_127_71_65_exp2000_lights_off_20261110_100f_s02 | 48.775502794355006 | 0 | 0 | 19 | 0/0/0 |
| goal2_replay_gray8v2_phasev1_fresh_seed20261199_1m_roi152_127_71_65_exp2000_lights_off_20261110_100f_s03 | 48.61790145338128 | 0 | 0 | 14 | 0/0/0 |
| goal2_replay_gray8v2_phasev1_fresh_seed20261199_1m_roi152_127_71_65_exp2000_lights_on_20261110_100f_s01 | 5.192084943586908 | 0 | 0 | 10 | 0/0/0 |
| goal2_replay_gray8v2_phasev1_fresh_seed20261199_1m_roi152_127_71_65_exp2000_lights_on_20261110_100f_s02 | 3.814142843110301 | 4 | 0 | 12 | 0/0/0 |
| goal2_replay_gray8v2_phasev1_fresh_seed20261199_1m_roi152_127_71_65_exp2000_lights_on_20261110_100f_s03 | 3.2224717167911345 | 7 | 0 | 11 | 0/0/0 |

Gain uses signed_optical_gain, falling back to tracker.gain, as in eval_controls.py.

## Controls reproduction

Not requested.

| Group | Decoder | Dry-run errors / bits | Controls errors / bits | Dry-run exact | Controls exact | Match |
|---|---|---:|---:|---:|---:|---|

No serial/USB or firmware execution is performed by this scorer. Board verification requires hardware.
