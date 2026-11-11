#!/usr/bin/env python3
"""Host-only residual timing sweep on the fixed fresh-campaign manifest."""
import sys
sys.dont_write_bytecode = True
import argparse
import csv
import json
import time
from pathlib import Path
import numpy as np
from fresh_common import (HERE, BENCH, AUG, RETRAINED, AUG_SHA, load_json, parse_manifest,
    load_windows, output_path, reference_inference, apply_polarity, bits_hex, metric, digest)
from robustness import classic, sample, normalize, PreambleTracker, refine
from plots import errorpoint

OFFSETS = list(range(-45, 46, 5))
DECODERS = ('classic', 'aug_host', 'retrained_host')


def offset_windows(frames, offset):
    # Exactly the repeat-3 construction: offset AFTER alignment/refinement.
    return np.array([[sample(f['values'], f['start']+offset/1000+(i+.5)*.05)
                      for i in range(192)] for f in frames], dtype=np.float64)


def score_windows(frames, windows):
    qs = np.stack([np.frombuffer(normalize(w), dtype=np.int8) for w in windows])
    predictions = dict(classic=classic(windows),
        aug_host=reference_inference(AUG, qs)[1],
        retrained_host=reference_inference(RETRAINED, qs)[1])
    rows = []
    for i, f in enumerate(frames):
        row = {k:f[k] for k in ('session','frame_id','distance_m','lighting','condition')}
        row.update(n_bits=48, input_int8_hex=qs[i].tobytes().hex())
        for d in DECODERS:
            pred, flip = apply_polarity(predictions[d][i], 'preamble')
            positions = np.flatnonzero(pred != bits_hex(f['frame_hex'])).tolist()
            row.update({d+'_errors':len(positions),d+'_error_positions':json.dumps(positions),
                        d+'_exact_window':not positions,d+'_flipped':flip,
                        d+'_bits':''.join(map(str,pred))})
        rows.append(row)
    return rows


def grouped(rows):
    groups = {'all':rows, **{'lighting:'+v:[r for r in rows if r['lighting']==v]
                            for v in sorted({r['lighting'] for r in rows})}}
    return {g:dict(metrics={d:metric(local,d) for d in DECODERS},
                   flipped_windows={d:sum(r[d+'_flipped'] for r in local) for d in DECODERS})
            for g,local in groups.items()}


def check_zero(rows, main_summary, main_output):
    """Require exact paired predictions, tensors, errors, flips and pooled metrics."""
    for d in DECODERS:
        expected = {}
        for s in main_summary['sessions']:
            with (main_output/f'{d}__{s}.csv').open(newline='') as stream:
                for r in csv.DictReader(stream):
                    expected[r['session'],int(r['frame_id'])] = r
        assert set(expected) == {(r['session'],r['frame_id']) for r in rows}
        for r in rows:
            e = expected[r['session'],r['frame_id']]
            assert (r['input_int8_hex'],r[d+'_bits'],r[d+'_errors'],r[d+'_flipped']) == (
                e['input_int8_hex'],e['predicted_bits'],int(e['errors']),e['polarity_flipped']=='True'), (d,r['session'],r['frame_id'])
    # JSON round trip normalizes CP tuples to the serialized main-summary lists.
    for g, entry in grouped(rows).items():
        for d in DECODERS:
            assert json.loads(json.dumps(entry['metrics'][d])) == main_summary['pooled'][g]['metrics'][d], (g,d)
            assert entry['flipped_windows'][d] == main_summary['pooled'][g]['flipped_windows'][d]


def regenerate(records):
    provenance = {}
    for session in dict.fromkeys(r['session'] for r in records):
        folder = BENCH/session
        meta = load_json(folder/'metadata.json')
        capture = BENCH/Path(meta['source_capture']).name
        paths = [capture/'samples.json',capture/'frame_decisions.json',capture/'tx_ground_truth.json',
                 folder/'metadata.json',folder/'frame_decisions.json']
        provenance.update({str(p):digest(p) for p in paths})
        values = load_json(paths[0])
        nominal = {r['frame_id']:r for r in load_json(paths[1])}
        tracker = PreambleTracker()
        for r in records:
            if r['session'] != session:
                continue
            coarse = tracker.align(values,nominal[r['frame_id']]['start_offset_s'])
            phase, window = refine(values,coarse['start'])
            assert abs(phase['start']-r['source']['start_offset_s']) < 1e-8, (session,r['frame_id'])
            assert normalize(window) == r['q'].tobytes(), (session,r['frame_id'],'regenerated tensor')
            assert np.array_equal(classic(window)[0],np.array(list(r['source']['classic_bits']),np.uint8))
            r.update(start=phase['start'],values=values)
        print('Regenerated '+session,flush=True)
    return provenance


def report(summary):
    lines = ['# Fresh timing sweep','',
        '24 fixed primary sessions; per-decoder preamble-only polarity; builtin host kernels without default delegates, one thread.',
        'Offsets are added after tracker alignment and Manchester phase refinement, with no re-alignment. Rebuild 192 samples at 20 Hz, then reuse normalize and INT8 quantization.',
        '30 FPS acquisition; 20 Hz resampling; 5 bit/s link. Exact two-sided 95% CP intervals pool correlated bits/windows.',
        f"Offset zero matches the main scorer, every tensor and decoded window: {summary['offset_zero_equal']}.",
        '', '| Offset ms | Group | Decoder | Errors / bits | BER [95% CP] | Exact / windows | Flips |',
        '|---:|---|---|---:|---|---:|---:|']
    for e in summary['conditions']:
        for g, entry in e['groups'].items():
            for d,m in entry['metrics'].items():
                lo,hi = m['ber_95cp']
                lines.append(f"| {e['offset_ms']:+d} | {g} | {d} | {m['errors']}/{m['n_bits']} | {m['ber']:.8g} [{lo:.8g}, {hi:.8g}] | {m['exact_windows']}/{m['n_windows']} | {entry['flipped_windows'][d]} |")
    return '\n'.join(lines)+'\n'


