# Augmented CNN: conversion and board evaluation

Execute each command below separately from
`/home/usnlab/Desktop/Job_ngon_UWC`, using the root `.venv`.
Use the v2 section below for the final model. The preceding workflow records
legacy conversion provenance; some legacy intermediate outputs are excluded.
No original receiver, benchmark, library, model, or dataset is replaced.
Only repeat-3 (800 windows, eight sessions) is held out for this model.
Acquisition is 30 FPS, resampling 20 Hz, and the optical link 5 bit/s.

Model siblings are `deployment_candidate_aug.keras`,
`deployment_candidate_aug_float32.h5`, and
`deployment_candidate_aug_int8.tflite` under
`AI_Pipeline_amebaPro2/retrain_sessions/results/repeat_aug/`.
INT8 SHA-256:
`f1248c13ba5e6e5a3e7225d19a72239b6aefe932abc435a140e15dd26890e586`.
Its input is signed INT8 NHWC [1,48,1,4], scale 1/255, zero -128.
Output is signed INT8 [1,48,1], scale 1/256, zero -128; bit 1 is raw >= 0.

1. Validate conversion inputs and show the exact Docker command. Then convert.

```bash
bash 1_VLC_code_main/AI_Pipeline_amebaPro2/ameba_deployment/aug/convert_nbg.sh --dry-run
bash 1_VLC_code_main/AI_Pipeline_amebaPro2/ameba_deployment/aug/convert_nbg.sh
```

The container is `ghcr.io/ameba-aiot/acuity-toolkit:6.18.8`. It imports
the existing quantized TFLite directly, preserves its i8 quantization, and exports
for `VIP8000NANONI_PID0XAD` with `--pack-nbg-unify`, the same SDK path and
export options as the CH4 toolkit script. It does not calibrate on held-out data.
Only `aug/work/` is writable in the container. It contains
`vlc_edge_cnn_aug.nb`, `vlc_edge_cnn_aug_nbg_meta.json`, import/export intermediates,
the ordered input manifest, and 800 saved dequantized Acuity CPU simulator outputs.
Simulation evaluates the imported quantized graph; actual NBG execution is
verified by the later board replay. The Docker command prints before execution.
Conversion refuses an existing work directory; retain failed intermediates for
review rather than overwriting them. A failed container can be resumed explicitly:

```bash
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -w /work -v "$PWD/1_VLC_code_main/AI_Pipeline_amebaPro2/ameba_deployment/aug/work:/work" -v "$PWD/1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/results/repeat_aug:/model:ro" -v "$PWD/1_VLC_code_main/AI_Pipeline_amebaPro2/ameba_deployment/aug/container_convert.py:/runner.py:ro" ghcr.io/ameba-aiot/acuity-toolkit:6.18.8 python3 /runner.py
```

This reuses only this task's intermediates. Acuity CLI execution remains
unverified in the sandbox; if 6.18.8 reports an option/path mismatch, preserve
its log and fix the AUG runner before accepting any gate.

2. Check real exported tensor metadata and require simulator/host bit agreement
>=99.9%. Generate the CPU headers and NBG byte array only after this gate.

```bash
.venv/bin/python 1_VLC_code_main/AI_Pipeline_amebaPro2/ameba_deployment/aug/check_nbg.py
.venv/bin/python 1_VLC_code_main/AI_Pipeline_amebaPro2/ameba_deployment/aug/export_cpu.py
```

`check_nbg.py --simulator-outputs PATH.npy` also accepts saved dequantized
probabilities of shape (800,48), in the manifest's exact order. It never treats
host predictions as simulator measurements. The default reads Acuity's
`iter_N_output_*out0*.tensor` files directly.
`check.json` binds the passing comparison to the TFLite and NBG hashes.
The exporter checks the existing pinned kernel-source hashes, leaves the original
library untouched, and emits only AUG-local headers/provenance. Its fixed batch-1
reshape is resolved without arithmetic; the 12 quantized compute ops remain.
The 64-vector timing bank is identical to the original input bank, with expected
outputs recomputed for AUG. That bank is a numerical/timing stimulus, not a
held-out accuracy set.

3. Run host tests, inspect the build command, then compile (no upload).

