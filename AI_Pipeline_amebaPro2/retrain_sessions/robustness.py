#!/usr/bin/env python3
"""CPU-only paired robustness evaluation of repeat-3 float replay windows."""
from session_config import RESULTS, load_manifest
import os
os.environ['CUDA_VISIBLE_DEVICES'] = ''
os.environ.setdefault('TF_NUM_INTRAOP_THREADS', '1')
os.environ.setdefault('TF_NUM_INTEROP_THREADS', '1')
import argparse
import csv
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
from replay_precrc_buffer import sample, PreambleTracker
from manchester_phase import refine
from benchmark_precrc import normalize
from plots import ci, errorpoint, NAMES, LABELS

OUT = RESULTS / 'robustness'
BENCH = ROOT / '1_VLC_code_main/dataset/benchmark'
POINTS = {'timing_offset': list(range(-40, 41, 10)),
          'contrast': [1, .5, .25, .1], 'snr': [20, 15, 10, 6, 3, 0]}
SEEDS = [76101, 76102, 76103, 76104, 76105]


def classic(window):
    v = np.asarray(window).reshape(-1, 48, 4)
    return (v[:, :, 0]+v[:, :, 1] > v[:, :, 2]+v[:, :, 3]).astype(np.uint8)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def regenerate(all_sessions=False):
    manifest, manifest_sources = load_manifest()
    manifest_sources.append(HERE / 'session_config.py')
    sessions = [s for s in manifest['sessions'] if all_sessions or s['index'] == 3]
    assert len(sessions) == (24 if all_sessions else 8)
    frames, provenance, session_stats = [], {str(p.relative_to(ROOT)): digest(p) for p in manifest_sources}, {}
    equal_frames = equal_bits = 0
    for s in sessions:
        capture = BENCH / s['capture']
        replay = BENCH / s['replay']
        paths = [capture/'samples.json', capture/'tx_ground_truth.json',
                 capture/'frame_decisions.json', replay/'frame_decisions.json']
        for path in paths:
            provenance[str(path.relative_to(ROOT))] = digest(path)
        values = json.loads(paths[0].read_text())
        truth = {int(r['frame_id']): r for r in json.loads(paths[1].read_text()) if r.get('payload_mode') == 'test'}
        nominal = {r['frame_id']: r for r in json.loads(paths[2].read_text())}
        stored = json.loads(paths[3].read_text())
        tracker = PreambleTracker()
        local = []
        for row in stored:
            fid = row['frame_id']
            gt = truth[fid]
            assert int(gt['frame_hex'], 16) == int(row['frame_hex'], 16)
            coarse = tracker.align(values, nominal[fid]['start_offset_s'])
            phase, window = refine(values, coarse['start'])
            q = normalize(window)
            equal_frames += q.hex() == row['input_int8_hex']
            cb = classic(window)[0]
            equal_bits += int(np.sum(cb == np.array(list(row['classic_bits']), dtype=np.uint8)))
            frame = dict(session=s['replay'], distance_m=s['distance_m'], lighting=s['lighting'],
                         frame_id=fid, preproc=s['preprocessing'], start=phase['start'],
                         values=values, window=np.array(window, dtype=np.float64),
                         truth=np.array([(int(gt['frame_hex'],16)>>b)&1 for b in range(47,-1,-1)],dtype=np.uint8))
            frames.append(frame)
            local.append(frame['window'])
        # For each half-bit's two resampled values, pooled residual SSE / DOF.
        pairs = np.asarray(local).reshape(-1, 2)
        sigma = float(np.sqrt(np.mean((pairs[:,0]-pairs[:,1])**2)/2))
        session_stats[s['replay']] = dict(noise_std=sigma, frames=len(local), distance_m=s['distance_m'])
    n = len(frames)
    assert n == (2400 if all_sessions else 800)
    sanity = dict(frames=n, matching_int8_frames=equal_frames, int8_frame_match_rate=equal_frames/n,
                  bits=n*48, matching_classic_bits=equal_bits, classic_bit_match_rate=equal_bits/(n*48))
    sanity['passed'] = sanity['int8_frame_match_rate'] >= .99 and sanity['classic_bit_match_rate'] >= .999
    print(json.dumps({'sanity': sanity}), flush=True)
    if not sanity['passed']:
        raise RuntimeError('Replay sanity failed: raw capture, nominal timing, alignment implementation, or stored decisions differ; perturbations stopped.')
    return frames, sanity, provenance, session_stats


