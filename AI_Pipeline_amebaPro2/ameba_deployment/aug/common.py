"""Shared, read-only input validation for the augmented board evaluation."""
import hashlib
import json
import os
import sys
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PIPE = ROOT / 'AI_Pipeline_amebaPro2'
SESS = PIPE / 'retrain_sessions'
# Resolve the opt-in once; every consumer uses these selected paths.
output_set = 'RETRAIN_OUTPUT_ROOT' in os.environ
session_set = 'RETRAIN_SESSION_SET' in os.environ
assert output_set == session_set, 'Set both RETRAIN_OUTPUT_ROOT and RETRAIN_SESSION_SET, or neither'
sys.path.insert(0, str(SESS))
from session_config import RESULTS, DATASET, CORRECTED
V2 = output_set
if V2:
    assert RESULTS == SESS / 'results_v2' and CORRECTED, 'AUG v2 requires results_v2 and corrected-7m'
MODEL = RESULTS / 'repeat_aug/deployment_candidate_aug_int8.tflite'
HISTORY = RESULTS / 'repeat_aug/history_all.json'
DATA = DATASET
CACHE = DATA.with_name('windows_offsets.npz')
SUFFIX = '_v2' if V2 else ''
WORK = HERE / ('work' + SUFFIX)
NBG_STEM = 'vlc_edge_cnn_aug' + SUFFIX
NBG_NAME = NBG_STEM + '.nb'
META_NAME = NBG_STEM + '_nbg_meta.json'
SKETCH = ROOT / ('RX/Ameba_CPU_NPU_Benchmark_AUG' + ('_V2' if V2 else ''))
BUILD = Path('/tmp/vlc_goal2_aug' + SUFFIX + '_build')
REPLAY_OUT = RESULTS / 'onboard_aug'
LATENCY_OUT = RESULTS / 'onboard_aug_latency'
PARITY = HERE / ('host_parity' + SUFFIX + '.json')
ROBUSTNESS = SESS / 'robustness.py'
EXPECTED_MODEL_SHA = ('ef9076f8f5c08cf17ded2e5b5374f92d40b814ab0ad084462a7fe1eaa9e373ca'
                      if V2 else 'f1248c13ba5e6e5a3e7225d19a72239b6aefe932abc435a140e15dd26890e586')
# Keep the legacy dry-run JSON unchanged.
CONVERSION_NAMES = dict(nbg_stem=NBG_STEM) if V2 else {}


def benchmark_paths(session):
    return dict(sketch=str(SKETCH), build=str(BUILD), output=str(LATENCY_OUT/session)) if V2 else {}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def interpreter(reference=False):
    try:
        from tflite_runtime.interpreter import Interpreter, OpResolverType
    except ImportError:
        from tensorflow.lite.python.interpreter import Interpreter, OpResolverType
    it = Interpreter(model_path=str(MODEL), num_threads=1,
                     experimental_preserve_all_tensors=reference,
                     experimental_op_resolver_type=OpResolverType.BUILTIN_REF if reference else OpResolverType.BUILTIN_WITHOUT_DEFAULT_DELEGATES)
    it.allocate_tensors()
    for detail, shape, scale in zip([it.get_input_details()[0], it.get_output_details()[0]],
                                    [(1,48,1,4), (1,48,1)], [1/255,1/256]):
        assert tuple(detail['shape']) == shape
        assert detail['dtype'] == np.int8
        assert abs(detail['quantization'][0]-scale) < 1e-8 and detail['quantization'][1] == -128
    return it


def predict(x, reference=False):
    it = interpreter(reference)
    inp, out = it.get_input_details()[0], it.get_output_details()[0]
    ys = []
    for v in x:
        it.set_tensor(inp['index'], v.reshape(inp['shape']))
        it.invoke()
        ys.append(it.get_tensor(out['index']).reshape(48))
    return np.stack(ys)


def baseline():
    d = np.load(DATA, allow_pickle=False)
    ids = np.flatnonzero(d['repeat'] == 3)
    tests = set(d['session'][ids].tolist())
    assert len(ids) == 800 and len(tests) == 8
    assert digest(MODEL) == EXPECTED_MODEL_SHA, 'Unexpected AUG candidate'
    histories = json.loads(HISTORY.read_text())
    for h in histories:
        assert set(h['test_sessions']) == tests
        assert tests.isdisjoint(set(h['train_sessions']) | set(h['validation_sessions']) |
                                set(h['calibration_sessions']))
    x, y = d['input_int8'][ids], d['y'][ids]
    assert x.dtype == np.int8 and x.shape == (800,192)
    assert y.shape == (800,48) and np.isin(y, [0,1]).all()
    return d, ids


def inputs(with_offsets=True):
    d, ids = baseline()
    groups = [(0, d['input_int8'][ids])]
    if with_offsets:
        import sys
        sys.path.insert(0, str(SESS))
        from robustness import normalize, regenerate, sample
        c = np.load(CACHE, allow_pickle=False)
        assert np.array_equal(c['session'], d['session'])
        assert np.array_equal(c['frame_id'], d['frame_index'])
        assert np.array_equal(c['truth'], d['y'])
        frames = None
        for offset in [-40,-25,25,40]:
            hit = np.flatnonzero(c['offsets_ms'] == offset)
            if len(hit):
                w = c['windows'][ids, int(hit[0])]
            else:
                if frames is None:
                    fs, _, _, _ = regenerate()
                    by_key = {(f['session'], f['frame_id']): f for f in fs}
                    frames = [by_key[(str(d['session'][i]), int(d['frame_index'][i]))] for i in ids]
                    assert np.array_equal(np.stack([f['truth'] for f in frames]), d['y'][ids])
                w = np.array([[sample(f['values'], f['start']+offset/1000+(j+.5)*.05)
                               for j in range(192)] for f in frames])
            assert np.isfinite(w).all()
            groups.append((offset, np.stack([np.frombuffer(normalize(v), np.int8) for v in w])))
    return d, ids, groups


if __name__ == '__main__':
    assert sys.argv[1:] == ['--shell-paths']
    print('\n'.join(map(str, [WORK, MODEL.parent, SKETCH, BUILD])))