```bash
env XDG_CACHE_HOME=/tmp/vlc_aug_cache MPLCONFIGDIR=/tmp/vlc_aug_mpl PYTHONPATH=1_VLC_code_main/AI_Pipeline_amebaPro2/ameba_deployment/aug:1_VLC_code_main/AI_Pipeline_amebaPro2 .venv/bin/python -m unittest test_cpu_reference test_preamble_tracker test_onboard_tools
bash 1_VLC_code_main/AI_Pipeline_amebaPro2/ameba_deployment/aug/build_aug.sh --dry-run
bash 1_VLC_code_main/AI_Pipeline_amebaPro2/ameba_deployment/aug/build_aug.sh
```

The exact underlying compile command is:

```bash
/home/usnlab/ros2_ws/tools/arduino-cli/arduino-cli compile --fqbn realtek:AmebaPro2:Ameba_AMB82-MINI --libraries "$PWD/1_VLC_code_main/libraries" --build-property "build.extra_flags=-DARDUINO_SDK -DARDUINO_AMBPRO2 -DBOARD_AMB82_MINI {build.usb_flags} {build.menu_flags} -I$PWD/1_VLC_code_main/libraries/VLCInt8Reference/src" --build-property 'compiler.optimization_flags=-O3 -DTF_LITE_STATIC_MEMORY -DNDEBUG' --build-path /tmp/vlc_goal2_aug_build "$PWD/1_VLC_code_main/RX/Ameba_CPU_NPU_Benchmark_AUG"
```

The wrapper checks the conversion gate, regenerates local headers, then reports
`flash_ntz.bin` byte size and SHA-256. The sketch changes from the original
are its filename, the three generated-header includes using quotes, and the local
`vlc_tflite_runtime.cpp` translation unit. The latter includes the library's only
two source files (`runtime_shape.cc` and `common.cc`) exactly once; `ruy`,
`fixedpoint`, and `internal` contain only headers. The explicit `src/` include path
supplies the reference kernels without Arduino library discovery. Quoted model
includes keep AUG-local headers first; no library header includes `vlc_cpu_*.h`.
`build.extra_flags` preserves the board's three defines and USB/menu placeholders
from 4.1.0 `boards.txt` (the platform default is empty).
Core is realtek AmebaPro2 4.1.0. Vendor pre/postbuild hooks write shared tools
under ~/.arduino15; execute this outside the restricted sandbox, with no concurrent
Arduino build. Preserve the printed build size and hash in the evaluation record.

4. Close all serial monitors. Put the board in download mode: hold
UART_DOWNLOAD, press/release RESET, then release UART_DOWNLOAD (from the original
benchmark README). Upload, then press RESET to execute.

```bash
bash 1_VLC_code_main/AI_Pipeline_amebaPro2/ameba_deployment/aug/upload_aug.sh --dry-run
bash 1_VLC_code_main/AI_Pipeline_amebaPro2/ameba_deployment/aug/upload_aug.sh --port /dev/ttyUSB0
```

The wrapper first compares the built binary with the shared tools
`flash_ntz.bin`, because the vendor uploader consumes the latter. Exact upload:

```bash
/home/usnlab/ros2_ws/tools/arduino-cli/arduino-cli upload --fqbn realtek:AmebaPro2:Ameba_AMB82-MINI --port /dev/ttyUSB0 --input-dir /tmp/vlc_goal2_aug_build 1_VLC_code_main/RX/Ameba_CPU_NPU_Benchmark_AUG
```

5. Check identity without sending EVAL, then validate all replay inputs without
opening a port, and finally replay 4,000 tensors.

```bash
.venv/bin/python 1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/onboard_replay.py --identity-only --port /dev/ttyUSB0
.venv/bin/python 1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/onboard_replay.py --dry-run
.venv/bin/python 1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/onboard_replay.py --port /dev/ttyUSB0
```

Identity must report the AUG TFLite SHA above, buffered-v1, bank=64, pairs=512.
The firmware's identity field hashes the CPU TFLite graph; it does not independently
hash NBG bytes at runtime. The build gate/local provenance bind the NBG to that
graph; do not mix headers after building.

Offsets are 0, -40, -25, +25, +40 ms, 800 windows each. Baseline uses the exact
stored INT8 inputs. ±40 ms use `windows_offsets.npz`; ±25 ms are regenerated
from captured float traces using the frozen preamble/Manchester alignment and
sampling machinery, since that cache contains only 10-ms grid points.
Split assertions exclude training, validation, and calibration sessions.
Default pacing is 50 ms between EVALs, timeout 3 s, two retries; timeout retries
use an INFO barrier to drain late responses before resending the same input.

