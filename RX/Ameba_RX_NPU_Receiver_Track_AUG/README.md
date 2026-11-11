# Tracking receiver with AUG v2

Paths and commands below are relative to the workspace root unless absolute.
This live receiver was derived from the earlier CH4 tracking receiver, which
is no longer distributed. It embeds the final AUG v2 model. The recorded
source comparison found only model-header and boot-identity changes; tracking,
frame validation and UART behavior were preserved.

Tracking, Classic fallback, acceptance/frame guard, and UART telemetry JSON
are identical to the original. Rates remain **30 FPS acquisition**, **20 Hz
resampling**, and **5 bit/s link**. No vendor compile, upload, serial access,
firmware execution, or git command was performed for this task.

## Provenance and byte verification

Paths in this table start at `1_VLC_code_main/`.

| Artifact | Path | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| Original embedded CH4 NBG | `AI_Pipeline_amebaPro2/ameba_deployment/vlc_edge_cnn_ch4.nb` | 62688 | `2838c4db8f34af99599c528a765d254e6ab82c8d2d76bdaedce8079cacdd9fb8` |
| AUG v2 NBG and new embedded array | `AI_Pipeline_amebaPro2/ameba_deployment/aug/work_v2/vlc_edge_cnn_aug_v2.nb` | 62688 | `c3f0eec20652e9e786904bb5bd0281e87903eeb0fae19d9622181431627fc097` |
| AUG v2 TFLite | `AI_Pipeline_amebaPro2/retrain_sessions/results_v2/repeat_aug/deployment_candidate_aug_int8.tflite` | 33168 | `ef9076f8f5c08cf17ded2e5b5374f92d40b814ab0ad084462a7fe1eaa9e373ca` |

The original Track array was extracted and compared byte-for-byte to
`vlc_edge_cnn_ch4.nb`: equal. Its matching metadata is
`ameba_deployment/vlc_edge_cnn_ch4_nbg_meta.json`. Older UINT8, non-CH4 metadata is not part of this model contract.

A standalone historical header-generation script/invocation was not found
in the deployment scripts or available agent logs. The original header is
reproduced **exactly, including whitespace**, by `xxd -i` followed by symbol,
`const`, and `aligned(64)` substitutions. This verified conversion method
was used for AUG v2, preserving `vlc_edge_cnn_nb`, `vlc_edge_cnn_nb_len`, and
64-byte alignment. This establishes the identical conversion output format,
not an independently recovered historical command.

The complete conversion used from the workspace root is:

```bash
.venv/bin/python - <<'PYGEN'
from pathlib import Path
import re, subprocess
src = Path('1_VLC_code_main/AI_Pipeline_amebaPro2/ameba_deployment/aug/work_v2/vlc_edge_cnn_aug_v2.nb')
out = Path('1_VLC_code_main/RX/Ameba_RX_NPU_Receiver_Track_AUG/vlc_edge_cnn_nb.h')
s = subprocess.check_output(['xxd', '-i', str(src)], text=True)
s = re.sub(r'unsigned char \w+\[\]', 'const unsigned char __attribute__((aligned(64))) vlc_edge_cnn_nb[]', s, count=1)
s = re.sub(r'unsigned int \w+_len', 'const unsigned int vlc_edge_cnn_nb_len', s, count=1)
out.write_text(s)
PYGEN
```

The new header array was independently extracted from its initializer:
**62688 bytes, byte-for-byte equal to the AUG `.nb`, SHA-256 as above**.
The declared length also equals 62688. Both complete metadata JSON objects
are equal. Existing `aug/work_v2/check.json` reports 1.0 simulator/TFLite bit
agreement over 800 windows with these same model hashes; that historical
simulation was read, not rerun. `RX/Ameba_CPU_NPU_Benchmark_AUG_V2/provenance.json`
also records these same NBG and TFLite hashes. No existing model, firmware,
source sketch, library, or dataset artifact was modified.

## Model-specific constant audit

Line references below refer to the original Track `.ino`; the copy has the
same line numbers. Original and AUG metadata use one input and one output,
NHWC, `asymmetric_affine`, and no recurrent connections.

