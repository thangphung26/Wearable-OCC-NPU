#!/usr/bin/env python3
"""Export the 24 paper sessions as a paired, immutable training dataset."""
from session_config import DATASET, CORRECTED, LEGACY_DATA, load_manifest
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import tensorflow as tf

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
BENCH = ROOT / '1_VLC_code_main/dataset/benchmark'


def bits_hex(value):
    return np.array([(int(value, 16) >> b) & 1 for b in range(47, -1, -1)], dtype=np.uint8)


def decision_bytes(value):
    return (np.frombuffer(bytes.fromhex(value), dtype=np.uint8) < 128).astype(np.uint8)


def replay_input(row):
    """Return the stored INT8 window and its training-scale float input."""
    q = np.frombuffer(bytes.fromhex(row['input_int8_hex']), dtype=np.uint8).view(np.int8)
    assert q.size == 192
    x = ((q.astype(np.float32) + 128.0) / 255.0).reshape(48, 1, 4)
    return q, x


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check-only', action='store_true', help='Verify existing NPZ without rewriting it')
    args = parser.parse_args()
    manifest, _ = load_manifest()
    records = []
    for session in manifest['sessions']:
        name = session['replay']
        rows = json.loads((BENCH / name / 'frame_decisions.json').read_text())
        for row in rows:
            q, x = replay_input(row)
            truth = bits_hex(row['frame_hex'])
            classic = np.array([int(c) for c in row['classic_bits']], dtype=np.uint8)
            cpu = decision_bytes(row['cpu_hex'])
            npu = decision_bytes(row['npu_hex'])
            assert all(a.shape == (48,) for a in (truth, classic, cpu, npu))
            records.append((x, truth, name, session['distance_m'], session['lighting'], session['index'],
                            'jpeg-gray8-v2' if CORRECTED or session['distance_m'] != 7 else 'legacy-gray', row['frame_id'],
                            q, classic, cpu, npu))
    X = np.stack([r[0] for r in records]).astype(np.float32)
    arrays = dict(X=X, y=np.stack([r[1] for r in records]), session=np.array([r[2] for r in records]),
                  distance_m=np.array([r[3] for r in records]), lighting=np.array([r[4] for r in records]),
                  repeat=np.array([r[5] for r in records]), preproc=np.array([r[6] for r in records]),
                  frame_index=np.array([r[7] for r in records]), input_int8=np.stack([r[8] for r in records]),
                  classic_bits=np.stack([r[9] for r in records]), cpu_bits=np.stack([r[10] for r in records]),
                  npu_bits=np.stack([r[11] for r in records]))
    out = DATASET
    if args.check_only:
        with np.load(out) as stored:
            assert set(stored.files) == set(arrays)
            for key, value in arrays.items():
                assert np.array_equal(stored[key], value), key
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(out, **arrays)
    interpreter = tf.lite.Interpreter(model_path=str(ROOT / '1_VLC_code_main/AI_Pipeline_amebaPro2/vlc_edge_cnn_int8.tflite'))
    interpreter.allocate_tensors()
    inp, output = interpreter.get_input_details()[0], interpreter.get_output_details()[0]
    assert inp['dtype'] == np.int8 and output['dtype'] == np.int8
    assert inp['shape'].tolist() == [1, 48, 1, 4]
    assert np.isclose(inp['quantization'][0], 1 / 255) and inp['quantization'][1] == -128
    assert np.isclose(output['quantization'][0], 1 / 256) and output['quantization'][1] == -128
    matched = total = classic_errors = npu_errors = 0
    by_condition = {}
    for i in range(len(X)):
        interpreter.set_tensor(inp['index'], arrays['input_int8'][i:i+1].reshape(1, 48, 1, 4))
        interpreter.invoke()
        pred = (interpreter.get_tensor(output['index']).reshape(48) >= 0).astype(np.uint8)
        cpu = arrays['cpu_bits'][i]
        matched += int(np.sum(pred == cpu)); total += 48
        ce = int(np.sum(arrays['classic_bits'][i] != arrays['y'][i]))
        ne = int(np.sum(arrays['npu_bits'][i] != arrays['y'][i]))
        classic_errors += ce; npu_errors += ne
        key = f"{arrays['distance_m'][i]}m_{arrays['lighting'][i]}"
        counts = by_condition.setdefault(key, {'classic': 0, 'npu': 0})
        counts['classic'] += ce; counts['npu'] += ne
    if not CORRECTED:
        assert classic_errors == 12 and npu_errors == 71, (classic_errors, npu_errors)
    assert matched / total >= .9999, (matched, total)
    expected = {}
    for item in manifest['groups']:
        key = f"{item['distance_m']}m_{item['lighting']}"
        expected[key] = {'classic': item['classic']['errors'], 'npu': item['npu']['errors']}
    assert expected == by_condition, (expected, by_condition)
    assert len(X) == 2400 and len(set(arrays['session'])) == 24
    counts = {s: int(np.sum(arrays['session'] == s)) for s in dict.fromkeys(arrays['session'])}
    assert set(counts.values()) == {100}
    if CORRECTED:
        with np.load(LEGACY_DATA) as old:
            old_mask = old['distance_m'] <= 5
            new_mask = arrays['distance_m'] <= 5
            for key, value in arrays.items():
                assert np.array_equal(old[key][old_mask], value[new_mask]), key
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    print(json.dumps({'frames_per_session': counts, 'sessions': len(counts), 'unchanged_1_3_5m': CORRECTED, 'frames': len(X), 'bits': total, 'deployed_cpu_matches': matched,
                      'match_rate': matched / total, 'classic_errors': classic_errors,
                      'npu_errors': npu_errors, 'per_condition_match': True, 'dataset_sha256': digest}, indent=2))


if __name__ == '__main__':
    main()