Results go to `retrain_sessions/results/onboard_aug/`:
`raw.csv`, `events.jsonl`, `config.json`, `summary.json`.
Existing output directories are refused. Failures retain partial CSV/logs and
`complete=false`; inspect `error` and every offset before reporting results.
Each offset reports direct pre-CRC truth errors, BER and two-sided 95% CP bounds
for CPU/NPU/host, plus raw/threshold CPU–NPU and backend–host agreement.
Host comparison explicitly uses TFLite BUILTIN_WITHOUT_DEFAULT_DELEGATES, because
this environment's XNNPACK AUTO resolver has threshold differences on AUG.
Intervals pool correlated bits and are descriptive, not independent-session
confidence or blind synchronization/channel PDR.

6. Run the same paired latency collector, adapted only for AUG source/model/output
paths and a conversion gate. Three rounds each contain 512 AB/BA pairs.

```bash
.venv/bin/python 1_VLC_code_main/AI_Pipeline_amebaPro2/ameba_deployment/aug/benchmark_aug.py --session aug_reference_s01 --rounds 3 --dry-run
.venv/bin/python -u 1_VLC_code_main/AI_Pipeline_amebaPro2/ameba_deployment/aug/benchmark_aug.py --port /dev/ttyUSB0 --session aug_reference_s01 --rounds 3
```

Outputs are in `retrain_sessions/results/onboard_aug_latency/aug_reference_s01/`.
Require CPU bank parity, all pair rows and completion markers before accepting
timings. Report NPU versus official CPU reference kernels, full-API ratio primary.
These are inference timings with the video domain active; they are not camera
pipeline FPS, channel latency, optimized CPU performance, energy, or power.

Historical preparation status (before conversion and board measurement):
CPU graph reference parity passed for 931 windows /
44,688 raw outputs; built-in kernels without delegates have six numeric
differences (maximum nine LSB), zero threshold differences. All 4,000 replay inputs
validate. Docker socket access is denied; /dev/ttyUSB0 is absent. Arduino compile
was attempted and stopped at the vendor chmod hook on read-only ~/.arduino15.
No AUG NBG or firmware binary/size exists yet. `vlc_cpu_nbg.h` intentionally
contains #error until the real checked AUG NBG is generated; no old NBG is reused.
Conversion, metadata/simulator agreement, final compile size, upload, runtime
identity, board parity, accuracy and latency all require manager verification.

## v2 mode

Set both variables on every command; setting only one is rejected. The only
accepted override is `results_v2` with `corrected-7m`. With both unset, legacy
paths remain selected. The v2 model SHA-256 is
`ef9076f8f5c08cf17ded2e5b5374f92d40b814ab0ad084462a7fe1eaa9e373ca`.
The dataset, offset cache and history come from `results_v2/data/` and
`results_v2/repeat_aug/`; repeat-3 must contain 800 windows in eight sessions
disjoint from training, validation and calibration. Replay dry-run prints their names.

Conversion mounts `results_v2/repeat_aug` read-only and writes only `aug/work_v2/`,
using `vlc_edge_cnn_aug_v2.nb` and `vlc_edge_cnn_aug_v2_nbg_meta.json`. The copied
sketch is `RX/Ameba_CPU_NPU_Benchmark_AUG_V2/`, with generated v2 CPU headers and
a generated NBG header gated by conversion/check/export. The build is
`/tmp/vlc_goal2_aug_v2_build`; replay and latency defaults are
`results_v2/onboard_aug/` and `results_v2/onboard_aug_latency/`. Host tests write
`aug/host_parity_v2.json`. No legacy conversion or sketch artifacts are reused.

Run commands separately from the repository root. Docker, compilation, upload
and serial commands below are for the manager; CPU checks and dry-runs open no port.

