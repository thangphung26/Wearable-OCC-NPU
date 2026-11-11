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
| wide_off | classic | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| wide_off | aug_cpu | unavailable | unavailable | unavailable |
| wide_off | aug_npu | unavailable | unavailable | unavailable |
| wide_off | aug_host | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| wide_off | retrained_host | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| wide_on | classic | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| wide_on | aug_cpu | unavailable | unavailable | unavailable |
| wide_on | aug_npu | unavailable | unavailable | unavailable |
| wide_on | aug_host | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| wide_on | retrained_host | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| tight_on_primary | classic | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| tight_on_primary | aug_cpu | unavailable | unavailable | unavailable |
| tight_on_primary | aug_npu | unavailable | unavailable | unavailable |
| tight_on_primary | aug_host | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| tight_on_primary | retrained_host | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| all | classic | 0/43200 | 0 [0, 8.5387082e-05] | 900/900 |
| all | aug_cpu | unavailable | unavailable | unavailable |
| all | aug_npu | unavailable | unavailable | unavailable |
| all | aug_host | 0/43200 | 0 [0, 8.5387082e-05] | 900/900 |
| all | retrained_host | 0/43200 | 0 [0, 8.5387082e-05] | 900/900 |
| lighting:off | classic | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| lighting:off | aug_cpu | unavailable | unavailable | unavailable |
| lighting:off | aug_npu | unavailable | unavailable | unavailable |
| lighting:off | aug_host | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| lighting:off | retrained_host | 0/14400 | 0 [0, 0.00025613938] | 300/300 |
| lighting:on | classic | 0/28800 | 0 [0, 0.00012807789] | 600/600 |
| lighting:on | aug_cpu | unavailable | unavailable | unavailable |
| lighting:on | aug_npu | unavailable | unavailable | unavailable |
| lighting:on | aug_host | 0/28800 | 0 [0, 0.00012807789] | 600/600 |
| lighting:on | retrained_host | 0/28800 | 0 [0, 0.00012807789] | 600/600 |
| distance_m:3 | classic | 0/43200 | 0 [0, 8.5387082e-05] | 900/900 |
| distance_m:3 | aug_cpu | unavailable | unavailable | unavailable |
| distance_m:3 | aug_npu | unavailable | unavailable | unavailable |
| distance_m:3 | aug_host | 0/43200 | 0 [0, 8.5387082e-05] | 900/900 |
| distance_m:3 | retrained_host | 0/43200 | 0 [0, 8.5387082e-05] | 900/900 |

## Polarity and host kernels

Polarity: direct. Flips use decoded 1010/0101 distances only; ties stay direct.
Host models use BUILTIN_WITHOUT_DEFAULT_DELEGATES, num_threads=1. Diagnostic disagreements are before polarity correction.

| Group | Decoder | Flipped windows | Reference vs XNNPACK bit disagreements |
|---|---|---:|---:|
| wide_off | classic | 0 | unavailable |
| wide_off | aug_cpu | None | unavailable |
| wide_off | aug_npu | None | unavailable |
| wide_off | aug_host | 0 | 0 |
| wide_off | retrained_host | 0 | 0 |
| wide_on | classic | 0 | unavailable |
| wide_on | aug_cpu | None | unavailable |
| wide_on | aug_npu | None | unavailable |
| wide_on | aug_host | 0 | 0 |
| wide_on | retrained_host | 0 | 0 |
| tight_on_primary | classic | 0 | unavailable |
| tight_on_primary | aug_cpu | None | unavailable |
| tight_on_primary | aug_npu | None | unavailable |
| tight_on_primary | aug_host | 0 | 0 |
| tight_on_primary | retrained_host | 0 | 0 |
| all | classic | 0 | unavailable |
| all | aug_cpu | None | unavailable |
| all | aug_npu | None | unavailable |
| all | aug_host | 0 | 0 |
| all | retrained_host | 0 | 0 |
| lighting:off | classic | 0 | unavailable |
| lighting:off | aug_cpu | None | unavailable |
| lighting:off | aug_npu | None | unavailable |
| lighting:off | aug_host | 0 | 0 |
| lighting:off | retrained_host | 0 | 0 |
| lighting:on | classic | 0 | unavailable |
| lighting:on | aug_cpu | None | unavailable |
| lighting:on | aug_npu | None | unavailable |
| lighting:on | aug_host | 0 | 0 |
| lighting:on | retrained_host | 0 | 0 |
| distance_m:3 | classic | 0 | unavailable |
| distance_m:3 | aug_cpu | None | unavailable |
| distance_m:3 | aug_npu | None | unavailable |
| distance_m:3 | aug_host | 0 | 0 |
| distance_m:3 | retrained_host | 0 | 0 |

## Payload audit

{
  "training_words": 100,
  "windows": 900,
  "distinct_words": 100,
  "overlap_count": 900,
  "distinct_overlap_count": 100,
  "overlap_fraction": 1.0,
  "nearest_hamming_min": 0,
  "nearest_hamming_mean": 0.0,
  "hamming_weighting": "one observation per included window",
  "passed": false
}

Unseen-payload requirement: False. Old-seed controls are expected to overlap 100%.

## Board agreement

Threshold disagreements and raw equality (per condition, session and pooled) are recorded in summary.json.

## Per-session tracker audit

| Session | Gain median | Weak fit | Fallback | Phase boundary | Missing gain / tracker / phase |
|---|---:|---:|---:|---:|---|
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107_100f_s01 | 97.27949286922848 | 0 | 0 | 17 | 0/0/0 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107_100f_s02 | 96.48321304353317 | 0 | 0 | 17 | 0/0/0 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107_100f_s03 | 94.80114488686948 | 0 | 0 | 0 | 0/0/0 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s01 | 9.510830893018579 | 0 | 0 | 7 | 0/0/0 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s02 | 10.797704035111813 | 0 | 0 | 5 | 0/0/0 |
| goal2_replay_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108_100f_s03 | 10.243834980551862 | 0 | 0 | 5 | 0/0/0 |
| goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s01 | 149.01498804557724 | 0 | 0 | 19 | 0/0/0 |
| goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s04 | 134.00451591066076 | 0 | 0 | 18 | 0/0/0 |
| goal2_replay_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108_100f_s03 | 152.44143845426328 | 0 | 0 | 4 | 0/0/0 |

Gain uses signed_optical_gain, falling back to tracker.gain, as in eval_controls.py.

## Controls reproduction

36/36 exact metric-dictionary comparisons passed (group and session; includes CP intervals).

| Group | Decoder | Dry-run errors / bits | Controls errors / bits | Dry-run exact | Controls exact | Match |
|---|---|---:|---:|---:|---:|---|
| wide_off | classic | 0/14400 | 0/14400 | 300/300 | 300/300 | True |
| wide_off | retrained_host | 0/14400 | 0/14400 | 300/300 | 300/300 | True |
| wide_off | aug_host | 0/14400 | 0/14400 | 300/300 | 300/300 | True |
| wide_on | classic | 0/14400 | 0/14400 | 300/300 | 300/300 | True |
| wide_on | retrained_host | 0/14400 | 0/14400 | 300/300 | 300/300 | True |
| wide_on | aug_host | 0/14400 | 0/14400 | 300/300 | 300/300 | True |
| tight_on_primary | classic | 0/14400 | 0/14400 | 300/300 | 300/300 | True |
| tight_on_primary | retrained_host | 0/14400 | 0/14400 | 300/300 | 300/300 | True |
| tight_on_primary | aug_host | 0/14400 | 0/14400 | 300/300 | 300/300 | True |

No serial/USB or firmware execution is performed by this scorer. Board verification requires hardware.
