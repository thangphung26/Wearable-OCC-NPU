# Re-capture 7 m with the 1/3/5-m gray8v2 pipeline

Run commands individually from `/home/usnlab/Desktop/Job_ngon_UWC`, using the
root `.venv/`. This document records a procedure; no capture, firmware build,
upload, analysis, or dataset modification was performed to write it. Firmware
execution, optical alignment, RESET, and acceptance on the board require the
manager and author with hardware. The board currently holds
`RX/Ameba_CPU_NPU_Benchmark_AUG`; replace it with the **original** benchmark
before acquisition or CH4 replay. Restore/rebuild AUG only for the final AUG
evaluation.

## 1. What actually produced the reference sessions

Evidence paths below are relative to `1_VLC_code_main/`.

The six `dataset/benchmark/goal2_gray8v2_phasev1_*/protocol_lock.json` files
specify three independent 1010-second acquisitions each, 100 complete optical
frames per acquisition, RX `/dev/ttyUSB0`; TX was local `/dev/ttyACM0` at 1 m and
`COM3` on a laptop over SSH at 3 m and 5 m,
`require_preprocess=jpeg-gray8-v2`, and replay
`alignment=tracker-manchester-v1`. All 18 corresponding capture/replay
`metadata.json`, `summary.json`, and `REPORT.md` files were inspected: each
capture and replay completed, each compared 100 frames, each replay compared
4800 bits and had `cpu_board_host_unequal_values=0`.

| Distance | Fixed ROI x,y,w,h | Batch suffix, before `_100f_s01`–`s03` |
|---|---|---|
| 1 m | `167,94,71,65` | `1m_roi167_94_exp2000_lights_{on,off}_20261026` |
| 3 m | `195,51,32,26` | `3m_roi195_51_exp2000_lights_{on,off}_20261026` |
| 5 m, ON | `133,82,25,28` | `5m_core133_82_exp2000_lights_on_20261025` |
| 5 m, OFF | `133,82,25,28` | `5m_core133_82_exp2000_lights_off_20261026` |

Prefixes are `goal2_precrc_gray8v2_phasev1_` for acquisition,
`goal2_replay_gray8v2_phasev1_` for aligned replay, and
`goal2_gray8v2_phasev1_` for the batch directory. Lighting metadata is
`room_lights_on` or `room_lights_off`.

**RX:** `RX/Ameba_CPU_NPU_Benchmark/Ameba_CPU_NPU_Benchmark.ino`, Realtek
AmebaPro2 core 4.1.0, FQBN `realtek:AmebaPro2:Ameba_AMB82-MINI`, local
`libraries/`, optimization `-O3 -DTF_LITE_STATIC_MEMORY -DNDEBUG`.
The sketch sets exposure **2000 us**, gain **256**, WDR/AE/AWB disabled,
red/blue balance 357/399; these are source settings, not host capture flags.
It requests JPEG 320×192 at **30 FPS acquisition**, then interpolates the
fixed ROI mean to **20 Hz resampling**. `accuracy_bench.h` sets
`EIGHT_BIT_GRAYSCALE` **after each** `openFLASH`, decodes `JPEG_LUMA_ONLY`,
and rejects non-8-bit callback data. `pipeline_bench.h` uses callback stride
`iWidth`, valid width `iWidthUsed`, and bounds the measurement to 320×192.
The SDK can actually deliver 352×288 JPEGs; do not rescale snapshot coordinates
or select outside the application's 320×192 bounds.

The locks name the verified upload bundle
`dataset/diagnostics/goal2_cpu_npu/gray8_exp2000_v2_upload_bundle/`.
Its `flash_ntz.bin` SHA-256 is
`fcfbe4669491658a8fbeac85d53a5644dfe30b3d19db4659773c88c1cfbc2836`.
Its README and `RUNTIME_VERIFIED.md` distinguish it from the earlier
`gray8_exp2000_v2_build/` combined image. Runtime compiler was 10.3.0 and
`f_cpu_macro=500000000`. Current RX/TX sources, capture/replay/tracker/phase
scripts, CH4 CPU/NBG headers and model artifacts match the hashes in the
1-m ON lock, including that archived binary.

