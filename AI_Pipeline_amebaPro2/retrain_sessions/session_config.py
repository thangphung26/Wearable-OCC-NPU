"""Shared opt-in paths/session selection; no environment overrides retain legacy behavior."""
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BENCH = ROOT / '1_VLC_code_main/dataset/benchmark'
RESULTS = Path(os.environ.get('RETRAIN_OUTPUT_ROOT', str(HERE / 'results'))).resolve()
CORRECTED = os.environ.get('RETRAIN_SESSION_SET', 'legacy') == 'corrected-7m'
assert os.environ.get('RETRAIN_SESSION_SET', 'legacy') in ('legacy', 'corrected-7m')
LEGACY_DATA = ROOT / '1_VLC_code_main/dataset/retrain_sessions/dataset.npz'
if CORRECTED:
    assert RESULTS != HERE / 'results', 'Corrected sessions require a new RETRAIN_OUTPUT_ROOT'
DATASET = RESULTS / 'data/dataset.npz' if 'RETRAIN_OUTPUT_ROOT' in os.environ else LEGACY_DATA
LOCKS = [BENCH / ('goal2_gray8v2_phasev1_7m_roi162_90_14_15_exp2000_lights_' + suffix) / 'protocol_lock.json'
         for suffix in ('on_20261107', 'off_20261107_r2')]


def load_manifest():
    path = ROOT / '1_VLC_code_main/paper_jsen/distance_results/summary.json'
    manifest = json.loads(path.read_text())
    sources = [path]
    if CORRECTED:
        locks = {light: json.loads(p.read_text()) for light, p in zip(('on', 'off'), LOCKS)}
        sources += LOCKS
        for s in manifest['sessions']:
            if s['distance_m'] == 7:
                lock = locks[s['lighting']]
                assert len(lock['replay_sessions']) == len(lock['capture_sessions']) == 3
                i = s['index'] - 1
                s.update(replay=lock['replay_sessions'][i], capture=lock['capture_sessions'][i],
                         preprocessing='jpeg-gray8-v2')
        # Expected error counts come from the corrected replay summaries, not the legacy totals.
        for group in manifest['groups']:
            if group['distance_m'] == 7:
                matching = [s for s in manifest['sessions'] if s['distance_m'] == 7 and s['lighting'] == group['lighting']]
                for alg in ('classic', 'npu'):
                    group[alg]['errors'] = sum(json.loads((BENCH / s['replay'] / 'summary.json').read_text())[alg + '_direct']['errors'] for s in matching)
    assert len(manifest['sessions']) == 24
    assert len({s['replay'] for s in manifest['sessions']}) == 24
    return manifest, sources
