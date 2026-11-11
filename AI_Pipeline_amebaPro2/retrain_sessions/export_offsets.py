#!/usr/bin/env python3
"""Cache all session windows after alignment, with fixed residual timing offsets."""
from session_config import DATASET
import json
import numpy as np
from robustness import HERE, ROOT, regenerate, sample, normalize, digest


def main():
    frames, sanity, provenance, stats = regenerate(all_sessions=True)
    data = np.load(DATASET)
    by_key = {(f['session'], f['frame_id']): f for f in frames}
    frames = [by_key[(str(s), int(i))] for s, i in zip(data['session'], data['frame_index'])]
    assert np.array_equal(np.stack([f['truth'] for f in frames]), data['y'])
    offsets = np.arange(-40, 41, 10)
    windows = np.array([[[sample(f['values'], f['start']+delta/1000+(i+.5)*.05)
                          for i in range(192)] for delta in offsets] for f in frames], dtype=np.float64)
    q = np.stack([np.frombuffer(normalize(w), dtype=np.int8) for w in windows[:,4]])
    assert np.mean(np.all(q == data['input_int8'], axis=1)) >= .99
    provenance[str(Path(__file__).relative_to(ROOT))] = digest(Path(__file__))
    out = DATASET.with_name('windows_offsets.npz')
    np.savez_compressed(out, windows=windows, offsets_ms=offsets,
                        session=data['session'], frame_id=data['frame_index'], truth=data['y'],
                        noise_std=np.array([stats[f['session']]['noise_std'] for f in frames]),
                        aligned_start_s=np.array([f['start'] for f in frames]),
                        metadata_json=json.dumps(dict(sanity=sanity, provenance=provenance, sessions=stats)))
    print(json.dumps(dict(cache=str(out), shape=windows.shape, sanity=sanity)), flush=True)


from pathlib import Path
if __name__ == '__main__':
    main()
