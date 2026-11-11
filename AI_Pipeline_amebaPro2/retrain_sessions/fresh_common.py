"""Read-only fresh-campaign contracts shared by replay and scoring."""
import os
import sys
os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
os.environ.setdefault('TF_NUM_INTRAOP_THREADS', '1')
os.environ.setdefault('TF_NUM_INTEROP_THREADS', '1')
sys.dont_write_bytecode = True
import json
import re
from pathlib import Path
import numpy as np
from eval_controls import BENCH, HERE, bits_hex, decision_bytes, replay_input, digest, ci, metric, tflite_predict

FRESH = HERE / 'results_v2/fresh'
AUG = HERE / 'results_v2/repeat_aug/deployment_candidate_aug_int8.tflite'
RETRAINED = HERE / 'results_v2/repeat/deployment_candidate_int8.tflite'
AUG_SHA = 'ef9076f8f5c08cf17ded2e5b5374f92d40b814ab0ad084462a7fe1eaa9e373ca'
SCHEMA = dict(version=1, conditions=[dict(name='unique condition name', distance_m=3,
    lighting='on or off', sessions=[dict(session='benchmark replay directory basename', repeat=1)])],
    excluded=[dict(session='excluded replay basename', reason='required nonempty reason')])
RULE = ('All listed non-excluded windows, no CRC/error/weak-fit filtering. TX frame_hex is 48 bits MSB first. '
        'Reuse eval_controls/export_dataset bits_hex, replay_input, decision_bytes and train_eval.ci/metric. Host inference uses builtin kernels without default delegates, one thread. '
        'Polarity is explicit: direct or per-decoder preamble-only correction; never truth-selected inversion. '
        'Host probabilities use model output scale/zero-point and >=0.5; AUG INT8 >=0 maps to 1 '
        '(scale 1/256, zero -128). Exact two-sided 95% Clopper-Pearson; pooled bits/windows may be correlated. '
        'Capture CPU/NPU values are ORIGINAL CNN and never used as AUG. 30 FPS acquisition, 20 Hz resampling, 5 bit/s link.')


def load_json(path):
    return json.loads(Path(path).read_text())


def session_name(name):
    if not isinstance(name, str) or not name or Path(name).name != name or name in ('.', '..'):
        raise ValueError('Session must be a benchmark directory basename')
    return name


def parse_manifest(data):
    if data.get('version') != 1 or not isinstance(data.get('excluded'), list):
        raise ValueError('Manifest requires version=1 and explicit excluded list')
    excluded = set()
    for e in data['excluded']:
        n = session_name(e['session'])
        if n in excluded or not isinstance(e.get('reason'), str) or not e['reason'].strip():
            raise ValueError('Duplicate exclusion or missing reason')
        excluded.add(n)
    records, seen, names = [], set(), set()
    for c in data['conditions']:
        if not c['name'] or c['name'] in names or c['lighting'] not in ('on', 'off') or c['distance_m'] not in (1, 3, 5, 7):
            raise ValueError('Invalid/duplicate condition')
        names.add(c['name']); repeats = set()
        for s in c['sessions']:
            n = session_name(s['session']); repeat = s['repeat']
            if n in seen or type(repeat) is not int or repeat < 1 or repeat in repeats:
                raise ValueError('Duplicate session/repeat or invalid repeat')
            seen.add(n); repeats.add(repeat)
            if n not in excluded:
                records.append(dict(session=n, repeat=repeat, condition=c['name'],
                                    distance_m=c['distance_m'], lighting=c['lighting']))
    if not records:
        raise ValueError('No included sessions')
    return records


def hex_value(value, size):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-fA-F]{%d}' % (2*size), value):
        raise ValueError(f'Expected {size} hex bytes')
    return value.lower()


def frame_hex(value):
    return hex_value(value.removeprefix('0x').removeprefix('0X'), 6)


