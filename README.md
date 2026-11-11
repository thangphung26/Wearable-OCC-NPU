# Wearable optical health telemetry

Research code for wearable optical camera communication (OCC) with on-camera
NPU decoding. **Research prototype, not a medical device.**

## System

The TX uses a Seeed XIAO nRF52840 Sense, MAX30102 PPG sensor and MLX90614 IR
thermometer. A blue LED is switched through a 2N3904 transistor. Manchester OOK
carries a 48-bit CRC-8 frame at **5 bit/s**, with one frame every **10 s**.
The live TX also reads its onboard IMU.

The RX is an AmebaPro2 AMB82-MINI: **30 FPS acquisition** → LED ROI mean →
**20 Hz resampling** → **48×4 INT8 window** → INT8 CNN on the NPU with Classic
Manchester fallback → UART JSON → web monitor. Acquisition FPS, resampling Hz
and link bit rate are distinct.

## Repository map

| Path | Purpose |
|---|---|
| `TX/VLC_OOK_TX_realtime_sensors/` | Benchmark transmitter with serial-controlled synthetic ground truth and sensor mode. |
| `TX/VLC_OOK_TX_realtime_sensors_rt_imu/` | Live sensor transmitter with IMU motion flags. |
| `TX/tools/imu_probe/` | Bounded IMU bus probe and helper used by the live TX. |
| `RX/Ameba_CPU_NPU_Benchmark/` | Buffered optical capture and original CH4 CPU/NPU benchmark. |
| `RX/Ameba_CPU_NPU_Benchmark_AUG_V2/` | Final augmented CNN CPU/NPU replay and paired benchmark. |
| `RX/Ameba_CPU_NPU_Benchmark_AUG/` | Legacy augmented-model benchmark; retained for comparison. |
| `RX/Ameba_RX_NPU_Receiver_Track_AUG/` | Live AUG v2 receiver with ROI tracking and Classic fallback. |
| `RX/Ameba_RX_NPU_Receiver/` | Legacy CH4 reference headers and host-test support; no runnable sketch remains. |
| `libraries/VLCInt8Reference/` | Pinned TensorFlow Lite INT8 CPU reference kernels and provenance. |
| `AI_Pipeline_amebaPro2/` | Capture, optical alignment and replay tools. |
| `AI_Pipeline_amebaPro2/retrain_sessions/` | Session-separated training, ablations and evaluation; final outputs in `results_v2/`. |
| `AI_Pipeline_amebaPro2/ameba_deployment/aug/` | Acuity conversion, model export and board benchmark tools. |
| `web_medical_monitor/` | UART gateway and browser monitor. |
| `results/` | Live-run, ROI and energy evidence; large raw logs are excluded. |
| `output/` | Local generated builds and scratch outputs (ignored). |

## Final model

The final augmented INT8 CNN has **five convolution layers and 21,481 parameters**:

- TFLite: `AI_Pipeline_amebaPro2/retrain_sessions/results_v2/repeat_aug/deployment_candidate_aug_int8.tflite`
- NBG: `AI_Pipeline_amebaPro2/ameba_deployment/aug/work_v2/vlc_edge_cnn_aug_v2.nb`
- TFLite SHA-256: `ef9076f8f5c08cf17ded2e5b5374f92d40b814ab0ad084462a7fe1eaa9e373ca`

The model without augmentation (`results_v2/repeat/deployment_candidate_int8.tflite`)
is an ablation. Capture firmware embeds an earlier model only so it compiles;
its CNN output is not used for final evaluation. Evaluation replays the stored
INT8 inputs through the augmented CNN on the board CPU and NPU.

## Quick start

Commands below run separately from the **workspace root**, the parent of
`1_VLC_code_main/`. Install `arduino-cli`, the Seeed nRF52 core and Realtek
AmebaPro2 core (deployment records use 4.1.0), plus the sensor/camera dependencies
listed in each sketch. Replace serial ports for your hardware.

### Firmware

Build and upload the live TX (use the benchmark sketch instead for captures):

```bash
arduino-cli compile --fqbn Seeeduino:nrf52:xiaonRF52840Sense --build-path /tmp/vlc_tx_build 1_VLC_code_main/TX/VLC_OOK_TX_realtime_sensors_rt_imu
arduino-cli upload --fqbn Seeeduino:nrf52:xiaonRF52840Sense --port /dev/ttyACM0 --input-dir /tmp/vlc_tx_build 1_VLC_code_main/TX/VLC_OOK_TX_realtime_sensors_rt_imu
```

Build/upload the AUG_V2 replay benchmark with the runbook's SDK defines,
include path and optimization flags:

```bash
arduino-cli compile --fqbn realtek:AmebaPro2:Ameba_AMB82-MINI --libraries "$PWD/1_VLC_code_main/libraries" --build-property "build.extra_flags=-DARDUINO_SDK -DARDUINO_AMBPRO2 -DBOARD_AMB82_MINI {build.usb_flags} {build.menu_flags} -I$PWD/1_VLC_code_main/libraries/VLCInt8Reference/src" --build-property 'compiler.optimization_flags=-O3 -DTF_LITE_STATIC_MEMORY -DNDEBUG' --build-path /tmp/vlc_goal2_aug_v2_build 1_VLC_code_main/RX/Ameba_CPU_NPU_Benchmark_AUG_V2
arduino-cli upload --fqbn realtek:AmebaPro2:Ameba_AMB82-MINI --port /dev/ttyUSB0 --input-dir /tmp/vlc_goal2_aug_v2_build 1_VLC_code_main/RX/Ameba_CPU_NPU_Benchmark_AUG_V2
```

Before RX upload, verify the built `flash_ntz.bin` matches the shared vendor
tools image; another build can overwrite it. If automatic boot fails, enter
manual download mode: hold UART_DOWNLOAD, press/release RESET, then release
UART_DOWNLOAD. Require `upload success`, then press RESET to run.
See the [conversion and replay runbook](AI_Pipeline_amebaPro2/ameba_deployment/aug/RUNBOOK.md)
for conversion gates and paired v2 environment settings. For live telemetry,
build the [tracking AUG receiver](RX/Ameba_RX_NPU_Receiver_Track_AUG/README.md)
instead; it uses its own `-Os` build procedure. The replay benchmark does not
publish telemetry. Building, flashing and optical operation require hardware
verification; these commands were not executed during documentation preparation.

### Python and evaluation

Use the root `.venv/`. No requirements file is supplied. Key packages are
NumPy, SciPy, pandas, matplotlib, TensorFlow/Keras, scikit-learn, pyserial,
Pillow and OpenCV (`opencv-python`); some legacy export tools require
`tflite-runtime`. Install versions compatible with the retained model/toolchain.

Select the corrected training data and outputs before each training/deployment
workflow (legacy defaults otherwise remain active):

```bash
export RETRAIN_OUTPUT_ROOT="$PWD/1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/results_v2"
export RETRAIN_SESSION_SET=corrected-7m
.venv/bin/python 1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/export_dataset.py --check-only
.venv/bin/python 1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/train_eval_aug.py repeat
.venv/bin/python 1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/train_eval_aug.py loso
.venv/bin/python 1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/robustness.py --aug
.venv/bin/python 1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/onboard_replay.py --dry-run
```

Training/evaluation needs the excluded recordings and derived inputs; preserve
the published outputs before rerunning writers. See the [training guide](AI_Pipeline_amebaPro2/retrain_sessions/README_aug.md)
and [independent-test protocol](CAPTURE_FRESH_TEST_RUNBOOK.md) for data selection,
board replay and fresh-session scoring.

Start the monitor using either launcher:

```bash
.venv/bin/python 1_VLC_code_main/run_web.py --port 8080
```

Equivalent: `.venv/bin/python 1_VLC_code_main/web_medical_monitor/start_monitor.py --port 8080`.
Open `http://127.0.0.1:8080/monitor.html`, then connect RX/TX through Settings →
COM ports at 115200 baud. See the [monitor guide](web_medical_monitor/README.md).

## Key results

These are recorded measurements, not guarantees across installations. CNN
accuracy is timing-assisted pre-CRC replay; CPU timings use the pinned reference
kernels. Let `R` denote `AI_Pipeline_amebaPro2/retrain_sessions/results_v2/`.

| Measurement | Result | Evidence folder |
|---|---|---|
| Independent test | CNN NPU 2 / 115,200 bit errors; Classic 3 / 115,200 | `R/fresh/campaign_board_v2/` |
| Timing offsets −40 / +40 ms | Augmented CNN (host sweep) 21 / 6 errors; Classic 539 / 396 | `R/fresh/robustness_fresh/` |
| Inference latency | CPU 22.234 ms; NPU 2.901 ms | `R/onboard_aug_latency/` |
| Camera throughput | NPU 30 FPS; CPU approximately 20 FPS | `dataset/benchmark/` (pipeline records; available on request) |
| Whole-board power | 0.825 W; no resolvable NPU increment | `results/energy/` |
| Live delivery | 119 bit-exact NPU frames | `results/raw_serial/20261109_084326/` |
| TX off | 0 false publications over 14 h | `results/raw_serial/20261110_040050/` |

## Data

Raw recordings in `dataset/` (including pipeline-local datasets) are excluded
because of size and are **available on request**. Large regenerable caches,
compiler dumps and raw instrument logs are also ignored. Kept reports and
summaries document the results; a code-only checkout cannot reproduce capture
replay without the recordings and the AmebaPro2 hardware/toolchain.