**TX:** `TX/VLC_OOK_TX_realtime_sensors/VLC_OOK_TX_realtime_sensors.ino`;
`BENCHMARK_GROUND_TRUTH=1`, normal serial-controlled build
(`VLC_BATTERY_BENCHMARK=0`), seed default `VLC_BENCHMARK_SEED=20261014UL`.
`benchmark_precrc.py` verifies a live `[TX_GT]` before sending
`PAYLOAD TEST ON`, waits for acknowledgement and `payload_mode=test`, and
restores `PAYLOAD TEST OFF` on exit. No battery-only reconstruction or
sensor readings were used as truth. The synthetic payload cycles indices
0–99; sequence alone wraps at 16 and must not identify frames.

Each frame is 48 bits MSB first: header `1010`, patient ID 1, HR `30+index`,
SpO2 `70+r%31`, temperature code `(r>>8)%81`, IMU `(r>>16)&3`, battery
`(r>>18)&7`, alert `(r>>21)&1`, sequence `index&15`, and CRC-8/0x07,
init 0, over the first 40 bits. Here `r` is uint32 seed XOR
`((index+1)*0x9E3779B9)`, followed by xorshift left 13, right 17, left 5.
Manchester 1=ON/OFF, 0=OFF/ON, 100 ms per half-bit: **5 bit/s link**,
9.6 s data plus 400 ms LOW delimiter = 10 s cycle.

**Truth/timing:** `[TX_GT]` supplies `frame_id`, `frame_hex`, `tx_us`, and
`payload_mode`. The capture unwraps 32-bit `tx_us`, estimates the TX-to-host
offset using minimum UART-corrected receipt delay, and uses receipt of
`[GOAL2_CAPTURE_BEGIN]` as buffer origin. Every complete 9.6-s test window
inside the buffer is scored; 1010 s yields the locked 100-frame target.
The host extracts 192 samples (48×4), min/max normalizes each bit with a
0.5-luma cutoff and round-half-up INT8 quantization, and sends `GOAL2 EVAL`.
CPU and NPU run on the same board with identical bytes. There is no CNN or
per-sample UART during acquisition; samples are dumped afterward.

**Phase/post-processing:** `replay_precrc_buffer.py` reads the saved buffer
and nominal starts, acquires delimiter/preamble timing within ±250 ms at
5-ms steps, then tracks within ±50 ms around the smoothed offset (0.25).
The tracker locks optical gain polarity from the first frame; weak fit
(`<0.8`) and fallback are flagged and retained. Manchester refinement searches
±25 ms at 5-ms steps using unknown-bit half-bit consistency over the window.
Neither stage ranks with payload, CRC, model output, or BER. Reporting also
applies a decoded-preamble polarity policy (normal on tie). Replay performs
fresh on-board CH4 EVALs and checks CPU output against
`/tmp/vlc_cpu_reference_host`. This is the entire precrc→phase replay path;
do **not** add an extra capture/replay stage or CRC-filtering step. It is timing-assisted pre-CRC accuracy, not blind PDR.

**ROI provenance:** capture is a fixed manually supplied `--roi-px`; there
is no ROI search in `benchmark_precrc.py` or `run_precrc_repeats.py`.
`roi_confirm.py` offers a serial JPEG, a brightest-hotspot proposal (default
32×26), and manual rectangle selection. The 5-m spatial diagnostic in
`AI_Pipeline_amebaPro2/analyze_led_snapshots.py` and
`dataset/benchmark/controlled_led_snapshots_gray8v2_5m_20261025_s01/REPORT.md`
explains the tighter 133,82,25,28 LED core: the earlier broad ROI mixed
opposite-sign modulation. The core was chosen from diagnostic images, then
frozen for fresh validation; it is not an independently established optimal
ROI policy. Exact original GUI actions/search commands for 1 and 3 m are
**UNKNOWN**: locks and metadata retain final coordinates, not selection history.

