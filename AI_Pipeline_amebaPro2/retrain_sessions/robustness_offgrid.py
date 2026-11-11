#!/usr/bin/env python3
"""CPU-only, session-isolated timing checks; never retrain or alter prior results."""
from session_config import RESULTS, DATASET
import os
os.environ['CUDA_VISIBLE_DEVICES'] = ''
import csv
import json
import time
from pathlib import Path

import numpy as np
from robustness import HERE, ROOT, Decoder, classic, digest, metric, normalize, regenerate, sample
from plots import errorpoint

OUT = RESULTS/'robustness_offgrid'
NAMES = ['classic', 'old_int8', 'new_int8', 'new_aug_int8']
OFFSETS = [-45, -35, -25, -15, -5, 5, 15, 25, 35, 45]
LOSO_OFFSETS = [-40, -25, 0, 25, 40]


def read_json(path, provenance):
    provenance[str(path.relative_to(ROOT))] = digest(path)
    return json.loads(path.read_text())


def check_split(h, tests):
    train, val = set(h['train_sessions']), set(h['validation_sessions'])
    # Baseline train_eval.py calibrates on the first 200 training frames.
    calibration = set(h.get('calibration_sessions', h['train_sessions']))
    assert calibration <= train
    assert train.isdisjoint(val)
    assert tests == set(h['test_sessions'])
    assert tests.isdisjoint(train | val | calibration)


def subset_metric(errors):
    if errors.size:
        return metric(errors)
    return dict(errors=0, n_bits=0, ber=None, ber_95cp=None, exact_windows=0, n_windows=0)


def evaluate(frames, offsets, decoders, experiment, writer, fold='repeat'):
    names = ['classic'] + list(decoders)
    subset = np.array([f['distance_m'] <= 5 for f in frames])
    entries = []
    for offset in offsets:
        windows = np.array([[sample(f['values'], f['start']+offset/1000+(i+.5)*.05)
                             for i in range(192)] for f in frames], dtype=np.float64)
        cb = classic(windows)
        counts = np.zeros((len(frames), len(names)), dtype=int)
        for i, (frame, window) in enumerate(zip(frames, windows)):
            q = np.frombuffer(normalize(window), dtype=np.int8)
            predictions = [cb[i]] + [d.predict(q) for d in decoders.values()]
            row = {k: frame[k] for k in ['session', 'frame_id', 'distance_m', 'lighting', 'preproc']}
            row.update(experiment=experiment, fold=fold, offset_ms=offset, n_bits=48)
            for j, (name, prediction) in enumerate(zip(names, predictions)):
                positions = np.flatnonzero(prediction != frame['truth']).tolist()
                counts[i, j] = len(positions)
                row[name+'_errors'] = len(positions)
                row[name+'_error_positions'] = json.dumps(positions, separators=(',', ':'))
                row[name+'_exact_window'] = int(not positions)
            writer.writerow(row)
        entry = dict(point=offset, perturbation='timing_offset',
                     all_sessions={n: metric(counts[:, j]) for j, n in enumerate(names)},
                     **{'1_5m': {n: subset_metric(counts[subset, j]) for j, n in enumerate(names)}})
        entries.append(entry)
        print(json.dumps(dict(experiment=experiment, fold=fold, offset_ms=offset,
                              errors={n: m['errors'] for n, m in entry['all_sessions'].items()})), flush=True)
    return entries


def pool(folds):
    entries = []
    for i, offset in enumerate(LOSO_OFFSETS):
        entry = dict(point=offset, perturbation='timing_offset')
        for group in ['all_sessions', '1_5m']:
            entry[group] = {}
            for name in ['classic', 'new_aug_int8']:
                metrics = [f['conditions'][i][group][name] for f in folds]
                # Pool integer counts before calculating CP intervals.
                from plots import ci
                e = sum(m['errors'] for m in metrics)
                n = sum(m['n_bits'] for m in metrics)
                entry[group][name] = dict(errors=e, n_bits=n, ber=e/n, ber_95cp=ci(e, n),
                    exact_windows=sum(m['exact_windows'] for m in metrics),
                    n_windows=sum(m['n_windows'] for m in metrics))
        assert entry['all_sessions']['classic']['n_bits'] == 115200
        entries.append(entry)
    return entries


def plot(entries):
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.size': 7, 'pdf.fonttype': 42})
    fig, axes = plt.subplots(2, 1, figsize=(3.5, 4.7))
    for ax, group, title in zip(axes, ['all_sessions', '1_5m'], ['1–7 m', '1–5 m']):
        for j, (name, label) in enumerate(zip(NAMES, ['Classic', 'CNN (original)', 'CNN (retrained)', 'CNN (retrained + aug.)'])):
            for i, e in enumerate(entries):
                m = e[group][name]
                errorpoint(ax, e['point'], m['errors'], m['n_bits'], f'C{j}',
                           marker=['o', 's', '^', 'D'][j], label=label if i == 0 else None)
            ax.plot([e['point'] for e in entries],
                    [e[group][name]['ber'] or e[group][name]['ber_95cp'][1] for e in entries],
                    color=f'C{j}', linewidth=.6, alpha=.6)
        ax.set(xlabel='Residual timing offset (ms)', ylabel='BER', yscale='log')
        ax.set_xticks([-40, -20, 0, 20, 40])
        ax.text(.5, .97, title, ha='center', va='top', transform=ax.transAxes)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc='upper center', ncol=2, fontsize=6)
    fig.text(.5, .01, '95% CP intervals; open markers: zero errors at upper bound', ha='center', fontsize=6)
    fig.tight_layout(rect=(0, .04, 1, .92))
    for ext in ['pdf', 'png']:
        fig.savefig(OUT/f'ber_vs_timing_offset_full.{ext}', dpi=300)
    plt.close(fig)


