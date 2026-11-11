#!/usr/bin/env bash
set -euo pipefail
# Run from repository root. No original artifact or toolkit directory is writable.
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
aug="$repo/1_VLC_code_main/AI_Pipeline_amebaPro2/ameba_deployment/aug"
test -f "$aug/common.py"
"$repo/.venv/bin/python" "$aug/prepare_conversion.py" --validate-only
cmd=(docker run --rm --user "$(id -u):$(id -g)"
  -e HOME=/tmp -w /work
  -v "$work:/work"
  -v "$model_dir:/model:ro"
  -v "$aug/container_convert.py:/runner.py:ro"
  ghcr.io/ameba-aiot/acuity-toolkit:6.18.8 python3 /runner.py)
printf '%q ' "${cmd[@]}"
printf '\n'
if (( dry )); then exit 0; fi
test ! -e "$work"
"$repo/.venv/bin/python" "$aug/prepare_conversion.py"
"${cmd[@]}"
"$repo/.venv/bin/python" "$aug/check_nbg.py"
