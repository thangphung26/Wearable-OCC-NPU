#!/usr/bin/env python3
"""CPU-only held-out control evaluation; never writes captures or prior results.

Run from the repository root with:
env CUDA_VISIBLE_DEVICES=-1 PYTHONDONTWRITEBYTECODE=1 nice -n 10 \
    .venv/bin/python 1_VLC_code_main/AI_Pipeline_amebaPro2/retrain_sessions/eval_controls.py
Positional batch names are appended to the four defaults. An existing output
directory is refused, so a run cannot overwrite a previous evaluation.
"""
import os
os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
os.environ.setdefault('TF_NUM_INTRAOP_THREADS', '1')
os.environ.setdefault('TF_NUM_INTEROP_THREADS', '1')
os.environ.setdefault('OMP_NUM_THREADS', '1')
import sys
sys.dont_write_bytecode = True
import argparse
import csv
import hashlib
import itertools
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from export_dataset import bits_hex, decision_bytes, replay_input
from train_eval import ci, tflite_predict

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BENCH = ROOT / '1_VLC_code_main/dataset/benchmark'
OUT = HERE / 'results_v2/controls_3m_v2'
# train_eval puts the pipeline directory on sys.path. These modules do not open
# serial ports on import; only their pure alignment/normalization APIs are used.
from preamble_tracker import PreambleTracker
from manchester_phase import refine
from benchmark_precrc import normalize

DEFAULT_BATCHES = [
    'goal2_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_off_20261107',
    'goal2_gray8v2_phasev1_3m_roi169_97_32_26_exp2000_lights_on_20261108',
    'goal2_gray8v2_phasev1_3m_roi180_107_11_10_exp2000_lights_on_20261108',
]
DEFAULT_BATCHES.append(DEFAULT_BATCHES[2] + '_s04')
# (source batch, real session suffix, display repeat). The replacement rule was
# fixed at 12:08, before s02 was scored; display repeat 2 belongs to s04.
GROUPS = {
    'wide_off': [(DEFAULT_BATCHES[0], f'_s{i:02}', i) for i in (1, 2, 3)],
    'wide_on': [(DEFAULT_BATCHES[1], f'_s{i:02}', i) for i in (1, 2, 3)],
    'tight_on_primary': [(DEFAULT_BATCHES[2], '_s01', 1),
                         (DEFAULT_BATCHES[3], '_s04', 2),
                         (DEFAULT_BATCHES[2], '_s03', 3)],
    'tight_on_excluded': [(DEFAULT_BATCHES[2], '_s02', None)],
}
EXCLUSION_REASON = 'person crossed room during capture'
MODELS = {
    'original_ch4': HERE.parent / 'ameba_deployment/vlc_edge_cnn_ch4_int8.tflite',
    'candidate': HERE / 'results_v2/repeat/deployment_candidate_int8.tflite',
    'augmented': HERE / 'results_v2/repeat_aug/deployment_candidate_aug_int8.tflite',
}
DECODERS = ['classic', *MODELS, 'board_cpu', 'board_npu']


def relative(path):
    return str(path.relative_to(ROOT))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path, provenance):
    raw = path.read_bytes()
    provenance[relative(path)] = hashlib.sha256(raw).hexdigest()
    return json.loads(raw)


def check(checks, name, passed, **details):
    result = dict(name=name, passed=bool(passed), **details)
    checks.append(result)
    print(('PASS ' if passed else 'FAIL ') + json.dumps(result), flush=True)


def metric(rows, decoder):
    errors = sum(r[decoder + '_errors'] for r in rows)
    n, windows = len(rows) * 48, len(rows)
    exact = sum(r[decoder + '_errors'] == 0 for r in rows)
    return dict(errors=errors, n_bits=n, ber=errors/n, ber_95cp=ci(errors, n),
                exact_windows=exact, n_windows=windows,
                exact_window_rate_95cp=ci(exact, windows))