def main():
    started = time.time()
    assert not OUT.exists(), 'Refusing to overwrite existing results'
    frames, sanity, provenance, stats = regenerate(all_sessions=True)
    data_path = DATASET
    data = np.load(data_path)
    by_key = {(f['session'], f['frame_id']): f for f in frames}
    assert len(by_key) == len(frames) == 2400
    frames = [by_key[(str(s), int(i))] for s, i in zip(data['session'], data['frame_index'])]
    assert np.array_equal(np.stack([f['truth'] for f in frames]), data['y'])
    repeat_frames = [f for f, r in zip(frames, data['repeat']) if r == 3]
    tests = {f['session'] for f in repeat_frames}
    assert len(repeat_frames) == 800 and len(tests) == 8
    for directory in ['repeat', 'repeat_aug']:
        for h in read_json(RESULTS/directory/'history_all.json', provenance):
            check_split(h, tests)
    loso_dir = RESULTS/'loso_aug'
    loso_summary = read_json(loso_dir/'summary.json', provenance)
    assert loso_summary['complete'] and loso_summary['completed_folds'] == 24
    histories = read_json(loso_dir/'history_all.json', provenance)
    assert len(histories) == 24
    assert {h['test_sessions'][0] for h in histories} == {f['session'] for f in frames}
    for h in histories:
        assert len(h['test_sessions']) == 1 and h['fold'] == h['test_sessions'][0]
        check_split(h, set(h['test_sessions']))
    paths = [Path(__file__), HERE/'robustness.py', HERE/'export_offsets.py', HERE/'plots.py',
             HERE/'train_eval.py', HERE/'train_eval_aug.py', data_path]
    provenance.update({str(p.relative_to(ROOT)): digest(p) for p in paths})
    decoders = dict(zip(NAMES[1:], [Decoder(HERE.parent/'vlc_edge_cnn_int8.tflite'),
        Decoder(RESULTS/'repeat/deployment_candidate_int8.tflite'),
        Decoder(RESULTS/'repeat_aug/deployment_candidate_aug_int8.tflite')]))
    ongrid_path = RESULTS/'robustness_aug/summary.json'
    ongrid = [e for e in read_json(ongrid_path, provenance)['conditions'] if e['perturbation'] == 'timing_offset']
    previous_config = read_json(ongrid_path.with_name('config.json'), provenance)
    assert previous_config['models'] == json.loads(json.dumps([d.details for d in decoders.values()]))
    # Check regenerated capture/script inputs against the source of the reused on-grid points.
    for path, sha in previous_config['inputs_sha256'].items():
        if path in provenance:
            assert provenance[path] == sha, path
    OUT.mkdir()
    fields = ['experiment', 'fold', 'offset_ms', 'session', 'frame_id', 'distance_m', 'lighting', 'preproc', 'n_bits']
    for n in NAMES:
        fields += [n+'_errors', n+'_error_positions', n+'_exact_window']
    folds, model_details = [], []
    with (OUT/'results.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        repeat = evaluate(repeat_frames, OFFSETS, decoders, 'repeat_offgrid', writer)
        for h in histories:
            local = [f for f in frames if f['session'] in h['test_sessions']]
            assert len(local) == 100
            decoder = Decoder(loso_dir/f'.tmp_{h["fold"]}_{h["seed"]}.tflite')
            model_details.append(decoder.details)
            folds.append(dict(fold=h['fold'], seed=h['seed'], conditions=evaluate(
                local, LOSO_OFFSETS, {'new_aug_int8': decoder}, 'loso_aug', writer, h['fold'])))
    combined = sorted(ongrid+repeat, key=lambda e: e['point'])
    assert len(combined) == 19 and len({e['point'] for e in combined}) == 19
    plot(combined)
    summary = dict(sanity=sanity, repeat_offgrid=repeat, loso_aug=pool(folds), loso_folds=folds,
                   repeat_full=combined, runtime_seconds=time.time()-started)
    (OUT/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    config = dict(tensorflow_device='CPU', offsets_ms=OFFSETS, loso_offsets_ms=LOSO_OFFSETS,
                  inputs_sha256=provenance, repeat_models=[d.details for d in decoders.values()],
                  loso_models=model_details, acquisition_fps=30, resampling_hz=20, link_bit_s=5,
                  leakage_assertions='test disjoint from training/validation/calibration for every evaluated fold',
                  intervals='95% two-sided Clopper–Pearson on pooled bits; correlated captured bits are not independent captures')
    (OUT/'config.json').write_text(json.dumps(config, indent=2)+'\n')
    print(json.dumps({'output': str(OUT), 'runtime_seconds': summary['runtime_seconds']}), flush=True)


if __name__ == '__main__':
    main()