| Dependency / source location | Original CH4 | AUG v2 | Difference / action |
| --- | --- | --- | --- |
| Input tensor shape; ready string L285 | `[1,48,1,4]` | `[1,48,1,4]` | None |
| Output tensor shape; ready string L285 | `[1,48,1]` | `[1,48,1]` | None |
| Input/output dtype; L276–278 and INT8 mappings | signed INT8 / `i8` | signed INT8 / `i8` | None |
| Input scale; implicit in L497, L619 | `0.003921568859368563` (float32 1/255) | same | None |
| Input zero point; L497, L619 | `-128` | `-128` | None |
| Output scale; implicit in L67, L369–370, L517 | `0.00390625` (1/256) | same | None |
| Output zero point; implicit in threshold | `-128` | `-128` | None |
| Input metadata range | `[0,1.0000000591389835]` | same | None |
| Output metadata range | `[0,0.99609375]` | same | None |
| `FRAME_BITS`, `FRAMES_PER_BIT`, `WINDOW_SAMPLES`; L37–39 | 48, 4, 192 | same | None; flat index `bit*4+j` |
| `MODEL_SAMPLE_HZ`, sample period; L35–36 | 20 Hz, 50000 us | same | None; acquisition is separately 30 FPS |
| Both input normalization/quantization paths; L483–502, L607–624 | per-bit min/max, amplitude >0.5 luma; `int(x*255+0.5)-128`, clamp [-128,127] | same | None; normalization cutoff is receiver policy, not NBG metadata |
| `OUTPUT_ONE_THRESHOLD`; L67, L369–370, L517 | raw `>=0`, real probability >=0.5 | same | None; threshold is receiver policy, compatible with both quantizers |
| Runtime tensor properties; L247–262 | query rank, dimensions, dtype, quant format, scale, zero point | same | None; passed to buffer creation |
| Runtime contract check; L276–279 | 192 input elements, 48 output elements, both INT8 | same | None; existing check does not separately enforce shape or scale |
| Tensor names | `serving_default_vlc_features/0`, `StatefulPartitionedCall_1/0` | same | None; runtime uses index 0, not names |
| Embedded symbols/alignment; L266–271 | `vlc_edge_cnn_nb`, length 62688, aligned(64) | same | None; payload changes |
| Embedded NBG SHA-256 | `2838c4db…dd9fb8` | `c3f0eec2…7fc097` | Changed model payload |
| Boot `[BUILD]`; L1179 | `edge-perf-v2-framed-track-v1`, no SHA | same prefix plus `AUG-v2`, full NBG and TFLite SHA-256 | Identity only |
| Boot ready contract; L285 | `INT8 [1,48,1,4] -> [1,48,1]` | same | None |

There are no other model SHA/name constants in the Track sketch. Neither
quantization parameter differs, so the stop condition was not triggered.
For completeness, the older non-CH4 metadata differs: input UINT8
`[1,48,4,1]`, zero point 0; output UINT8, scale `0.0038968673907220364`,
zero point 0. It is excluded because its bytes do not match the original
Track header.

The new boot identity is exactly:

```text
[BUILD] edge-perf-v2-framed-track-v1 AUG-v2 nbg_sha256=c3f0eec20652e9e786904bb5bd0281e87903eeb0fae19d9622181431627fc097 tflite_sha256=ef9076f8f5c08cf17ded2e5b5374f92d40b814ab0ad084462a7fe1eaa9e373ca
```

These are build-time provenance strings, not runtime hashing. The NPU-ready
line and accepted telemetry must also be observed on hardware to establish
successful execution.

## Directory diff and files created

All seven files in this directory are new copies:

- `Ameba_RX_NPU_Receiver_Track_AUG.ino`: Arduino-required filename change;
  compared with the original `.ino`, exactly one `[BUILD]` string differs.
- `vlc_edge_cnn_nb.h`: new AUG NBG bytes, same symbols, length and alignment.
- `README.md`: this provenance, audit, and manager handoff.
- `edge_perf.h`, `frame_guard.h`, `led_tracking.h`, and
  `tests/tracking_test.cpp`: byte-identical copies.

`diff -r` of the original and AUG directories reports the original `.ino`
only in the original directory, renamed `.ino` only in AUG, and differences
in `README.md` and `vlc_edge_cnn_nb.h`. Comparing the renamed sketch directly
reports only the boot identity line. Every other file and the receiver logic
match exactly. The README and required Arduino filename change are the only
non-header/non-identity differences.

The source comparison above is a historical provenance record; the original
Track directory is no longer included, so that diff cannot be rerun here.

## Claude: compile, stage, upload (not executed)

Run each shell command separately from the workspace root on the hardware
host. Do not allow another Arduino build between compilation, staging and
upload: vendor hooks overwrite shared component images.

