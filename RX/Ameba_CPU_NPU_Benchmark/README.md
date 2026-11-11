# Goal 2: same-board INT8 CPU-reference / NPU comparison

This retained CH4 sketch is the buffered acquisition firmware. Its embedded
CNN is an earlier model retained so the sketch compiles; final evaluation does
not use its CNN output. Stored inputs are replayed through AUG v2 on board
CPU and NPU. See [the project README](../../README.md) and
[the capture procedure](../../CAPTURE_7M_RUNBOOK.md). Historical Phase-A/B
measurements below describe the original model and are not final AUG results.

This is a separate firmware, **not** the production optical receiver. It does
not publish patient telemetry. TX, ROI and room lighting are not needed for
this first inference microbenchmark. Video hardware is initialized for the
NPU power domain and stays active; JPEG decoding/ROI/resampling are not run.
Do not label its timings as channel latency, PDR, or production FPS.

## What is held constant

- Same AMB82-MINI, same input bytes and same CH4 trained/quantized graph.
- NPU uses the exact NBG bytes from the production receiver header; exporter
  asserts equality with `ameba_deployment/vlc_edge_cnn_ch4.nb`.
- CPU executes official TensorFlow Lite 2.14 reference INT8 kernels, including
  five Conv2D, the three post-ReLU BN MUL/ADD pairs, and logistic. Only fixed
  shape bookkeeping is resolved ahead of time. No FP32 substitute, retraining,
  or moving BN across ReLU is used.
- 64 input tensors: 32 previously captured INT8 windows plus 32 calibration
  windows. They test numerical agreement/timing, not physiological accuracy or
  channel BER. Warm up all 64 inputs on both backends before timing.
- 512 timed pairs per round, AB/BA order alternating, same sample within pair.
  Delay 1 ms between pairs, outside both timing regions; no prints inside them.
- Compile both paths with `-O3 -DTF_LITE_STATIC_MEMORY -DNDEBUG`, same firmware.
  This differs from production guard-v2's default `-Os`; do not silently merge
  throughput results across these builds.

## Buffered pre-CRC accuracy mode

Capability `accuracy=buffered-v1` adds `GOAL2 CAPTURE <seconds>` (10..1200)
and `GOAL2 EVAL <384 hex characters>`. Capture records live ROI brightness
at 20 Hz in a 96,000-byte float buffer, then exports it after capture stops.
No CNN or per-sample UART output runs during acquisition. This capture's
FPS must not be presented as the production CNN pipeline's FPS. EVAL runs
the same 192-byte tensor on the real board CPU and NPU, returning both raw
INT8 outputs without CRC/range filtering. The old 4,608-byte CPU workspace
is not total RAM: this diagnostic build additionally reserves the capture
buffer, weights, NPU buffers, video/runtime memory and other state.

`benchmark_precrc.py` captures TX synthetic truth over a second serial port;
it verifies TX identity before sending commands and restores sensor payload
mode on completion. It does not disable the physical TX. A host-time-only
window match is diagnostic because USB, camera buffering and sampling phase
introduce uncertainty. `replay_precrc_buffer.py` therefore also supports an
explicit optical alignment diagnostic: within +/-250 ms of the nominal TX
start, choose the best squared correlation against ONLY the 400-ms delimiter
and fixed Manchester preamble. No TX payload bits, CRC, CNN output, or BER
are used to rank timing candidates. Polarity uses the decoded four-bit
preamble, with normal polarity on a tie. All complete source frames are
scored, including CRC failures; report direct and preamble-polarity results.

This is timing-assisted accuracy, not a blind synchronization/PDR experiment.
The first pilot was used to develop the alignment rule. Validate a frozen
rule on new captures before paper results. Same-setup repeats are not
independent-day or session-separated generalization evidence.

## CPU baseline scope

Reference kernels are not an optimized CMSIS-NN baseline or the best possible
CPU implementation. Describe any ratio as **NPU versus this CPU reference**.
For a broad accelerator-benefit claim, add an optimized CPU implementation
and/or justify why the reference is the deployment baseline. Microbenchmark
ratios alone do not establish lower energy or preserved end-to-end FPS.

Upstream sources, copied with Apache-2.0 licenses into
`libraries/VLCInt8Reference`, are pinned in `provenance.json`:

- [TensorFlow v2.14 reference Conv2D](https://github.com/tensorflow/tensorflow/blob/v2.14.0/tensorflow/lite/kernels/internal/reference/integer_ops/conv.h)
- [TensorFlow INT8 quantization specification](https://github.com/tensorflow/tensorflow/blob/v2.14.0/tensorflow/lite/g3doc/performance/quantization_spec.md)
- TensorFlow commit `4dacf3f368eb7965e9b5c3bbdd5193986081c3b2`.
- gemmlowp commit `e844ffd17118c1e17d94e1ba4354c075a4577b88`.
- ruy commit `2264753777198e4393fb83c44c693462d57a2be1` (profiling disabled).

## Numerical gate before timing

For the final AUG numerical gates, use the host-test commands in
[the AUG runbook](../../AI_Pipeline_amebaPro2/ameba_deployment/aug/RUNBOOK.md).
Host tests verify correctness, not board latency. On board, all timing-bank
CPU outputs must match the stored reference before timings are accepted.
NPU disagreements must be reported separately from channel BER.

## Build and run

From the project root:

```bash
/home/usnlab/ros2_ws/tools/arduino-cli/arduino-cli compile \
  --fqbn realtek:AmebaPro2:Ameba_AMB82-MINI --libraries libraries \
  --build-property 'compiler.optimization_flags=-O3 -DTF_LITE_STATIC_MEMORY -DNDEBUG' \
  --build-path /tmp/vlc_goal2_paired_build RX/Ameba_CPU_NPU_Benchmark
```

The exporter and all generated arrays are already provided. To regenerate,
checkout the pinned upstream sources at the paths named in
`ameba_deployment/export_cpu_reference.py`, then run it with the root `.venv/bin/python`.
Its upstream copy operation preserves original kernel code and licenses.

Upload through the vendor uploader; verify that the shared tools
`flash_ntz.bin` matches `/tmp/vlc_goal2_paired_build/flash_ntz.bin` first.
If auto UART boot fails, hold UART_DOWNLOAD, press/release RESET, then release
UART_DOWNLOAD. After successful upload press RESET to execute.

Serial firmware commands:

- `GOAL2 INFO`: firmware/model/bank identity; compiler and F_CPU macro.
- `GOAL2 RUN`: numerical gate, warm-up and 512 paired measurements.

Capture three rounds with no concurrent serial client:

```bash
python3 -u AI_Pipeline_amebaPro2/benchmark_cpu_npu.py \
  --port /dev/ttyUSB0 --session new_unique_latency_session --rounds 3
```

Collector requires correct firmware identity/model before RUN and rejects
missing parity, pair rows, quality windows or completion markers. It retains
failed runs rather than replacing them. Raw output and timestamps are in
`events.jsonl`; exact per-call durations in `paired_timings.csv`; quality in
`quality.json`; status and descriptive statistics in `summary.json`.
Read `complete` and `error` before reporting any results.

## Timing boundaries

- CPU core: `vlcCpuInfer`, all model arithmetic and output copy to CPU buffer.
- NPU core: `vip_run_network`; not model loading or graph preparation.
- CPU full API: input copy plus CPU core.
- NPU full API: input map/copy/cache clean/unmap/set-input, network run,
  output cache invalidate and output copy.

The full-API mean ratio is the primary reported ratio; core-only times are
secondary because runtime boundaries differ. Percentiles are exact stored
durations using nearest-rank p95, not the old 1-ms histogram bound.
Repeated windows/calls are not independent experimental sessions. Report
per-round statistics as well as pooled descriptive values.

## Rollback and remaining goal-2 work

### Phase B controlled camera workload

Build `paired-int8-pipeline-v2` adds real 320x192 JPEG decoding, 20x12 grid
activity calculation, fixed ROI, timestamp interpolation and per-bit
normalization. It still feeds the same stored tensor bank to CNN, not the
live normalized ROI. This isolates backend scheduling under camera load;
it is NOT a production live decoder or a new channel PDR/BER measurement.
No classic search, packet validation or telemetry publication is included.

Camera preset matches goal 1: exposure 500 us, gain 256, WDR/AE/AWB disabled,
red/blue balance 357/399. Keep the scene and illumination fixed across legs.

Each mode receives exactly 20 jobs/s for a finite requested interval; no
jobs are discarded. Responses are measured from scheduled release until
inference completes. A response over 50 ms is a scheduling deadline miss,
not a medical-alert deadline. Catch-up batches and achieved job rate are
reported to expose backlog. Timing quantiles are 250-us histogram upper
bounds (overflow uses observed maximum), not exact p95 from phase A.
Inference wall-time fraction is not CPU utilization, especially for NPU
calls that may block while the accelerator works.

Commands: `GOAL2 PIPE CAMERA 60`, `GOAL2 PIPE CPU 60`,
`GOAL2 PIPE NPU 60`, `GOAL2 ROI 92 90 67 60`. Each measurement has a
10-second warm-up and resets the replay sequence to input 0. CPU output
parity is checked before each leg and during CPU measurements. Raw logging
and UART output are disabled in the measured stages.

```bash
python3 -u AI_Pipeline_amebaPro2/benchmark_cpu_npu_pipeline.py \
  --port /dev/ttyUSB0 --session new_unique_pipeline_session \
  --duration 60 --repeats 2 --roi-px 92,90,67,60 \
  --lighting unchanged_operator_to_confirm
```

Order is camera/CPU/NPU then NPU/CPU/camera. All measured legs must report
identical job counts and replay sequence hashes. Stored samples are repeated,
not additional independent numerical-quality observations. The collector
saves incomplete runs without overwriting. This phase completed in
`dataset/benchmark/goal2_pipeline_reference_s01`: 6/6 measured legs,
`complete=true`, no capture errors. Two 60-s measurements per mode:
preprocessing 30.000 FPS, CPU reference 11.636 FPS mean (11.050–12.223),
NPU 30.001 FPS mean. All inference jobs executed (2400 per backend);
CPU had 2273 responses over 50 ms, NPU none. These results are specific
to the synchronous scheduler and reference CPU implementation, not a
hardware limit for all optimized CPU pipelines. See the session report.

Historical Phase-A binaries and rollback images are no longer distributed.
Rebuild the required sketch and verify its identity rather than relying on an
archived image. For final paired latency, camera throughput and power results,
see [README.md](../../README.md). Inference timings alone do not establish
energy savings or production FPS; hardware verification is required.