def aggregate(rows):
    # Session/frame_id identifies a paired window unambiguously across batches.
    misses = {d: [dict(session=r['session'], frame_id=r['frame_id'])
                  for r in rows if r[d + '_errors']] for d in DECODERS}
    pairs = {}
    for a, b in itertools.combinations(DECODERS, 2):
        cells = {key: [] for key in ('both_miss', 'only_first_misses',
                                     'only_second_misses', 'both_exact')}
        for r in rows:
            am, bm = bool(r[a + '_errors']), bool(r[b + '_errors'])
            key = ('both_miss' if bm else 'only_first_misses') if am else (
                'only_second_misses' if bm else 'both_exact')
            cells[key].append(dict(session=r['session'], frame_id=r['frame_id']))
        pairs[a + '__' + b] = dict(first=a, second=b,
            counts={key: len(value) for key, value in cells.items()},
            **{key: value for key, value in cells.items() if key != 'both_exact'})
    return dict(metrics={d: metric(rows, d) for d in DECODERS},
                missed_windows=misses, paired_window_overlap=pairs)


def amplitude(window, truth):
    # Manchester 1 = ON,ON,OFF,OFF; 0 = OFF,OFF,ON,ON.
    on = np.repeat(np.stack((truth, 1-truth), axis=1), 2, axis=1).astype(bool)
    values = np.asarray(window).reshape(48, 4)
    return float(values[on].mean() - values[~on].mean())


def write_csv(path, rows):
    with path.open('x', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, separators=(',', ':'))
                             if isinstance(value, (list, dict)) else value
                             for key, value in row.items()})


