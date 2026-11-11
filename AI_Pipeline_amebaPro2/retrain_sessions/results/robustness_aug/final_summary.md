# Augmented evaluation

Unperturbed: errors [95% CP BER interval ×10⁻⁵].

| Split | Range | Bits | Classic | Old INT8 | New INT8 | Aug INT8 |
|---|---|---:|---:|---:|---:|---:|
| repeat | 1–7 m | 38400 | 9 [10.72, 44.49] | 34 [61.33, 123.71] | 7 [7.33, 37.56] | 9 [10.72, 44.49] |
| repeat | 1–5 m | 28800 | 6 [7.65, 45.34] | 12 [21.53, 72.77] | 6 [7.65, 45.34] | 6 [7.65, 45.34] |
| loso | 1–7 m | 115200 | 12 [5.38, 18.20] | 70 [47.37, 76.77] | 16 [7.94, 22.55] | 9 [3.57, 14.83] |
| loso | 1–5 m | 86400 | 9 [4.76, 19.77] | 26 [19.66, 44.09] | 10 [5.55, 21.28] | 8 [4.00, 18.24] |

Robustness entries: errors all / 1–5 m. Timing bits: 38,400 / 28,800. Contrast/SNR bits: 192,000 / 144,000 (five realizations).

## timing_offset

| Point | Classic | Old INT8 | New INT8 | Aug INT8 |
|---:|---:|---:|---:|---:|
| -40 | 362/153 | 1226/732 | 3607/2662 | 18/4 |
| -30 | 59/22 | 98/36 | 95/42 | 8/5 |
| -20 | 13/6 | 33/10 | 20/10 | 8/6 |
| -10 | 8/6 | 20/8 | 7/6 | 7/6 |
| 0 | 9/6 | 35/12 | 7/6 | 9/6 |
| 10 | 9/6 | 154/93 | 12/6 | 7/6 |
| 20 | 24/6 | 959/680 | 17/7 | 7/6 |
| 30 | 75/7 | 3548/2621 | 77/14 | 7/6 |
| 40 | 362/88 | 7185/5315 | 1168/737 | 24/7 |

## contrast

| Point | Classic | Old INT8 | New INT8 | Aug INT8 |
|---:|---:|---:|---:|---:|
| 1 | 58/26 | 402/108 | 53/24 | 64/28 |
| 0.5 | 498/75 | 1981/715 | 584/92 | 484/59 |
| 0.25 | 6155/2744 | 11592/6193 | 7103/3272 | 6163/2725 |
| 0.1 | 38610/26227 | 46266/32131 | 40367/27499 | 37617/25565 |

## snr

| Point | Classic | Old INT8 | New INT8 | Aug INT8 |
|---:|---:|---:|---:|---:|
| 20 | 38/25 | 163/53 | 32/26 | 39/26 |
| 15 | 41/27 | 201/71 | 37/26 | 45/30 |
| 10 | 53/29 | 432/220 | 45/26 | 56/28 |
| 6 | 137/38 | 1501/910 | 162/58 | 121/34 |
| 3 | 932/521 | 4307/2921 | 1207/700 | 943/529 |
| 0 | 5201/3625 | 11784/8421 | 6144/4310 | 5309/3696 |

## Verification

Runtime: repeat 108.03 s; LOSO 1118.39 s; robustness 20.38 s; task wall time approximately 21.5 min.

Tests: 4 + 6 + 5 = 15/15. Completed training runs: 3 repeat seeds + 24 LOSO folds. Cache sanity: 2400/2400 INT8 windows, 115200/115200 Classic bits. Robustness sanity: 800/800 windows, 38400/38400 Classic bits. Minimum FP32/INT8 agreement: repeat 99.9974%; LOSO 100%. Baseline files unchanged: 51/51. Robustness CSV audit: 47200 windows, 19 points, 4 series; original 3 series exactly reproduced.

Stored old CPU output is used in unperturbed summaries (repeat: 34 errors); paired offline inference is used in robustness (clean repeat: 35 errors). Nominal CP intervals pool correlated captured bits; noise realizations reuse those captures.

## Execution record

New source/docs: `export_offsets.py`, `train_eval_aug.py`, `README_aug.md`. Modified: `robustness.py` (optional all-session replay and four-model evaluation/plot flags). `train_eval.py` is unchanged. New cache: `dataset/retrain_sessions/windows_offsets.npz`. New result directories: `repeat_aug/`, `loso_aug/`, `robustness_aug/`.

Commands from repository root: `.venv/bin/python` with `export_offsets.py`, `train_eval_aug.py repeat`, `train_eval_aug.py loso`, and `robustness.py --aug` (scripts under `1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/`). Tests: `.venv/bin/python -m unittest discover -s 1_VLC_code_main/AI_Pipeline_amebaPro2 -p <module>.py` for `test_precrc`, `test_manchester_phase`, and `test_preamble_tracker`. Syntax compilation and cache/result/hash audits also passed.

Open execution issues: 0. Physical deployment/timing/noise behavior needs hardware verification; no firmware or hardware was changed or run. No raw captures, benchmark data, paper files, or existing results were changed.