```bash
env CUDA_VISIBLE_DEVICES=-1 RETRAIN_OUTPUT_ROOT="$PWD/1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/results_v2" RETRAIN_SESSION_SET=corrected-7m bash 1_VLC_code_main/AI_Pipeline_amebaPro2/ameba_deployment/aug/convert_nbg.sh --dry-run
env CUDA_VISIBLE_DEVICES=-1 RETRAIN_OUTPUT_ROOT="$PWD/1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/results_v2" RETRAIN_SESSION_SET=corrected-7m bash 1_VLC_code_main/AI_Pipeline_amebaPro2/ameba_deployment/aug/convert_nbg.sh
env CUDA_VISIBLE_DEVICES=-1 RETRAIN_OUTPUT_ROOT="$PWD/1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/results_v2" RETRAIN_SESSION_SET=corrected-7m .venv/bin/python 1_VLC_code_main/AI_Pipeline_amebaPro2/ameba_deployment/aug/check_nbg.py
env CUDA_VISIBLE_DEVICES=-1 RETRAIN_OUTPUT_ROOT="$PWD/1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/results_v2" RETRAIN_SESSION_SET=corrected-7m .venv/bin/python 1_VLC_code_main/AI_Pipeline_amebaPro2/ameba_deployment/aug/export_cpu.py
env CUDA_VISIBLE_DEVICES=-1 RETRAIN_OUTPUT_ROOT="$PWD/1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/results_v2" RETRAIN_SESSION_SET=corrected-7m XDG_CACHE_HOME=/tmp/vlc_aug_cache MPLCONFIGDIR=/tmp/vlc_aug_mpl PYTHONPATH=1_VLC_code_main/AI_Pipeline_amebaPro2/ameba_deployment/aug:1_VLC_code_main/AI_Pipeline_amebaPro2 .venv/bin/python -m unittest test_cpu_reference test_onboard_tools
env CUDA_VISIBLE_DEVICES=-1 RETRAIN_OUTPUT_ROOT="$PWD/1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/results_v2" RETRAIN_SESSION_SET=corrected-7m bash 1_VLC_code_main/AI_Pipeline_amebaPro2/ameba_deployment/aug/build_aug.sh --dry-run
env CUDA_VISIBLE_DEVICES=-1 RETRAIN_OUTPUT_ROOT="$PWD/1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/results_v2" RETRAIN_SESSION_SET=corrected-7m bash 1_VLC_code_main/AI_Pipeline_amebaPro2/ameba_deployment/aug/build_aug.sh
env CUDA_VISIBLE_DEVICES=-1 RETRAIN_OUTPUT_ROOT="$PWD/1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/results_v2" RETRAIN_SESSION_SET=corrected-7m bash 1_VLC_code_main/AI_Pipeline_amebaPro2/ameba_deployment/aug/upload_aug.sh --dry-run
env CUDA_VISIBLE_DEVICES=-1 RETRAIN_OUTPUT_ROOT="$PWD/1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/results_v2" RETRAIN_SESSION_SET=corrected-7m bash 1_VLC_code_main/AI_Pipeline_amebaPro2/ameba_deployment/aug/upload_aug.sh --port /dev/ttyUSB0
env CUDA_VISIBLE_DEVICES=-1 RETRAIN_OUTPUT_ROOT="$PWD/1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/results_v2" RETRAIN_SESSION_SET=corrected-7m .venv/bin/python 1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/onboard_replay.py --identity-only --port /dev/ttyUSB0
env CUDA_VISIBLE_DEVICES=-1 RETRAIN_OUTPUT_ROOT="$PWD/1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/results_v2" RETRAIN_SESSION_SET=corrected-7m .venv/bin/python 1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/onboard_replay.py --dry-run
env CUDA_VISIBLE_DEVICES=-1 RETRAIN_OUTPUT_ROOT="$PWD/1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/results_v2" RETRAIN_SESSION_SET=corrected-7m .venv/bin/python 1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/onboard_replay.py --port /dev/ttyUSB0
env CUDA_VISIBLE_DEVICES=-1 RETRAIN_OUTPUT_ROOT="$PWD/1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/results_v2" RETRAIN_SESSION_SET=corrected-7m .venv/bin/python 1_VLC_code_main/AI_Pipeline_amebaPro2/ameba_deployment/aug/benchmark_aug.py --session aug_v2_reference_s01 --rounds 3 --dry-run
env CUDA_VISIBLE_DEVICES=-1 RETRAIN_OUTPUT_ROOT="$PWD/1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/results_v2" RETRAIN_SESSION_SET=corrected-7m .venv/bin/python -u 1_VLC_code_main/AI_Pipeline_amebaPro2/ameba_deployment/aug/benchmark_aug.py --session aug_v2_reference_s01 --rounds 3 --port /dev/ttyUSB0
```

Known pre-existing limitation in this checkout: the legacy replay dry-run fails
regenerating ±25 ms windows with a missing legacy 7 m session key in the current
manifest. The v2 path uses the corrected session configuration. Source hashes
remain actual file hashes, so editing `onboard_replay.py` changes its own recorded
hash even when legacy input/model/header/history hashes remain unchanged.