def load_windows(sessions):
    records = []
    for item in sessions:
        name = session_name(item['session']); folder = BENCH / name
        if folder.resolve().parent != BENCH.resolve():
            raise ValueError('Session outside benchmark')
        status = load_json(folder/'summary.json')
        if not status['complete'] or status.get('error') is not None:
            raise ValueError('Incomplete replay: '+name)
        meta = load_json(folder/'metadata.json')
        if meta.get('alignment') != 'tracker-manchester-v1':
            raise ValueError('Expected tracker-manchester-v1: '+name)
        capture = BENCH / Path(meta['source_capture']).name
        truth_rows = [r for r in load_json(capture/'tx_ground_truth.json') if r.get('payload_mode') == 'test']
        truth = {int(r['frame_id']): frame_hex(r['frame_hex']) for r in truth_rows}
        rows = load_json(folder/'frame_decisions.json'); ids = set()
        if len(truth) != len(truth_rows) or not rows:
            raise ValueError('Duplicate TX IDs or empty replay')
        for row in rows:
            fid = row['frame_id']; word = frame_hex(row['frame_hex'])
            if type(fid) is not int or fid in ids or truth.get(fid) != word:
                raise ValueError('Duplicate frame or TX truth mismatch')
            ids.add(fid)
            hex_value(row['input_int8_hex'], 192)
            if not re.fullmatch('[01]{48}', row['classic_bits']):
                raise ValueError('Invalid Classic bits')
            q, _ = replay_input(row)
            records.append(dict(**item, frame_id=fid, frame_hex=word, q=q, source=row))
        # TX logs can include leading/trailing transmissions outside the captured
        # replay window range. Score every stored window, as eval_controls does.
        if status['frames_compared'] != len(rows) or status['bits_compared'] != len(rows)*48:
            raise ValueError('Replay summary/window count mismatch')
    return records


def output_path(path):
    path = Path(path).resolve()
    if FRESH.resolve() not in path.parents:
        raise ValueError('Output must be a new directory under results_v2/fresh/')
    if path.exists():
        raise FileExistsError('Refusing to overwrite: '+str(path))
    return path


def reference_inference(path, qs):
    from tensorflow.lite.python.interpreter import Interpreter, OpResolverType
    it = Interpreter(model_path=str(path), num_threads=1,
                     experimental_op_resolver_type=OpResolverType.BUILTIN_WITHOUT_DEFAULT_DELEGATES)
    it.allocate_tensors(); inp = it.get_input_details()[0]; out = it.get_output_details()[0]
    if inp['dtype'] != np.int8 or out['dtype'] != np.int8 or tuple(inp['shape']) != (1,48,1,4) or tuple(out['shape']) != (1,48,1):
        raise ValueError('Unexpected model tensor contract')
    if not np.isclose(inp['quantization'][0], 1/255) or inp['quantization'][1] != -128 or not np.isclose(out['quantization'][0], 1/256) or out['quantization'][1] != -128:
        raise ValueError('Unexpected model quantization')
    values = []
    for q in qs:
        it.set_tensor(inp['index'], q.reshape(1,48,1,4)); it.invoke()
        values.append(it.get_tensor(out['index']).reshape(48).copy())
    raw = np.stack(values)
    scale, zero = out['quantization']
    bits = ((raw.astype(np.float32)-zero)*scale >= .5).astype(np.uint8)
    return raw, bits


def raw_aug(qs):
    if digest(AUG) != AUG_SHA:
        raise ValueError('Unexpected AUG v2 model')
    return reference_inference(AUG, qs)[0]


def apply_polarity(bits, polarity='direct'):
    """Return corrected bits and a flip flag using only decoded preamble bits."""
    if polarity not in ('direct', 'preamble'):
        raise ValueError('Unknown polarity: '+polarity)
    bits = np.asarray(bits, dtype=np.uint8)
    preamble = np.array([1, 0, 1, 0], dtype=np.uint8)
    flip = polarity == 'preamble' and np.sum(bits[:4] != 1-preamble) < np.sum(bits[:4] != preamble)
    return (1-bits if flip else bits.copy()), bool(flip)


def payload_check(words, training):
    words = [frame_hex(w) for w in words]; training = {frame_hex(w) for w in training}
    if not words or not training:
        raise ValueError('Empty payload set')
    nearest = [min((int(w,16)^int(t,16)).bit_count() for t in training) for w in words]
    overlap = sum(w in training for w in words)
    return dict(training_words=len(training), windows=len(words), distinct_words=len(set(words)),
        overlap_count=overlap, distinct_overlap_count=len(set(words)&training), overlap_fraction=overlap/len(words),
        nearest_hamming_min=min(nearest), nearest_hamming_mean=float(np.mean(nearest)),
        hamming_weighting='one observation per included window', passed=overlap == 0)


def training_words():
    with np.load(HERE/'results_v2/data/dataset.npz', allow_pickle=False) as data:
        sessions = sorted(set(data['session'].tolist()))
    words = {frame_hex(r['frame_hex']) for s in sessions for r in load_json(BENCH/s/'frame_decisions.json')}
    if len(words) != 100:
        raise ValueError('Expected exactly 100 training payload words')
    return words