The original literal shell invocation is **UNKNOWN** (locks retain options,
not shell history). The commands below reconstruct the wrapper's actual
capture and replay argv from those locks and current matching source.
For example, the 1-m ON s01 operations were equivalent to the following
fully expanded commands (historical evidence only; those names already exist):

```bash
.venv/bin/python -u 1_VLC_code_main/AI_Pipeline_amebaPro2/benchmark_precrc.py \
  --session goal2_precrc_gray8v2_phasev1_1m_roi167_94_exp2000_lights_on_20261026_100f_s01 \
  --duration 1010 --roi-px 167,94,71,65 --lighting room_lights_on \
  --distance-m 1 --tx-port /dev/ttyACM0 --rx-port /dev/ttyUSB0 \
  --require-preprocess jpeg-gray8-v2
```

```bash
.venv/bin/python -u 1_VLC_code_main/AI_Pipeline_amebaPro2/replay_precrc_buffer.py \
  1_VLC_code_main/dataset/benchmark/goal2_precrc_gray8v2_phasev1_1m_roi167_94_exp2000_lights_on_20261026_100f_s01 \
  --session goal2_replay_gray8v2_phasev1_1m_roi167_94_exp2000_lights_on_20261026_100f_s01 \
  --alignment tracker-manchester-v1 --validation-capture
```

Use this buffered acquisition procedure for capture; the live receiver quick
start in [README.md](README.md) serves a different purpose.

## 2. Author: physical preparation

- Measure **7 m emitter-to-camera**, preserve line of sight, fix TX/camera
  mounts and aim the LED at the camera. Keep geometry static for all six runs;
  keep people and objects out of the beam. Record any obstruction when it
  happens, before examining decoder results.
- Keep the same emitter, driver polarity/current setting, supply, camera,
  lens/focus, room, orientation and daylight/window state as the reference
  setup wherever possible. Record deviations. Exact current, lux, lens/focus
  and measured angles are **UNKNOWN** in the gray8v2 locks/metadata; the
  author must establish these physically. Do not infer lux or total darkness
  from the lamps label.
- Connect TX USB to the SSH laptop described below; RX stays local on
  `/dev/ttyUSB0`. Do not substitute battery/RX-only mode: this procedure needs
  TX serial truth for the full run. Close Arduino monitors and competing
  serial capture/ROI tools.
- Keep 2 ms exposure, gain 256, camera/white-balance settings, CH4 model,
  normalization, 30 FPS acquisition, 20 Hz resampling and 5 bit/s link fixed.
  Do not optimize exposure/gain or ROI against the six test runs' BER.
- With lamps ON, identify the actual LED in snapshots, freeze a 7-m ROI,
  and verify it remains on the LED with lamps OFF. Do not reuse the legacy
  `roi97_71` coordinates without checking the new physical placement.
  Return lamps ON for the first batch; switch OFF only after its three runs
  finish. Keep the same ROI and geometry for both conditions.
- Author confirms lamps state immediately before each batch and records
  daylight and any movement. Budget at least 101 minutes of acquisition
  (6×1010 s), plus sample transfers, EVAL/replay, setup and manual actions.

### TX on a second laptop

Install Python 3 + pyserial on the laptop, enable an SSH server, authorize
this PC's SSH key, and use Tailscale or the same LAN. Close Arduino Serial
Monitor/other serial clients and disable laptop sleep. Replace `user@host`
and `COMx` below with the actual target and TX port. From this PC, list ports
without opening them:

```bash
ssh -T user@host "py -3 -m serial.tools.list_ports -v"
```

For a Linux/macOS laptop, list with
`ssh -T user@host "python3 -m serial.tools.list_ports -v"`, use its
`/dev/ttyACM0` or `/dev/cu.*` port instead of `COMx`, and set the remote
launcher on **this PC** before preparation and every capture/locked run:

```bash
export VLC_TX_SSH_PYTHON=python3
```