class Decoder:
    def __init__(self, path):
        import tensorflow as tf
        self.it = tf.lite.Interpreter(model_path=str(path), num_threads=1)
        self.it.allocate_tensors()
        self.ii = self.it.get_input_details()[0]
        self.oi = self.it.get_output_details()[0]
        assert self.ii['dtype'] == self.oi['dtype'] == np.int8
        assert self.ii['shape'].tolist() == [1,48,1,4]
        assert np.isclose(self.ii['quantization'][0],1/255) and self.ii['quantization'][1] == -128
        self.details = dict(path=str(path.relative_to(ROOT)), sha256=digest(path),
                            input_quantization=self.ii['quantization'], output_quantization=self.oi['quantization'])

    def predict(self, q):
        self.it.set_tensor(self.ii['index'], q.reshape(1,48,1,4))
        self.it.invoke()
        scale, zero = self.oi['quantization']
        assert scale > 0
        probability = (self.it.get_tensor(self.oi['index']).reshape(48).astype(float)-zero)*scale
        return (probability >= .5).astype(np.uint8)


def metric(errors):
    errors = np.asarray(errors)
    e, n = int(errors.sum()), int(errors.size*48)
    return dict(errors=e, n_bits=n, ber=e/n, ber_95cp=ci(e,n),
                exact_windows=int(np.sum(errors == 0)), n_windows=int(errors.size))


