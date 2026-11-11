# Session retraining

Run from the repository root with `.venv/bin/python`.

1. Export paired frames: `.../retrain_sessions/export_dataset.py`; use `--check-only` to verify the existing NPZ without rewriting it.
2. Train/evaluate: `.../retrain_sessions/train_eval.py repeat|loso|lodo`.
3. Plot repeat results: `.../retrain_sessions/plots.py`.

`repeat` trains on repeats 1–2; repeat 3 is held out. Validation uses every tenth eligible session, selected whole by session order and excluded from fit/calibration. Three seeds run; the median validation-loss seed is exported as deployment candidate.

`loso` leaves one of 24 sessions out per fold. `lodo` leaves one distance out per fold. Both use seed 42 once per fold (runtime budget). Each fold excludes validation sessions from fit/calibration; INT8 calibration uses training frames only.

The NPZ keeps 7 m legacy-gray provenance and all conditions. Reports include all distances and the 1–5 m subset.

INT8 input is `[1,48,1,4]`, scale 1/255, zero point -128. Decode output using its own scale/zero point and threshold at probability >= 0.5; FP32/INT8 agreement must be >= 99.9%. Logs print both models' quantization and each fold's agreement. CSV rows identify session/frame and error bit positions (0–47); summaries count exact 48-bit windows.

Candidate files are in `results/repeat/`: `deployment_candidate.keras`, `deployment_candidate_float32.h5`, and `deployment_candidate_int8.tflite`. Give the INT8 TFLite model and its input/output quantization details to the Acuity Toolkit/NBG conversion flow, then verify tensor layout, operators, quantization, and decoding on AmebaPro2 hardware. No board compilation occurs in this offline workflow.

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