Windows defaults to `py -3`. Section 4's two `--prepare-only` commands now
include `--tx-port COMx --tx-ssh user@host`; the resulting lock retains these
options and `--run-lock` uses them automatically. The launcher environment is
not stored in the lock, so keep it set for Linux/macOS targets.
Use `roi_confirm.py` with the original capture firmware to inspect snapshots
and freeze `ROI7` before capture. Use the same TX SSH settings for both lamp
states. 7 m uses SSH, as the 3 m and 5 m reference sessions did; only 1 m
used local TX serial. Record target,
port, laptop OS and launcher in campaign notes; retain `tx_ssh` in protocol
locks/capture metadata. About 116 ms of SSH arrival spread is not a bound on
absolute latency. Minimum-receipt clock mapping remains approximate; review
alignment diagnostics as for the 3 m and 5 m sessions.

## 3. Manager: restore the original capture firmware

These are manager/hardware commands, not commands executed by this document's
authoring agent. Vendor compile hooks write shared files under
`~/.arduino15`; run on the hardware host outside the restricted executor.
No concurrent Arduino build is allowed between compile, staging and upload.

```bash
.venv/bin/python -m serial.tools.list_ports -v
```

```bash
/home/usnlab/ros2_ws/tools/arduino-cli/arduino-cli compile \
  --fqbn realtek:AmebaPro2:Ameba_AMB82-MINI \
  --libraries "$PWD/1_VLC_code_main/libraries" \
  --build-property 'compiler.optimization_flags=-O3 -DTF_LITE_STATIC_MEMORY -DNDEBUG' \
  --build-path /tmp/vlc_capture7m_build \
  1_VLC_code_main/RX/Ameba_CPU_NPU_Benchmark
```

For exact historical firmware, stage a private copy of the vendor tools and
replace its images with the verified reference bundle **after compile**.
Use a fresh `/tmp/vlc_capture7m_tools` destination; stop if it already exists.
This deliberately flashes the archived reference application. A rebuilt
binary need not be byte-identical; do not claim its hash is the archive hash.

```bash
test ! -e /tmp/vlc_capture7m_tools
```

```bash
cp -a /home/usnlab/.arduino15/packages/realtek/tools/ameba_pro2_tools/1.4.7 /tmp/vlc_capture7m_tools
```

```bash
cp 1_VLC_code_main/dataset/diagnostics/goal2_cpu_npu/gray8_exp2000_v2_upload_bundle/firmware.bin 1_VLC_code_main/dataset/diagnostics/goal2_cpu_npu/gray8_exp2000_v2_upload_bundle/system_files.bin 1_VLC_code_main/dataset/diagnostics/goal2_cpu_npu/gray8_exp2000_v2_upload_bundle/firmware_isp_iq.bin 1_VLC_code_main/dataset/diagnostics/goal2_cpu_npu/gray8_exp2000_v2_upload_bundle/flash_ntz.bin /tmp/vlc_capture7m_tools/
```

```bash
sha256sum /tmp/vlc_capture7m_tools/firmware.bin /tmp/vlc_capture7m_tools/system_files.bin /tmp/vlc_capture7m_tools/firmware_isp_iq.bin /tmp/vlc_capture7m_tools/flash_ntz.bin
```

Require, in that order:

```text
3ac9d0f03a73a666c55cd67ae719195b99d722f98f8427a451aa5c2a59e17dcb
119551eb1e6cc8faa5f2dd3c40acf6e057da77d8bada7ed38c65da8bb90bb0e0
0742089f9db084085f47a4e164aa178fcb1b6c0016a2554521bc6c5716aef9ce
fcfbe4669491658a8fbeac85d53a5644dfe30b3d19db4659773c88c1cfbc2836
```

The bundle README warns that `image_linux` consumes **firmware.bin,
system_files.bin and firmware_isp_iq.bin**. Copying only `flash_ntz.bin`
previously flashed the wrong application. The first argument below is the
staged tools directory; all uploader arguments follow the task's vendor form.

```bash
~/.arduino15/packages/realtek/tools/ameba_pro2_tools/1.4.7/image_linux /tmp/vlc_capture7m_tools /dev/ttyUSB0 AMB82-MINI Enable Disable 2000000 uartfwburn.linux Auto_Flash_Pro2_V3.3_linux 0x60000 0x460000 0x530000
```