def main():
    global OUT, NAMES, LABELS
    started = time.time()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sanity-only', action='store_true')
    parser.add_argument('--aug', action='store_true', help='Four-model evaluation in robustness_aug')
    args = parser.parse_args()
    if args.aug:
        OUT = RESULTS / 'robustness_aug'
        NAMES = list(NAMES) + ['new_aug_int8']
        LABELS = list(LABELS) + ['New aug INT8']
    frames, sanity, provenance, session_stats = regenerate()
    if args.sanity_only:
        return
    decoders = [Decoder(HERE.parent/'vlc_edge_cnn_int8.tflite'),
                Decoder(RESULTS/'repeat/deployment_candidate_int8.tflite')]
    if args.aug:
        decoders.append(Decoder(RESULTS/'repeat_aug/deployment_candidate_aug_int8.tflite'))
        test_sessions = {f['session'] for f in frames}
        for directory in ['repeat', 'repeat_aug']:
            histories = json.loads((RESULTS/directory/'history_all.json').read_text())
            history_path = RESULTS/directory/'history_all.json'
            provenance[str(history_path.relative_to(ROOT))] = digest(history_path)
            for h in histories:
                assert test_sessions.isdisjoint(set(h['train_sessions']) | set(h['validation_sessions']))
                assert test_sessions == set(h['test_sessions'])
    OUT.mkdir(parents=True, exist_ok=True)
    windows = np.stack([f['window'] for f in frames])
    # Export the regenerated pre-normalization float signal for audit/reuse.
    np.savez_compressed(OUT/'float_windows.npz', windows=windows,
                        truth=np.stack([f['truth'] for f in frames]),
                        session=np.array([f['session'] for f in frames]),
                        frame_id=np.array([f['frame_id'] for f in frames]),
                        aligned_start_s=np.array([f['start'] for f in frames]))
    sigma = np.array([session_stats[f['session']]['noise_std'] for f in frames])
    halves = windows.reshape(-1,48,2,2).mean(axis=-1)
    amplitude = np.sqrt(np.mean(((halves[:,:,0]-halves[:,:,1])/2)**2, axis=1))
    provenance.update({str(p.relative_to(ROOT)): digest(p) for p in [Path(__file__), HERE/'plots.py',
                       HERE.parent/'replay_precrc_buffer.py', HERE.parent/'preamble_tracker.py',
                       HERE.parent/'manchester_phase.py', HERE.parent/'benchmark_precrc.py']})
    config = dict(points=POINTS, noise_seeds=SEEDS, noise_realizations=5,
                  tensorflow_device='CPU', sessions=session_stats, inputs_sha256=provenance,
                  models=[d.details for d in decoders], acquisition_fps=30, resampling_hz=20, link_bit_s=5,
                  noise_estimator='sqrt(mean((x[2k]-x[2k+1])**2)/2), pooled aligned within-half-bit residual DOF',
                  snr_definition='A=sqrt(mean_bits(((mean_first_half-mean_second_half)/2)**2)); sigma=A*10**(-SNR_dB/20)',
                  intervals='95% two-sided Clopper-Pearson on pooled bit counts; repeated windows/bits are correlated; not independent capture confidence',
                  random_pairing='same standardized Gaussian draws across points in each perturbation family; families independent')
    (OUT/'config.json').write_text(json.dumps(config,indent=2)+'\n')
    summary = dict(sanity=sanity, conditions=[])
    subset = np.array([f['distance_m'] <= 5 for f in frames])
    fields = ['perturbation','point','realization','seed','session','distance_m','lighting','preproc','frame_id','n_bits']
    for name in NAMES:
        fields += [name+'_errors',name+'_error_positions',name+'_exact_window']
    with (OUT/'results.csv').open('w',newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for family_index, (family, points) in enumerate(POINTS.items()):
            for point in points:
                counts = []
                realizations = 1 if family == 'timing_offset' else 5
                for realization in range(realizations):
                    seed = SEEDS[realization]+family_index*100000
                    rng = np.random.default_rng(seed)
                    if family == 'timing_offset':
                        perturbed = np.array([[sample(f['values'], f['start']+point/1000+(i+.5)*.05)
                                               for i in range(192)] for f in frames])
                    elif family == 'contrast':
                        mean = windows.mean(axis=1,keepdims=True)
                        perturbed = mean+point*(windows-mean)+rng.normal(size=windows.shape)*sigma[:,None]
                    else:
                        perturbed = windows+rng.normal(size=windows.shape)*(amplitude*10**(-point/20))[:,None]
                    cb = classic(perturbed)
                    errors = np.zeros((len(frames),len(NAMES)),dtype=int)
                    for i, (frame, window) in enumerate(zip(frames,perturbed)):
                        q = np.frombuffer(normalize(window),dtype=np.int8)
                        predictions = [cb[i]]+[d.predict(q) for d in decoders]
                        row = {k: frame[k] for k in ['session','distance_m','lighting','preproc','frame_id']}
                        row.update(perturbation=family,point=point,realization=realization+1,
                                   seed=seed if realizations>1 else '',n_bits=48)
                        for j, (name, prediction) in enumerate(zip(NAMES,predictions)):
                            positions = np.flatnonzero(prediction != frame['truth']).tolist()
                            errors[i,j] = len(positions)
                            row[name+'_errors'] = len(positions)
                            row[name+'_error_positions'] = json.dumps(positions,separators=(',',':'))
                            row[name+'_exact_window'] = int(not positions)
                        writer.writerow(row)
                    counts.append(errors)
                counts = np.stack(counts)
                entry = dict(perturbation=family,point=point,realizations=realizations,
                             all_sessions={name: metric(counts[:,:,j]) for j,name in enumerate(NAMES)},
                             **{'1_5m': {name: metric(counts[:,subset,j]) for j,name in enumerate(NAMES)}})
                entry['per_realization'] = [{name: metric(c[:,j]) for j,name in enumerate(NAMES)} for c in counts]
                entry['mean_errors_per_realization'] = {name: float(counts[:,:,j].sum(axis=1).mean()) for j,name in enumerate(NAMES)}
                summary['conditions'].append(entry)
                print(json.dumps(entry),flush=True)
    plot(summary)
    if args.aug:
        summary['runtime_seconds'] = time.time() - started
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')


def plot(summary):
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.size':7,'pdf.fonttype':42})
    labels = {'timing_offset':'Residual timing offset (ms)', 'contrast':'Contrast factor (dimensionless)', 'snr':'Added-noise SNR (dB)'}
    for family in POINTS:
        entries = [e for e in summary['conditions'] if e['perturbation'] == family]
        fig, axes = plt.subplots(2,1,figsize=(3.5,4.7)) if len(NAMES) == 4 else plt.subplots(1,2,figsize=(7.16,2.8))
        for ax, group, label in zip(axes,['all_sessions','1_5m'],['1–7 m','1–5 m']):
            for j,(name,legend) in enumerate(zip(NAMES,LABELS)):
                for i,e in enumerate(entries):
                    m = e[group][name]
                    errorpoint(ax,e['point'],m['errors'],m['n_bits'],f'C{j}',marker=['o','s','^','D'][j],label=legend if i==0 else None)
                # Connect observed BER, substituting CP upper bound only at zero.
                ax.plot([e['point'] for e in entries],
                        [e[group][name]['ber'] or e[group][name]['ber_95cp'][1] for e in entries],
                        color=f'C{j}',linewidth=.6,alpha=.6)
            ax.set(xlabel=labels[family],ylabel='Bit error rate (dimensionless)',yscale='log')
            ax.text(.5,.97,label,ha='center',va='top',transform=ax.transAxes)
            if len(NAMES) == 3:
                ax.legend(fontsize=6,loc='lower left')
        if len(NAMES) == 4:
            handles, legends = axes[0].get_legend_handles_labels()
            fig.legend(handles, legends, loc='upper center', ncol=2, fontsize=6)
        fig.text(.5,.01,'95% CP intervals; open markers: zero errors at upper bound',ha='center',fontsize=6)
        fig.tight_layout(rect=(0,.04,1,.92 if len(NAMES) == 4 else 1))
        fig.savefig(OUT/f'ber_vs_{family}.png',dpi=300)
        fig.savefig(OUT/f'ber_vs_{family}.pdf')
        plt.close(fig)


if __name__ == '__main__':
    main()