**Actual Track optimization is `-Os`.** Evidence:
`/tmp/vlc_rx_track_build/build.options.json` has
`compiler.optimization_flags=-Os`, empty `customBuildProperties`, and the
Track sketch location. Its `compile_commands.json` also has `-Os` for the
sketch and no `TF_LITE_STATIC_MEMORY` or `NDEBUG` defines. The recorded original compile command had no optimization override. The explicit `-Os` below freezes
that effective setting. The `-O3 -DTF_LITE_STATIC_MEMORY -DNDEBUG` used by
AUG benchmark builds is a different configuration.

Use fresh build/tools destinations; stop if either exists:

```bash
test ! -e /tmp/vlc_rx_track_aug_build
```

```bash
test ! -e /tmp/vlc_rx_track_aug_tools
```

```bash
/home/usnlab/ros2_ws/tools/arduino-cli/arduino-cli compile \
  --fqbn realtek:AmebaPro2:Ameba_AMB82-MINI \
  --libraries 1_VLC_code_main/libraries \
  --build-property 'compiler.optimization_flags=-Os' \
  --build-path /tmp/vlc_rx_track_aug_build \
  1_VLC_code_main/RX/Ameba_RX_NPU_Receiver_Track_AUG
```

Require a successful compile. Record the new image size/hash; no AUG receiver
image exists from this executor task.

```bash
stat -c '%n %s bytes' /tmp/vlc_rx_track_aug_build/flash_ntz.bin
```

```bash
sha256sum /tmp/vlc_rx_track_aug_build/flash_ntz.bin
```

Immediately compare the build combined image with the shared tools output,
then copy the **entire freshly generated tools directory**, as in
`../../CAPTURE_7M_RUNBOOK.md` section 3 and its AUG rebuild procedure:

```bash
cmp /tmp/vlc_rx_track_aug_build/flash_ntz.bin /home/usnlab/.arduino15/packages/realtek/tools/ameba_pro2_tools/1.4.7/flash_ntz.bin
```

```bash
cp -a /home/usnlab/.arduino15/packages/realtek/tools/ameba_pro2_tools/1.4.7 /tmp/vlc_rx_track_aug_tools
```

```bash
cmp /tmp/vlc_rx_track_aug_build/flash_ntz.bin /tmp/vlc_rx_track_aug_tools/flash_ntz.bin
```

```bash
sha256sum /tmp/vlc_rx_track_aug_tools/firmware.bin /tmp/vlc_rx_track_aug_tools/system_files.bin /tmp/vlc_rx_track_aug_tools/firmware_isp_iq.bin /tmp/vlc_rx_track_aug_tools/flash_ntz.bin
```

Record all four hashes. `image_linux` consumes the component images
`firmware.bin`, `system_files.bin`, and `firmware_isp_iq.bin`; copying only
`flash_ntz.bin` can upload the wrong application. Use these newly built AUG
components; no archived capture or benchmark bundle is substituted.
Confirm the RX device path on the hardware host before upload.

Enter download mode (hold UART_DOWNLOAD, press/release RESET, then release
UART_DOWNLOAD). The following uses the uploader argument form from
`CAPTURE_7M_RUNBOOK.md` section 3 and the private staged executable/tools:

```bash
/tmp/vlc_rx_track_aug_tools/image_linux /tmp/vlc_rx_track_aug_tools /dev/ttyUSB0 AMB82-MINI Enable Disable 2000000 uartfwburn.linux Auto_Flash_Pro2_V3.3_linux 0x60000 0x460000 0x530000
```

Require `upload success` in the output; exit status alone is insufficient.
Press RESET once after upload. Capture boot logs and verify the full `[BUILD]`
line above and `[PASS] NPU model ready: INT8 [1,48,1,4] -> [1,48,1]` before
re-running the autonomous demo. Receiver tracking, Classic fallback,
acceptance/UART behavior, timing, and actual AUG live delivery still require
hardware verification. The 800-window offline parity record alone does not
establish live autonomous performance.

## Executor verification and remaining work

Executed: read-only `pwd`, `cat`, `find`, `ls`, `sed`; root `.venv/bin/python`
for search, metadata/hash comparison, guarded copy, generation and byte/source
checks; `xxd -i`; directory and renamed-sketch `diff` commands. `rg` was
unavailable, so searches used Python/find. No compilation or hardware test
was run; unchanged tracking tests were copied, not rerun.

Open items: Claude compiles, records the firmware/component hashes, stages
and uploads, verifies boot identity and NPU readiness, and repeats the live
autonomous demo on hardware. Historical header-generator provenance remains
limited to exact output reproduction with `xxd`, as stated above.