If UART boot fails: hold UART_DOWNLOAD, press/release RESET, release
UART_DOWNLOAD, then retry the uploader. After successful upload, the author
must press **RESET** to run the application. Query identity with a bounded
serial read:

```bash
.venv/bin/python - <<'PY'
import serial, time
with serial.Serial('/dev/ttyUSB0', 115200, timeout=.3, write_timeout=2, exclusive=True) as p:
    p.write(b'\nGOAL2 INFO\n')
    p.flush()
    end = time.monotonic() + 12
    while time.monotonic() < end:
        line = p.readline().decode(errors='replace').strip()
        if line:
            print(line)
        if line.startswith('[GOAL2_READY]'):
            assert 'paired-int8-pipeline-v2 accuracy=buffered-v1' in line
            assert 'preprocess=jpeg-gray8-v2 exposure_us=2000 gain=256' in line
            assert 'model_sha256=ce60f7f60045592f3618d717001c2962538d601175f952994802e5b9c04c8175' in line
            break
    else:
        raise RuntimeError('No correct original capture firmware identity after RESET')
PY
```

Do not proceed if the identity is AUG or otherwise mismatched. Identity is
build provenance, not sensor-register readback or flash readback.

Keep the existing TX if it is the matching sketch and acknowledges synthetic
mode. TX historical FQBN, bootloader/upload invocation and extra build flags
are **UNKNOWN** in the inspected locks, RX build notes and TX sketch. Do not
invent a TX flash command or flash the finite battery build; if TX needs
restoration, resolve its board configuration with the manager first. Locked
TX source SHA-256 is
`0d647928f0064e1b500949527c87b61bf4c475b8b8e1e0a1c4b8da0d41861178`.

Build the original portable CPU comparator, needed by every phase replay:

```bash
g++ -std=c++11 -O3 -DNDEBUG -DTF_LITE_STATIC_MEMORY \
  -I1_VLC_code_main/libraries/VLCInt8Reference/src \
  1_VLC_code_main/RX/Ameba_RX_NPU_Receiver/tests/cpu_reference_host.cpp \
  1_VLC_code_main/libraries/VLCInt8Reference/src/tensorflow/lite/kernels/internal/common.cc \
  1_VLC_code_main/libraries/VLCInt8Reference/src/tensorflow/lite/kernels/internal/runtime_shape.cc \
  -o /tmp/vlc_cpu_reference_host
```

This is the compile argv from `test_cpu_reference.py`; invoking that test itself
would also rewrite `dataset/diagnostics/.../host_parity.json`, so it is not
needed here. Replay supplies the per-window board/host parity check.

## 4. Choose ROI, then acquire and replay six sessions

Use the GUI on the hardware host; confirm the LED visually, use R to draw a
rectangle if the proposed hotspot is a lamp, and ENTER to confirm. Do not
use `--auto` without checking its result: its dark-image fallback is the
image center, not proof of an LED detection.

```bash
.venv/bin/python 1_VLC_code_main/AI_Pipeline_amebaPro2/roi_confirm.py --port /dev/ttyUSB0 --baud 115200
```

Record the confirmed x,y,w,h. New ROI coordinates are **UNKNOWN until physical
alignment**. Replace the placeholder once below with those four integers.
The example is intentionally not the legacy 7-m ROI. Keep variables in the
same shell for the remaining commands. The tag includes all four coordinates
to make the new sessions unambiguous; a new date/tag is required if names exist.

```bash
ROI7='UNKNOWN_X,UNKNOWN_Y,UNKNOWN_W,UNKNOWN_H'
```

```bash
.venv/bin/python -c 'import sys; x,y,w,h=map(int,sys.argv[1].split(",")); assert x>=0 and y>=0 and w>0 and h>0 and x+w<=320 and y+h<=192' "$ROI7"
```

```bash
CAPTURE_DATE=$(date +%Y%m%d)
```

```bash
ROI_TAG="roi${ROI7//,/_}"
```

```bash
BATCH_ON="goal2_gray8v2_phasev1_7m_${ROI_TAG}_exp2000_lights_on_${CAPTURE_DATE}"
```