def plot(summary, out):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.size':7,'pdf.fonttype':42})
    fig, axes = plt.subplots(3,1,figsize=(3.5,6.5))
    entries = summary['conditions']
    for ax,g,title in zip(axes,('all','lighting:off','lighting:on'),('1–7 m, all sessions','Lamps off','Lamps on')):
        for j,(d,label) in enumerate(zip(DECODERS,('Classic','CNN (retrained + aug.)','CNN (retrained)'))):
            for i,e in enumerate(entries):
                m = e['groups'][g]['metrics'][d]
                errorpoint(ax,e['offset_ms'],m['errors'],m['n_bits'],f'C{j}',marker=['o','D','^'][j],label=label if i==0 else None)
            ax.plot([e['offset_ms'] for e in entries],
                    [e['groups'][g]['metrics'][d]['ber'] or e['groups'][g]['metrics'][d]['ber_95cp'][1] for e in entries],color=f'C{j}',linewidth=.6,alpha=.6)
        ax.set(xlabel='Residual timing offset (ms)',ylabel='BER',yscale='log')
        ax.set_xticks([-40,-20,0,20,40])
        ax.text(.5,.97,title,ha='center',va='top',transform=ax.transAxes)
    fig.legend(*axes[0].get_legend_handles_labels(),loc='upper center',ncol=2,fontsize=6)
    fig.text(.5,.01,'95% CP intervals; open markers: zero errors at upper bound',ha='center',fontsize=6)
    fig.tight_layout(rect=(0,.04,1,.93))
    for ext in ('pdf','png'):
        fig.savefig(out/f'ber_vs_timing_offset_full.{ext}',dpi=300)
    plt.close(fig)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,default=HERE/'results_v2/fresh/campaign_manifest.json')
    p.add_argument('--main-output',type=Path,default=HERE/'results_v2/fresh/campaign_host_v2')
    p.add_argument('--output',type=Path,default=HERE/'results_v2/fresh/robustness_fresh')
    a = p.parse_args(argv); started = time.time()
    out = output_path(a.output)
    manifest = load_json(a.manifest); sessions = parse_manifest(manifest)
    assert len(sessions) == 24 and not manifest['excluded']
    main_summary = load_json(a.main_output/'summary.json')
    assert main_summary['accepted'] and main_summary['polarity']=='preamble'
    assert main_summary['manifest_sha256']==digest(a.manifest)
    assert main_summary['model_sha256']==dict(aug=digest(AUG),retrained=digest(RETRAINED))
    assert digest(AUG)==AUG_SHA
    records = load_windows(sessions)
    assert len(records)==2400 and all(sum(r['session']==s['session'] for r in records)==100 for s in sessions)
    for s in sessions:
        assert main_summary['input_sha256'][s['session']]==digest(BENCH/s['session']/'frame_decisions.json')
    provenance = regenerate(records)
    # Validate zero before creating outputs or evaluating other offsets.
    zero = score_windows(records,offset_windows(records,0))
    check_zero(zero,main_summary,a.main_output)
    out.mkdir(parents=True)
    summary = dict(manifest=manifest,polarity='preamble',offsets_ms=OFFSETS,offset_zero_equal=True,
        model_sha256=main_summary['model_sha256'],main_summary_sha256=digest(a.main_output/'summary.json'),
        source_sha256={n:digest(HERE/n) for n in ('robustness_fresh.py','fresh_common.py','robustness.py','robustness_offgrid.py','export_offsets.py')},
        input_sha256=provenance,acquisition_fps=30,resampling_hz=20,link_bit_s=5,conditions=[])
    with (out/'per_window.csv').open('x',newline='') as stream:
        writer = csv.DictWriter(stream,fieldnames=['offset_ms']+list(zero[0])); writer.writeheader()
        for offset in OFFSETS:
            rows = zero if offset==0 else score_windows(records,offset_windows(records,offset))
            entry = dict(offset_ms=offset,groups=grouped(rows)); summary['conditions'].append(entry)
            writer.writerows(dict(offset_ms=offset,**r) for r in rows)
            print(json.dumps(dict(offset_ms=offset,errors={d:m['errors'] for d,m in entry['groups']['all']['metrics'].items()})),flush=True)
    with (out/'metrics.csv').open('x',newline='') as stream:
        writer = csv.DictWriter(stream,fieldnames=['offset_ms','group','decoder','errors','n_bits','ber','cp_low','cp_high','exact_windows','n_windows','flipped_windows']); writer.writeheader()
        for e in summary['conditions']:
            for g,entry in e['groups'].items():
                for d,m in entry['metrics'].items():
                    writer.writerow(dict(offset_ms=e['offset_ms'],group=g,decoder=d,errors=m['errors'],n_bits=m['n_bits'],ber=m['ber'],cp_low=m['ber_95cp'][0],cp_high=m['ber_95cp'][1],exact_windows=m['exact_windows'],n_windows=m['n_windows'],flipped_windows=entry['flipped_windows'][d]))
    plot(summary,out)
    summary['runtime_seconds']=time.time()-started
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    (out/'REPORT.md').write_text(report(summary))
    print(json.dumps(dict(output=str(out),runtime_seconds=summary['runtime_seconds'])))


if __name__=='__main__':
    main()
