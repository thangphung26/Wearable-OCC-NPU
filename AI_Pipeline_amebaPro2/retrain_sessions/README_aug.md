# Augmented session training

Run from the repository root using the root virtual environment:

1. `.venv/bin/python 1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/export_offsets.py`
2. `.venv/bin/python 1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/train_eval_aug.py repeat`
3. `.venv/bin/python 1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/train_eval_aug.py loso`
4. `.venv/bin/python 1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/robustness.py --aug`

The cache contains float64 raw-luma windows for all 24 sessions at offsets −40 through +40 ms in 10 ms steps. Windows use the original sampling, alignment, refinement and sanity checks. Session/frame identity and truth are checked against `dataset.npz`. No raw capture is changed. Acquisition is 30 FPS; resampling is 20 Hz; the link rate is 5 bit/s.

Each training window independently receives a nonzero uniformly selected timing offset with probability 0.5, contrast uniformly distributed from 0.25 to 1 plus session-residual-std Gaussian noise with probability 0.3, and Gaussian noise with uniform 3–20 dB SNR with probability 0.3. These three events are independent and can compose in that order. The SNR amplitude is measured on the timing-selected window before added contrast/noise. Session residual std and half-bit amplitude follow the estimators in `results/robustness/README.md`. An epoch/batch/seed-keyed RNG generates augmentation on demand. Exact per-bit normalization, including the 0.5-luma cutoff and round-half-up INT8 quantization, precedes dequantization to the training float input.

Validation uses clean whole sessions, every tenth eligible session in the original order. Test, training and validation sessions are disjoint. Calibration uses the first 200 clean training windows, as in the baseline; it excludes validation and test sessions. Architecture, optimizer, class weights, seeds, maximum epochs and early stopping are imported unchanged from `train_eval.py`. Repeat uses three seeds and chooses the median minimum-validation-loss seed. LOSO uses seed 42 per fold. Candidate files are `results/repeat_aug/deployment_candidate_aug.keras`, `_aug_float32.h5`, and `_aug_int8.tflite`. INT8/FP32 test agreement must be at least 99.9% for every seed/fold and the exported candidate.

The augmented output directories contain config, history, frame-level error positions, exact windows, pooled/subset/per-condition CP intervals and agreement records. LOSO checkpoints metrics after each completed fold; `complete` must be true for a 24-fold result. Baseline `old_int8` unperturbed tables use stored CPU outputs for compatibility, whereas robustness runs all models offline on identical perturbed inputs (see the existing one-bit baseline parity caveat).

`results/robustness_aug/` uses the original robustness grid and five paired noise draws with all four decoders. Both candidate training histories are checked against the eight held-out test sessions. Each 3.5-inch-wide PNG/PDF contains vertically stacked all-distance and 1–5 m panels with log-y BER and 95% Clopper–Pearson error bars. Open markers show zero-error points at the upper confidence bound. Pooled noise counts use repeated evaluations of the same captured windows; their binomial intervals are not independent-capture confidence intervals. Model deployment and physical timing/noise behavior require hardware verification.

## Corrected 7 m run (opt-in)

Set both environment variables for every export/evaluation command:

```bash
export RETRAIN_OUTPUT_ROOT="$PWD/1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/results_v2"
export RETRAIN_SESSION_SET=corrected-7m
```

With these settings, exports use `results_v2/data/dataset.npz` and
`results_v2/data/windows_offsets.npz`; models, summaries, caches and plots use
`results_v2/`. With neither variable set, the original paths and legacy session
selection remain the defaults. The corrected selection replaces the six 7 m rows
in place, using `replay_sessions` and paired `capture_sessions` from the
`lights_on_20261107` and `lights_off_20261107_r2` protocol locks (ROI
162,90,14,15, exposure 2000). All corrected sessions are `jpeg-gray8-v2`.
The failed lights-off run without `_r2` is never selected. Session order, repeat
indices and all training/evaluation methods remain unchanged.

Run both exports in the original order (`export_dataset.py`, `export_offsets.py`),
then `export_dataset.py --check-only` and `robustness.py --sanity-only`.
`train_eval.py loso --smoke` runs only the first fold, seed 42, three epochs,
with the normal calibration and agreement checks, writing `results_v2/smoke/`.
This is a path/contract check, not a complete LOSO result.

After paired export and sanity checks, run the public entry points separately:
`train_eval.py repeat`, `train_eval.py loso`, `train_eval.py lodo`,
`train_eval_aug.py repeat`, `train_eval_aug.py loso`, `train_eval_aug.py lodo`,
`robustness.py`, `robustness.py --aug`, and `robustness_offgrid.py`.
Prefix each with the root `.venv/bin/python` and this directory's workspace-relative
path. Keep both environment variables set for all commands. Preserve existing
outputs before rerunning writers.

The final model is AUG v2 in `results_v2/repeat_aug/`; the model without
augmentation is an ablation. For conversion and board replay, use the v2 section
of [the AUG runbook](../ameba_deployment/aug/RUNBOOK.md). The independent test is
recorded in `results_v2/fresh/campaign_board_v2/`; its recordings are excluded
from the code repository and available on request. Hardware reproduction must
verify model identity, CPU/NPU parity, accuracy and timing.