```bash
BATCH_OFF="goal2_gray8v2_phasev1_7m_${ROI_TAG}_exp2000_lights_off_${CAPTURE_DATE}"
```

Prepare lamps-ON protocol (no acquisition yet). `--first-session 1 --repeats 3`
creates independent s01–s03, not three cuts of one recording.

```bash
.venv/bin/python -u 1_VLC_code_main/AI_Pipeline_amebaPro2/run_precrc_repeats.py \
  --prepare-only --batch "$BATCH_ON" --roi-px "$ROI7" \
  --capture-prefix "goal2_precrc_gray8v2_phasev1_7m_${ROI_TAG}_exp2000_lights_on_${CAPTURE_DATE}_100f" \
  --replay-prefix "goal2_replay_gray8v2_phasev1_7m_${ROI_TAG}_exp2000_lights_on_${CAPTURE_DATE}_100f" \
  --first-session 1 --repeats 3 --baseline-session none \
  --distance-m 7 --tx-port COMx --tx-ssh user@host --lighting room_lights_on \
  --alignment tracker-manchester-v1 --require-preprocess jpeg-gray8-v2 \
  --firmware-bin dataset/diagnostics/goal2_cpu_npu/gray8_exp2000_v2_upload_bundle/flash_ntz.bin
```

Inspect its `protocol_lock.json`: ROI, ports, 1010 s, 100 frames, 20 Hz,
alignment and preprocessing must match. Author confirms lamps ON, then run:

```bash
.venv/bin/python -u 1_VLC_code_main/AI_Pipeline_amebaPro2/run_precrc_repeats.py --run-lock "1_VLC_code_main/dataset/benchmark/$BATCH_ON/protocol_lock.json"
```

The wrapper performs capture s01 → replay s01 → capture s02 → replay s02 →
capture s03 → replay s03, stopping on errors/count/parity failure. It freezes
source/model/binary hashes and saves a portable comparator copy. No automatic
resume, overwrite, BER-dependent retries or frame exclusions are allowed.
Check section 5 before changing lamps.

Author switches lamps OFF, confirms unchanged geometry/ROI and lamp state.
Prepare and run the second batch:

```bash
.venv/bin/python -u 1_VLC_code_main/AI_Pipeline_amebaPro2/run_precrc_repeats.py \
  --prepare-only --batch "$BATCH_OFF" --roi-px "$ROI7" \
  --capture-prefix "goal2_precrc_gray8v2_phasev1_7m_${ROI_TAG}_exp2000_lights_off_${CAPTURE_DATE}_100f" \
  --replay-prefix "goal2_replay_gray8v2_phasev1_7m_${ROI_TAG}_exp2000_lights_off_${CAPTURE_DATE}_100f" \
  --first-session 1 --repeats 3 --baseline-session none \
  --distance-m 7 --tx-port COMx --tx-ssh user@host --lighting room_lights_off \
  --alignment tracker-manchester-v1 --require-preprocess jpeg-gray8-v2 \
  --firmware-bin dataset/diagnostics/goal2_cpu_npu/gray8_exp2000_v2_upload_bundle/flash_ntz.bin
```

```bash
.venv/bin/python -u 1_VLC_code_main/AI_Pipeline_amebaPro2/run_precrc_repeats.py --run-lock "1_VLC_code_main/dataset/benchmark/$BATCH_OFF/protocol_lock.json"
```

For clarity, the exact expanded host operations for **one** ON acquisition
and replay are below. These explain what the wrapper executes; **do not run
them again** after the wrapper. For OFF the lighting and name component
change to `off`; repeat numbers are s01, s02 and s03 in each condition.

```bash
.venv/bin/python -u 1_VLC_code_main/AI_Pipeline_amebaPro2/benchmark_precrc.py \
  --session "goal2_precrc_gray8v2_phasev1_7m_${ROI_TAG}_exp2000_lights_on_${CAPTURE_DATE}_100f_s01" \
  --duration 1010 --roi-px "$ROI7" --lighting room_lights_on \
  --distance-m 7 --tx-port /dev/ttyACM0 --rx-port /dev/ttyUSB0 \
  --require-preprocess jpeg-gray8-v2
```

