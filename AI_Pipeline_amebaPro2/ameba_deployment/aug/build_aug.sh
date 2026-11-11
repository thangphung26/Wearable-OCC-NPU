#!/usr/bin/env bash
set -euo pipefail
case "${1:-}" in
  --dry-run) dry=1 ;;
  "") dry=0 ;;
  *) echo "Usage: bash $0 [--dry-run]" >&2; exit 2 ;;
esac
repo="$(pwd)"
paths="$("$repo/.venv/bin/python" "$repo/1_VLC_code_main/AI_Pipeline_amebaPro2/ameba_deployment/aug/common.py" --shell-paths)"
mapfile -t selected <<< "$paths"
work="${selected[0]}"
model_dir="${selected[1]}"
sketch="${selected[2]}"
build="${selected[3]}"
cli=/home/usnlab/ros2_ws/tools/arduino-cli/arduino-cli
test -x "$cli"
test -f "$sketch/vlc_cpu_model.h"
# Preserve Ameba_AMB82-MINI's boards.txt defaults (AmebaPro2 4.1.0).
extra_flags="-DARDUINO_SDK -DARDUINO_AMBPRO2 -DBOARD_AMB82_MINI {build.usb_flags} {build.menu_flags}"
cmd=("$cli" compile --fqbn realtek:AmebaPro2:Ameba_AMB82-MINI
  --libraries "$repo/1_VLC_code_main/libraries"
  --build-property "build.extra_flags=$extra_flags -I$repo/1_VLC_code_main/libraries/VLCInt8Reference/src"
  --build-property "compiler.optimization_flags=-O3 -DTF_LITE_STATIC_MEMORY -DNDEBUG"
  --build-path "$build" "$sketch")
printf '%q ' "${cmd[@]}"
printf '\n'
if (( dry )); then exit 0; fi
"$repo/.venv/bin/python" "$repo/1_VLC_code_main/AI_Pipeline_amebaPro2/ameba_deployment/aug/check_nbg.py"
"$repo/.venv/bin/python" "$repo/1_VLC_code_main/AI_Pipeline_amebaPro2/ameba_deployment/aug/export_cpu.py"
"${cmd[@]}"
stat -c '%n %s bytes' "$build/flash_ntz.bin"
sha256sum "$build/flash_ntz.bin"
