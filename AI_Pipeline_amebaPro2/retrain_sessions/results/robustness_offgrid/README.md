# Off-grid timing robustness

Run from repository root: `.venv/bin/python 1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/robustness_offgrid.py`.

CPU is forced with `CUDA_VISIBLE_DEVICES=""` before TensorFlow import. No training occurs. The script refuses to overwrite its output directory. Existing results and raw datasets remain unchanged.

The imported `robustness.regenerate(all_sessions=True)` reuses coarse alignment, phase refinement, float64 replay sampling and `benchmark_precrc.normalize`. Each residual offset is added after alignment/refinement; alignment is not repeated. Classic and all CNNs receive the same perturbed window, before normalization/INT8 quantization. Acquisition is 30 FPS; resampling is 20 Hz; link rate is 5 bit/s.

Sanity: 2,400/2,400 normalized INT8 frames and 115,200/115,200 Classic bits exactly match stored replay data. Session/frame identity and truth also match the exported dataset. Every repeat history and LOSO fold asserts test disjointness from training, validation, and calibration. Baseline calibration uses training frames as specified in train_eval.py; augmented calibration sessions are explicit in its histories. The old deployed model is the fixed historical comparator; its original training provenance is not reconstructed here.

Repeat evaluates eight repeat-3 sessions: 38,400 bits per offset (1–5 m: 28,800). LOSO evaluates each of 24 retained fold INT8 files only on its held-out session and pools integer counts: 115,200 bits per offset (1–5 m: 86,400). Seven-metre legacy preprocessing is retained in CSV. Per-fold summaries are included; 7 m folds have an empty 1–5 m subset represented by zero counts and null BER/CI.

`results.csv` records 20,000 evaluated windows with error positions and exact-window flags; non-applicable old/new non-aug LOSO fields are blank. `summary.json` reports both subsets, each fold, pooled counts, BER, and two-sided 95% Clopper–Pearson intervals. Intervals use nominal binomial counts; temporally correlated captured bits and reuse across offsets/folds do not create independent capture evidence. `config.json` records split checks, model/input hashes and rates.

`ber_vs_timing_offset_full.pdf/png`: four series for repeat-3, combining the nine prior on-grid points from `../robustness_aug/summary.json` with ten new off-grid points. Prior model hashes and shared input hashes are verified before reuse. Width is 3.5 inches, PNG is 300 dpi, y is logarithmic; upper panel 1–7 m, lower panel 1–5 m. Bars are 95% CP intervals. Zero-error points use open markers at the CP upper bound; lines are guides between tested offsets.

## Repeat-3 off-grid, all_sessions

| Offset (ms) | classic | old_int8 | new_int8 | new_aug_int8 |
|---:|---:|---:|---:|---:|
| -45 | 1590 / 38,400 | 3906 / 38,400 | 7406 / 38,400 | 30 / 38,400 |
| -35 | 137 / 38,400 | 334 / 38,400 | 575 / 38,400 | 10 / 38,400 |
| -25 | 25 / 38,400 | 45 / 38,400 | 44 / 38,400 | 7 / 38,400 |
| -15 | 10 / 38,400 | 18 / 38,400 | 10 / 38,400 | 8 / 38,400 |
| -5 | 9 / 38,400 | 19 / 38,400 | 7 / 38,400 | 6 / 38,400 |
| 5 | 8 / 38,400 | 64 / 38,400 | 9 / 38,400 | 8 / 38,400 |
| 15 | 13 / 38,400 | 408 / 38,400 | 12 / 38,400 | 7 / 38,400 |
| 25 | 36 / 38,400 | 1944 / 38,400 | 34 / 38,400 | 7 / 38,400 |
| 35 | 156 / 38,400 | 5567 / 38,400 | 259 / 38,400 | 15 / 38,400 |
| 45 | 1430 / 38,400 | 8561 / 38,400 | 3013 / 38,400 | 41 / 38,400 |

## Repeat-3 off-grid, 1_5m

| Offset (ms) | classic | old_int8 | new_int8 | new_aug_int8 |
|---:|---:|---:|---:|---:|
| -45 | 741 / 28,800 | 2856 / 28,800 | 5564 / 28,800 | 6 / 28,800 |
| -35 | 63 / 28,800 | 177 / 28,800 | 315 / 28,800 | 4 / 28,800 |
| -25 | 6 / 28,800 | 17 / 28,800 | 22 / 28,800 | 6 / 28,800 |
| -15 | 6 / 28,800 | 7 / 28,800 | 6 / 28,800 | 6 / 28,800 |
| -5 | 6 / 28,800 | 7 / 28,800 | 6 / 28,800 | 6 / 28,800 |
| 5 | 6 / 28,800 | 25 / 28,800 | 6 / 28,800 | 6 / 28,800 |
| 15 | 6 / 28,800 | 281 / 28,800 | 6 / 28,800 | 6 / 28,800 |
| 25 | 6 / 28,800 | 1408 / 28,800 | 7 / 28,800 | 6 / 28,800 |
| 35 | 30 / 28,800 | 4149 / 28,800 | 103 / 28,800 | 6 / 28,800 |
| 45 | 607 / 28,800 | 6318 / 28,800 | 2066 / 28,800 | 14 / 28,800 |

## Pooled LOSO augmented, all_sessions

| Offset (ms) | classic | new_aug_int8 |
|---:|---:|---:|
| -40 | 1014 / 115,200 | 13 / 115,200 |
| -25 | 59 / 115,200 | 6 / 115,200 |
| 0 | 12 / 115,200 | 9 / 115,200 |
| 25 | 67 / 115,200 | 11 / 115,200 |
| 40 | 957 / 115,200 | 59 / 115,200 |

## Pooled LOSO augmented, 1_5m

| Offset (ms) | classic | new_aug_int8 |
|---:|---:|---:|
| -40 | 389 / 86,400 | 5 / 86,400 |
| -25 | 15 / 86,400 | 6 / 86,400 |
| 0 | 9 / 86,400 | 8 / 86,400 |
| 25 | 10 / 86,400 | 8 / 86,400 |
| 40 | 269 / 86,400 | 9 / 86,400 |

Evaluation runtime including regeneration and plotting: 12.680 s.

Verification: all 20,000 unique frame/offset rows audited against pooled errors, denominators, exact-window counts and independently recomputed CP intervals; input/model hashes verified; LOSO timing-zero counts match the prior unperturbed results. All 15 tests pass using unittest: test_precrc (4), test_manchester_phase (6), test_preamble_tracker (5).

Offline analysis only. Physical timing behavior and deployment still require hardware verification. No firmware or paper edits.