```bash
.venv/bin/python -u 1_VLC_code_main/AI_Pipeline_amebaPro2/replay_precrc_buffer.py \
  "1_VLC_code_main/dataset/benchmark/goal2_precrc_gray8v2_phasev1_7m_${ROI_TAG}_exp2000_lights_on_${CAPTURE_DATE}_100f_s01" \
  --session "goal2_replay_gray8v2_phasev1_7m_${ROI_TAG}_exp2000_lights_on_${CAPTURE_DATE}_100f_s01" \
  --port /dev/ttyUSB0 --alignment tracker-manchester-v1 --validation-capture
```

The wrapper uses replay's default port `/dev/ttyUSB0`; it is explicit above.
There is no `--frames`, `--exposure`, `--gain`, or ROI-search option in this
capture CLI. Frame count is enforced after the fixed-duration capture;
`--require-preprocess` also fails closed on the 2000-us/gain-256 identity.
For historical reproduction, substitute the table's distance, ROI, date and
name component into these same commands; ports and algorithms are unchanged.

## 5. Acceptance checks for every session

Do not equate `[COMPLETE]` text alone with success. Inspect summaries and
batch status. The capture's `results.capture_stats.frames` counts camera
images (about 30,300), **not** optical frames (100). The buffer must have
**20,200 finite samples**, not 100 camera images.

- ROI found: author visually identifies the emitter; capture events must
  include the exact `[GOAL2_ROI] x,y,w,h` acknowledgement and matching
  `GOAL2_CAPTURE_BEGIN` ROI. A fixed ROI has no automatic `roi_found` field;
  a syntactically valid rectangle alone cannot prove it covers the LED.
- Capture: `complete=true`, `error=null`, 100 distinct scored frame IDs,
  4800 compared bits, nonzero signal range. Inspect camera elapsed/FPS,
  errors and `gaps100`; references' inspected s01 runs had `errors=0`,
  `gaps100=0`, approximately 30 FPS. Investigate faults; keep failed runs.
- Replay: `complete=true`, `error=null`, 100 frames/4800 bits, same ordered
  frame IDs and truth hex as capture, 192 input bytes and 48 output bytes
  per backend, `cpu_board_host_unequal_values=0`.
- Inspect tracker weak-fit/fallback counts, phase boundary hits, signed
  gains and optical shifts in `frame_decisions.json`/`REPORT.md`. Retain
  these windows. There is no established pass threshold for those counts
  in the locks; report them instead of selecting the best trial.
- CRC sanity: every transmitted truth frame must pass CRC-8/0x07 and begin
  with `1010`; decoded failures remain in the denominator. Report pre-CRC
  errors and exact windows for Classic/CPU/NPU. Zero BER or zero CPU/NPU
  disagreement is **not** a required channel acceptance criterion. CPU
  board/host parity is required. These scripts do not produce live PDR or
  a separate decoded CRC-pass counter; derive any such counter explicitly
  without filtering windows or treating CRC alone as payload correctness.

Read-only audit of both completed batches (run after each by supplying only
that batch name, or both once OFF finishes):