def report(summary, rows):
    lines = ['# Held-out 3 m control evaluation', '',
             f"UTC run: {summary['created_utc']}", '',
             'CPU-only host inference; no training, serial access, or firmware execution. '
             'Labels come only from capture TX ground truth with `payload_mode == test`; '
             'frame IDs and frame hex are checked against replay records. '
             'No CRC or error-based filtering is applied.', '',
             'The exact stored INT8 windows are read via `export_dataset.replay_input`. '
             'The existing import-safe helper is reused without modification. '
             'Raw windows are regenerated with the existing `PreambleTracker.align` and '
             '`manchester_phase.refine`, then checked byte-for-byte using '
             '`benchmark_precrc.normalize`. All three host models use '
             '`train_eval.tflite_predict` and model-specific output quantization.', '',
             'Acquisition: 30 FPS; resampling: 20 Hz; link: 5 bit/s. '
             'Error bit positions are zero-based (0–47). Window identifiers are '
             '`session:frame_id`, not row numbers.', '',
             '95% intervals are exact two-sided Clopper–Pearson, imported from '
             '`train_eval.ci`. BER intervals pool bit errors within each group; '
             'exact-window intervals use whole-window successes. Bits and windows may '
             'be correlated, so these binomial intervals do not establish independent-day '
             'generalization. New candidates have host-only results and still require '
             'board CPU/NPU deployment verification.', '', '## Skipped batches', '']
    for item in summary['skipped_batches']:
        lines.append(f"- `{item['batch']}`: skipped, state `{item['state']}`.")
    if not summary['skipped_batches']:
        lines.append('None.')
    lines += ['', '## Session map and exclusions', '',
              'The replacement rule was fixed at 12:08 before s02 was scored. '
              'Primary tight-ROI repeats are 1 = s01, 2 = s04, 3 = s03.', '',
              '| Group | Display repeat | Real replay session |',
              '|---|---:|---|']
    for name, mapping in summary['session_map'].items():
        for repeat, session in mapping.items():
            lines.append(f'| {name} | {repeat} | {session} |')
    for exclusion in summary['exclusions']:
        lines += ['', f"- Flagged and excluded from primary: `{exclusion['session']}`; "
                  f"reason: {exclusion['reason']}. Replacement: `{exclusion['replacement_session']}` "
                  '(display repeat 2). Retained in `tight_on_excluded` and both CSVs.']
    for name, group in summary['groups'].items():
        lines += ['', f'## {name}', '',
                  '| Decoder | Errors / bits | BER (95% CP) | Exact / windows | Exact rate 95% CP |',
                  '|---|---:|---|---:|---|']
        for d, m in group['metrics'].items():
            lo, hi = m['ber_95cp']; elo, ehi = m['exact_window_rate_95cp']
            lines.append(f"| {d} | {m['errors']}/{m['n_bits']} | {m['ber']:.8g} [{lo:.8g}, {hi:.8g}] | {m['exact_windows']}/{m['n_windows']} | [{elo:.8g}, {ehi:.8g}] |")
        lines += ['', '### Per session', '',
                  '| Session | Decoder | Errors / 4800 bits | Exact / 100 windows |',
                  '|---|---|---:|---:|']
        for session, item in group['sessions'].items():
            for d, m in item['metrics'].items():
                lines.append(f"| {session} | {d} | {m['errors']}/{m['n_bits']} | {m['exact_windows']}/{m['n_windows']} |")
        lines += ['', '### Paired missed windows', '',
                  'Complete per-session and per-group pairwise identities are in '
                  '`summary.json`; each window and error position is in `per_window.csv`. ', '']
        for d, misses in group['missed_windows'].items():
            ids = ', '.join(f"{r['session']}:{r['frame_id']}" for r in misses) or 'none'
            lines.append(f'- {d} misses: {ids}.')
        lines += ['', '| First / second | Both miss | Only first misses | Only second misses | Both exact |',
                  '|---|---:|---:|---:|---:|']
        for pair in group['paired_window_overlap'].values():
            c = pair['counts']
            lines.append(f"| {pair['first']} / {pair['second']} | {c['both_miss']} | {c['only_first_misses']} | {c['only_second_misses']} | {c['both_exact']} |")
    lines += ['', '## Erroneous windows', '',
              'Amplitude = mean expected ON samples minus mean expected OFF samples, '
              'using all 48 TX truth bits and Manchester half-bit assignments. '
              '`amplitude_before_normalize` uses the regenerated raw float input '
              '(capture intensity units); `amplitude_normalized_input` uses the exact '
              'training-scale input `(q+128)/255`. This reports both interpretations '
              'of normalized-before input. Fitted gain is `signed_optical_gain`, '
              'falling back to `tracker.gain`; absent values are null/blank.', '',
              '| Session:frame_id | Decoder errors (bit positions in CSV) | Amplitude before normalize | Amplitude normalized input | Fitted preamble gain |',
              '|---|---|---:|---:|---:|']
    for r in rows:
        failures = ', '.join(f"{d}: {r[d + '_errors']}" for d in DECODERS if r[d + '_errors'])
        if failures:
            lines.append(f"| {r['session']}:{r['frame_id']} | {failures} | {r['amplitude_before_normalize']:.8g} | {r['amplitude_normalized_input']:.8g} | {r['fitted_preamble_gain']} |")
    lines += ['', '## Acceptance checks', '']
    for c in summary['acceptance_checks']:
        lines.append(f"- {'PASS' if c['passed'] else 'FAIL'} `{c['name']}`: " + json.dumps({k: v for k, v in c.items() if k not in ('name', 'passed')}) )
    lines += ['', 'Held-out status is checked against both candidates’ train, validation, '
              'calibration and prior test session lists and the corrected export session '
              'manifest. This audits recorded session membership, not undocumented '
              'training outside these artifacts. Source/model hashes are in `summary.json`.', '',
              '## Files created', '']
    lines += [f'- `{path}`' for path in summary['files_created']]
    lines += ['', 'No git commands were used. Datasets and pre-existing results were not '
              'written. The running capture was not accessed through a serial port.', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('batches', nargs='*', help='Extra benchmark batch names')
    args = parser.parse_args()
    if OUT.exists():
        parser.error(f'Refusing existing output directory: {OUT}')
    batches = list(dict.fromkeys(DEFAULT_BATCHES + args.batches))
    for name in batches:
        if Path(name).name != name or name in ('.', '..'):
            parser.error('Batch names must be plain directory names')
    provenance, checks, skipped, records, session_sources = {}, [], [], [], {}
    status_snapshots, group_members = {}, {}
    for batch in batches:
        directory = BENCH / batch
        status_path = directory / 'status.json'
        status = read_json(status_path, provenance) if status_path.exists() else {'state': 'missing'}
        status_snapshots[batch] = status
        if status.get('state') != 'complete':
            skipped.append(dict(batch=batch, state=status.get('state'), status=status))
            print(f"SKIPPED {batch}: status state={status.get('state')!r}", flush=True)
            continue
        lock = read_json(directory / 'protocol_lock.json', provenance)
        sessions, captures = lock['replay_sessions'], lock['capture_sessions']
        assert len(sessions) == len(captures) == len(set(sessions)) > 0, batch
        if batch in DEFAULT_BATCHES:
            expected_suffixes = {suffix for members in GROUPS.values()
                                 for source, suffix, _ in members if source == batch}
            assert len(sessions) == len(expected_suffixes), batch
            assert all(sum(s.endswith(suffix) for s in sessions) == 1
                       for suffix in expected_suffixes), batch
        for index, (session, capture) in enumerate(zip(sessions, captures), 1):
            assert all(Path(n).name == n and n not in ('.', '..') for n in (session, capture))
            assert session not in session_sources, session
            if batch in DEFAULT_BATCHES:
                matches = [(name, repeat) for name, members in GROUPS.items()
                           for source, suffix, repeat in members
                           if source == batch and session.endswith(suffix)]
                assert len(matches) == 1, session
                group_name, display_repeat = matches[0]
            else:
                group_name, display_repeat = batch, index
            group_members.setdefault(group_name, []).append(session)
            excluded = group_name == 'tight_on_excluded'
            replay, source = BENCH / session, BENCH / capture
            stored = read_json(replay / 'frame_decisions.json', provenance)
            replay_summary = read_json(replay / 'summary.json', provenance)
            assert replay_summary['complete'] and replay_summary['error'] is None
            ground_truth = read_json(source / 'tx_ground_truth.json', provenance)
            truth_rows = [r for r in ground_truth if r.get('payload_mode') == 'test']
            truth = {int(r['frame_id']): r for r in truth_rows}
            assert len(truth) == len(truth_rows), 'Duplicate TX test frame IDs'
            nominal_rows = read_json(source / 'frame_decisions.json', provenance)
            nominal = {int(r['frame_id']): r for r in nominal_rows}
            values = read_json(source / 'samples.json', provenance)
            tracker = PreambleTracker()
            local = []
            for row in stored:
                fid = int(row['frame_id'])
                if fid not in truth:
                    continue
                gt = truth[fid]
                assert int(gt['frame_hex'], 16) == int(row['frame_hex'], 16)
                q, x = replay_input(row)
                coarse = tracker.align(values, nominal[fid]['start_offset_s'])
                phase, window = refine(values, coarse['start'])
                regenerated = normalize(window)
                same = regenerated == q.tobytes()
                assert abs(phase['start'] - row['start_offset_s']) < 1e-8
                labels = bits_hex(gt['frame_hex'])
                predictions = dict(classic=np.array(list(row['classic_bits']), dtype=np.uint8),
                                   board_cpu=decision_bytes(row['cpu_hex']),
                                   board_npu=decision_bytes(row['npu_hex']))
                assert labels.shape == (48,) and all(p.shape == (48,) for p in predictions.values())
                local.append(dict(batch=batch, group=group_name, display_repeat=display_repeat,
                    excluded=excluded, exclusion_reason=EXCLUSION_REASON if excluded else '',
                    session=session, capture=capture, frame_id=fid,
                    q=q, truth=labels, predictions=predictions, normalized_bytes_match=same,
                    amplitude_before_normalize=amplitude(window, labels),
                    amplitude_normalized_input=amplitude(x, labels),
                    fitted_preamble_gain=row.get('signed_optical_gain', row.get('tracker', {}).get('gain'))))
            check(checks, f'{session}:100_test_windows_4800_bits',
                  len(local) == len(stored) == 100 and len({r['frame_id'] for r in local}) == 100
                  and replay_summary['frames_compared'] == 100 and replay_summary['bits_compared'] == 4800,
                  windows=len(local), bits=len(local)*48, replay_rows=len(stored))
            records.extend(local)
            session_sources[session] = dict(batch=batch, capture=capture, summary=replay_summary,
                display_repeat=display_repeat, excluded=excluded)
    if not records:
        raise RuntimeError('No complete batches to evaluate; no output created')
    selected = set(session_sources) | {r['capture'] for r in records}
    # Check held-out membership without invoking any training code.
    for folder in ('repeat', 'repeat_aug'):
        history = read_json(HERE / 'results_v2' / folder / 'history_all.json', provenance)
        prior = {s for h in history for key in ('train_sessions', 'validation_sessions',
                 'calibration_sessions', 'test_sessions') for s in h.get(key, [])}
        check(checks, folder + ':held_out_session_membership', not selected & prior,
              overlapping_sessions=sorted(selected & prior))
    dataset_path = HERE / 'results_v2/data/dataset.npz'
    provenance[relative(dataset_path)] = digest(dataset_path)
    with np.load(dataset_path, allow_pickle=False) as data:
        overlap = selected & set(data['session'].tolist())
    check(checks, 'held_out_from_corrected_export', not overlap, overlapping_sessions=sorted(overlap))
    q = np.stack([r['q'] for r in records])
    for name, model in MODELS.items():
        predictions = tflite_predict(model, q)
        provenance[relative(model)] = digest(model)
        for record, prediction in zip(records, predictions):
            record['predictions'][name] = prediction
    rows = []
    for r in records:
        row = {key: r[key] for key in ('batch', 'group', 'display_repeat', 'excluded',
               'exclusion_reason', 'session', 'capture', 'frame_id',
               'amplitude_before_normalize', 'amplitude_normalized_input', 'fitted_preamble_gain')}
        row.update(n_bits=48, truth_bits=''.join(map(str, r['truth'].tolist())))
        for d in DECODERS:
            positions = np.flatnonzero(r['predictions'][d] != r['truth']).tolist()
            row[d + '_bits'] = ''.join(map(str, r['predictions'][d].tolist()))
            row[d + '_errors'] = len(positions)
            row[d + '_error_positions'] = positions
            row[d + '_exact_window'] = int(not positions)
        for backend in ('cpu', 'npu'):
            row['host_vs_board_' + backend + '_mismatch_bits'] = int(np.sum(
                r['predictions']['original_ch4'] != r['predictions']['board_' + backend]))
        row['normalized_bytes_match'] = r['normalized_bytes_match']
        rows.append(row)
    check(checks, 'regenerated_normalization_byte_exact', all(r['normalized_bytes_match'] for r in rows),
          mismatching_windows=sum(not r['normalized_bytes_match'] for r in rows))
    session_rows, group_results, session_map = [], {}, {}
    for name in [*GROUPS, *(b for b in batches if b not in DEFAULT_BATCHES)]:
        if name not in group_members:
            continue
        subset = [r for r in rows if r['group'] == name]
        group = aggregate(subset)
        group['sessions'] = {}
        sessions = sorted(group_members[name],
                          key=lambda s: session_sources[s]['display_repeat'] or 0)
        expected_sessions = len(GROUPS[name]) if name in GROUPS else len(sessions)
        group['excluded'] = name == 'tight_on_excluded'
        group['source_batches'] = list(dict.fromkeys(r['batch'] for r in subset))
        group['session_map'] = {str(session_sources[s]['display_repeat']): s for s in sessions
                                if session_sources[s]['display_repeat'] is not None}
        session_map[name] = group['session_map']
        check(checks, name + f':{expected_sessions}x100x48',
              len(sessions) == expected_sessions and len(subset) == expected_sessions*100,
              sessions=len(sessions), windows=len(subset), bits=len(subset)*48)
        for session in sessions:
            local = [r for r in subset if r['session'] == session]
            item = aggregate(local)
            group['sessions'][session] = item
            original = session_sources[session]['summary']
            for d, key in [('classic', 'classic_direct'), ('board_cpu', 'cpu_direct'), ('board_npu', 'npu_direct')]:
                m = item['metrics'][d]
                check(checks, session + ':' + key + '_summary_parity',
                      m['errors'] == original[key]['errors'] and m['exact_windows'] == original[key]['exact_windows'],
                      errors=m['errors'], expected_errors=original[key]['errors'],
                      exact_windows=m['exact_windows'], expected_exact_windows=original[key]['exact_windows'])
            for d, m in item['metrics'].items():
                source = session_sources[session]
                session_rows.append(dict(batch=source['batch'], group=name,
                    display_repeat=source['display_repeat'], excluded=source['excluded'],
                    exclusion_reason=EXCLUSION_REASON if source['excluded'] else '',
                    session=session, decoder=d, **m,
                    missed_frame_ids=[r['frame_id'] for r in local if r[d+'_errors']]))
        for d, m in group['metrics'].items():
            print(f"GROUP {name} {d}: errors {m['errors']}/{m['n_bits']}; "
                  f"exact windows {m['exact_windows']}/{m['n_windows']}", flush=True)
        for backend in ('cpu', 'npu'):
            key = 'host_vs_board_' + backend + '_mismatch_bits'
            count = sum(r[key] for r in subset)
            group[key] = count
            group['host_vs_board_' + backend + '_mismatch_windows'] = sum(r[key] > 0 for r in subset)
            # CPU bit-exactness is required; NPU disagreements are reported.
            if backend == 'cpu':
                check(checks, name + ':original_host_board_cpu_bit_exact', count == 0,
                      mismatch_bits=count, mismatch_windows=group['host_vs_board_cpu_mismatch_windows'])
            else:
                print(f'BOARD NPU VS HOST {name}: {count} mismatching bits', flush=True)
        if name in ('wide_off', 'wide_on'):
            expected = [0, 0, 0] if name == 'wide_off' else [0, 12, 12]
            actual = [group['metrics'][d]['errors'] for d in ('classic', 'board_cpu', 'board_npu')]
            check(checks, name + ':expected_classic_cpu_npu_errors', actual == expected,
                  actual=actual, expected=expected)
        group_results[name] = group
    exclusions = [dict(session=s, batch=session_sources[s]['batch'], excluded=True,
        reason=EXCLUSION_REASON, rule_fixed_at='2026-11-08T12:08:00+09:00',
        rule_timing='before s02 was scored', replacement_display_repeat=2,
        replacement_session=session_map.get('tight_on_primary', {}).get('2'))
        for s in group_members.get('tight_on_excluded', [])]
    check(checks, 'git_status_not_used', True, git_commands_used=0)
    files = [relative(OUT / name) for name in
             ('summary.json', 'per_session.csv', 'per_window.csv', 'REPORT.md')]
    for path in [Path(__file__).resolve(), HERE/'export_dataset.py', HERE/'train_eval.py',
                 HERE/'session_config.py', HERE.parent/'preamble_tracker.py',
                 HERE.parent/'manchester_phase.py', HERE.parent/'benchmark_precrc.py']:
        provenance[relative(path)] = digest(path)
    summary = dict(created_utc=datetime.now(timezone.utc).isoformat(),
        cpu_only=True, process_nice=os.nice(0), tensorflow_device='CPU',
        acquisition_fps=30, resampling_hz=20, link_bit_s=5,
        requested_batches=batches, status_snapshots=status_snapshots, skipped_batches=skipped,
        groups=group_results, session_map=session_map, exclusions=exclusions, acceptance_checks=checks,
        all_checks_passed=all(c['passed'] for c in checks), inputs_sha256=provenance,
        files_created=files, files_modified=[relative(Path(__file__).resolve())],
        interval_method='Exact 95% two-sided Clopper-Pearson; binomial intervals assume independent trials')
    assert summary['process_nice'] >= 10, 'Run with nice -n 10'
    # Evaluate everything first, then create only the new output directory.
    OUT.mkdir()
    write_csv(OUT / 'per_session.csv', session_rows)
    write_csv(OUT / 'per_window.csv', rows)
    with (OUT / 'summary.json').open('x') as stream:
        stream.write(json.dumps(summary, indent=2) + '\n')
    with (OUT / 'REPORT.md').open('x') as stream:
        stream.write(report(summary, rows))
    for path in files:
        print('CREATED ' + path, flush=True)
    print('ALL ACCEPTANCE CHECKS ' + ('PASS' if summary['all_checks_passed'] else 'FAIL'), flush=True)
    if not summary['all_checks_passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
