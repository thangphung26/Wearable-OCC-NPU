#!/usr/bin/env bash
set -euo pipefail
dry=0
port=/dev/ttyUSB0
while (( $# )); do
  case "$1" in
    --dry-run) dry=1; shift ;;
    --port) test "$#" -ge 2; port="$2"; shift 2 ;;
    *) echo "Usage: bash $0 [--dry-run] [--port /dev/ttyUSB0]" >&2; exit 2 ;;
  esac
done
[[ "$port" == /dev/tty* ]]
cli=/home/usnlab/ros2_ws/tools/arduino-cli/arduino-cli
repo="$(pwd)"
paths="$("$repo/.venv/bin/python" "$repo/1_VLC_code_main/AI_Pipeline_amebaPro2/ameba_deployment/aug/common.py" --shell-paths)"
mapfile -t selected <<< "$paths"
work="${selected[0]}"
model_dir="${selected[1]}"
sketch="${selected[2]}"
build="${selected[3]}"
tools=/home/usnlab/.arduino15/packages/realtek/tools/ameba_pro2_tools/1.4.7
cmd=("$cli" upload --fqbn realtek:AmebaPro2:Ameba_AMB82-MINI
  --port "$port" --input-dir "$build"
  "${sketch#"$repo/"}")
printf '%q ' "${cmd[@]}"
printf '\n'
if (( dry )); then exit 0; fi
test -c "$port"
test -s "$build/flash_ntz.bin"
# Vendor uploader uses this shared tools file; do not upload a stale firmware.
cmp "$build/flash_ntz.bin" "$tools/flash_ntz.bin"
"${cmd[@]}"