```bash
.venv/bin/python - "$BATCH_ON" "$BATCH_OFF" <<'PY'
from pathlib import Path
import json, math, sys
base = Path('1_VLC_code_main/dataset/benchmark')
def load(p):
    return json.loads(p.read_text())
def crc_ok(frame):
    value = int(frame, 16)
    crc = 0
    for bit in range(47, 7, -1):
        feedback = ((crc >> 7) & 1) ^ ((value >> bit) & 1)
        crc = ((crc << 1) & 255) ^ (7 if feedback else 0)
    return value >> 44 == 10 and crc == (value & 255)
for batch in sys.argv[1:]:
    root = base / batch
    lock = load(root/'protocol_lock.json')
    assert load(root/'status.json')['state'] == 'complete'
    agg = load(root/'aggregate.json')
    assert agg['complete'] and agg['completed_sessions'] == 3
    for capture, replay in zip(lock['capture_sessions'], lock['new_replay_sessions']):
        c, r = base/capture, base/replay
        cm, cs, rs = load(c/'metadata.json'), load(c/'summary.json'), load(r/'summary.json')
        assert cs['complete'] and cs['error'] is None
        assert cs['results']['frames_compared'] == 100 and cs['results']['bits_compared'] == 4800
        assert cm['roi'] == lock['roi'] and cm['distance_m'] == 7
        assert cm['lighting'] == lock['lighting'] and cm['require_preprocess'] == 'jpeg-gray8-v2'
        assert 'preprocess=jpeg-gray8-v2 exposure_us=2000 gain=256' in cm['runtime_identity']
        samples = load(c/'samples.json')
        assert len(samples) == 20200 and all(math.isfinite(x) for x in samples)
        assert max(samples) > min(samples)
        events = [json.loads(line) for line in (c/'events.jsonl').read_text().splitlines()]
        ack = '[GOAL2_ROI] ' + ','.join(map(str, lock['roi']))
        assert any(e['source'] == 'rx' and e['line'] == ack for e in events)
        source, rows = load(c/'frame_decisions.json'), load(r/'frame_decisions.json')
        assert rs['complete'] and rs['error'] is None
        assert rs['frames_compared'] == len(rows) == 100 and rs['bits_compared'] == 4800
        assert rs['cpu_board_host_unequal_values'] == 0
        assert load(r/'metadata.json')['alignment'] == 'tracker-manchester-v1'
        assert [(x['frame_id'], x['frame_hex']) for x in source] == [(x['frame_id'], x['frame_hex']) for x in rows]
        assert len({x['frame_id'] for x in rows}) == 100
        truth = {int(x['frame_id']): x['frame_hex'] for x in load(c/'tx_ground_truth.json') if x.get('payload_mode') == 'test'}
        for x in rows:
            assert int(truth[x['frame_id']], 16) == int(x['frame_hex'], 16)
            assert crc_ok(x['frame_hex'])
            assert len(bytes.fromhex(x['input_int8_hex'])) == 192
            assert len(bytes.fromhex(x['cpu_hex'])) == len(bytes.fromhex(x['npu_hex'])) == 48
            assert len(x['classic_bits']) == 48
        print(replay, 'PASS', cs['results']['capture_stats'])
        print('weak/fallback/phase_boundary:',
              sum(x['tracker']['weak_fit'] for x in rows),
              sum(x['tracker']['fallback'] for x in rows),
              sum(x['manchester_phase']['boundary_hit'] for x in rows))
        print({b: rs[b+'_preamble_polarity'] for b in ['classic', 'cpu', 'npu']})
PY
```

If acquisition fails, preserve the directory and logs; investigate the
failure and record a new explicitly named attempt. Do not silently replace
a failed or high-BER run. Use the buffered JSON acceptance audit above.

## 6. Corrected training and AUG evaluation

Corrected 7-m session selection is implemented through the paired environment
settings `RETRAIN_OUTPUT_ROOT=<workspace>/1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/results_v2`
and `RETRAIN_SESSION_SET=corrected-7m`. Do not use legacy defaults for final
results or overwrite existing raw recordings.

Follow the [training guide](AI_Pipeline_amebaPro2/retrain_sessions/README_aug.md)
for paired exports, repeat/LOSO/LODO and robustness. It selects the corrected
protocol locks and writes derived inputs under `results_v2/data/`.
Follow the [AUG conversion/replay runbook](AI_Pipeline_amebaPro2/ameba_deployment/aug/RUNBOOK.md)
v2 section for `work_v2/` conversion, AUG_V2 build, identity checks and board
replay. Preserve existing results before rerunning any writer; verify held-out
sessions remain disjoint from training, validation and calibration.

For the independent unseen-payload test, use
[CAPTURE_FRESH_TEST_RUNBOOK.md](CAPTURE_FRESH_TEST_RUNBOOK.md). Final scoring
replays stored tensors through AUG v2; capture-firmware CNN outputs are not
used. Optical capture, conversion, firmware upload and runtime identity require
verification on the hardware/toolchain host.
