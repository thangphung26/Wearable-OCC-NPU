#!/usr/bin/env python3
"""Pre-registered fresh-session scoring, with an explicit host-only audit mode."""
import sys
sys.dont_write_bytecode = True
import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
from fresh_common import (AUG, RETRAINED, AUG_SHA, HERE, BENCH, SCHEMA, RULE,
    load_json, parse_manifest, load_windows, output_path, raw_aug, payload_check,
    training_words, bits_hex, decision_bytes, digest, metric, tflite_predict, hex_value, reference_inference, apply_polarity)

DECODERS = ('classic','aug_cpu','aug_npu','aug_host','retrained_host')


def agreement(rows, a, b):
    if any(r['raw'].get(a) is None or r['raw'].get(b) is None for r in rows):
        return dict(available=False)
    x = np.stack([r['raw'][a] for r in rows]); y = np.stack([r['raw'][b] for r in rows])
    return dict(available=True, values=int(x.size), threshold_disagreements=int(np.sum((x>=0)!=(y>=0))),
        raw_equal_values=int(np.sum(x==y)), raw_unequal_values=int(np.sum(x!=y)),
        raw_equal_windows=int(np.sum(np.all(x==y,axis=1))), n_windows=len(rows))


def aggregate(rows):
    return dict(metrics={d: metric(rows,d) if rows[0][d+'_errors'] is not None else dict(available=False)
                         for d in DECODERS},
        flipped_windows={d: sum(r.get(d+'_flipped',False) for r in rows) if rows[0][d+'_errors'] is not None else None for d in DECODERS},
        kernel_disagreements={d: sum(r['kernel_disagreements'][d] for r in rows) for d in ('aug_host','retrained_host')},
        cpu_npu=agreement(rows,'aug_cpu','aug_npu'), cpu_host=agreement(rows,'aug_cpu','aug_host'))


def read_board(folder, records):
    summary = load_json(folder/'summary.json'); config = load_json(folder/'config.json')
    if not summary.get('complete') or summary.get('error') is not None or summary.get('model_sha256') != digest(AUG) or config.get('model_sha256') != digest(AUG):
        raise ValueError('Incomplete/wrong-model board replay')
    import re
    fields = dict(re.findall(r'(\w+)=([^ ]+)', summary.get('identity','')))
    if any(fields.get(k) != v for k,v in dict(model_sha256=AUG_SHA,accuracy='buffered-v1',
            build='paired-int8-pipeline-v2',bank='64',pairs='512').items()):
        raise ValueError('Missing/invalid AUG board identity')
    indexed = {}
    with (folder/'raw.csv').open(newline='') as stream:
        for row in csv.DictReader(stream):
            key = row['session'], int(row['frame_id'])
            if key in indexed:
                raise ValueError('Duplicate board window')
            for k in ('cpu_int8_hex','npu_int8_hex','host_int8_hex'):
                hex_value(row[k],48)
            indexed[key] = row
    expected = {(r['session'],r['frame_id']) for r in records}
    if set(indexed) != expected or set(config['sessions']) != {r['session'] for r in records}:
        raise ValueError('Board replay window/session set differs from manifest')
    for s in config['sessions']:
        if config['input_sha256'].get(s) != digest(BENCH/s/'frame_decisions.json'):
            raise ValueError('Replay source changed since board run')
    for r in records:
        b = indexed[r['session'],r['frame_id']]
        if b['frame_hex'] != r['frame_hex'] or b['input_int8_hex'] != r['q'].tobytes().hex():
            raise ValueError('Board replay tensor/truth mismatch')
    return indexed


def parity(summary, reference):
    checks = []
    for name in ('wide_off','wide_on','tight_on_primary'):
        actual = summary['conditions'][name]
        for scope, got, expected in [('group',actual,reference['groups'][name]), *[
            (s, summary['sessions'][s], reference['groups'][name]['sessions'][s])
            for s in summary['session_map'][name].values()]]:
            for new,old in [('classic','classic'),('retrained_host','candidate'),('aug_host','augmented')]:
                a,b = got['metrics'][new], expected['metrics'][old]
                passed = a == b
                checks.append(dict(condition=name,scope=scope,decoder=new,passed=passed,actual=a,expected=b))
    summary['controls_parity'] = checks
    if not all(c['passed'] for c in checks):
        raise AssertionError('Controls metric parity failed')


def report(summary):
    lines = ['# Fresh campaign evaluation','',RULE,'',
        'Manifest schema (version 1):','', '```json',json.dumps(SCHEMA,indent=2),'```','',
        'Exclusions are explicit and applied before scoring; all other windows are retained.',
        'Board metrics are unavailable in host-only mode. No capture-firmware CPU/NPU output is scored.',
        '', '| Group | Decoder | Errors / bits | BER [95% CP] | Exact / windows |',
        '|---|---|---:|---|---:|']
    for name,g in {**summary['conditions'],**summary['pooled']}.items():
        for d,m in g['metrics'].items():
            if m.get('available') is False:
                lines.append(f'| {name} | {d} | unavailable | unavailable | unavailable |'); continue
            lo,hi=m['ber_95cp']
            lines.append(f"| {name} | {d} | {m['errors']}/{m['n_bits']} | {m['ber']:.8g} [{lo:.8g}, {hi:.8g}] | {m['exact_windows']}/{m['n_windows']} |")
    lines += ['', '## Polarity and host kernels', '', f"Polarity: {summary['polarity']}. Flips use decoded 1010/0101 distances only; ties stay direct.",
        'Host models use BUILTIN_WITHOUT_DEFAULT_DELEGATES, num_threads=1. Diagnostic disagreements are before polarity correction.',
        '', '| Group | Decoder | Flipped windows | Reference vs XNNPACK bit disagreements |',
        '|---|---|---:|---:|']
    for name,g in {**summary['conditions'],**summary['pooled']}.items():
        for d in DECODERS:
            lines.append(f"| {name} | {d} | {g['flipped_windows'][d]} | {g['kernel_disagreements'].get(d, 'unavailable')} |")
    lines += ['', '## Payload audit','',json.dumps(summary['payload'],indent=2),'',
        'Unseen-payload requirement: '+str(summary['require_unseen_payload'])+'. Old-seed controls are expected to overlap 100%.',
        '', '## Board agreement','',
        'Threshold disagreements and raw equality (per condition, session and pooled) are recorded in summary.json.',
        '', '## Per-session tracker audit','',
        '| Session | Gain median | Weak fit | Fallback | Phase boundary | Missing gain / tracker / phase |',
        '|---|---:|---:|---:|---:|---|']
    for s,item in summary['sessions'].items():
        a=item['tracker']
        lines.append(f"| {s} | {a['fitted_preamble_gain_median']} | {a['weak_fit']} | {a['fallback']} | {a['boundary_hit']} | {a['missing_gain']}/{a['missing_tracker']}/{a['missing_phase']} |")
    lines += ['', 'Gain uses signed_optical_gain, falling back to tracker.gain, as in eval_controls.py.',
        '', '## Controls reproduction','']
    checks=summary.get('controls_parity',[])
    lines.append(f'{sum(c["passed"] for c in checks)}/{len(checks)} exact metric-dictionary comparisons passed (group and session; includes CP intervals).' if checks else 'Not requested.')
    lines += ['', '| Group | Decoder | Dry-run errors / bits | Controls errors / bits | Dry-run exact | Controls exact | Match |',
        '|---|---|---:|---:|---:|---:|---|']
    for c in checks:
        if c['scope']=='group':
            a,b=c['actual'],c['expected']
            lines.append(f"| {c['condition']} | {c['decoder']} | {a['errors']}/{a['n_bits']} | {b['errors']}/{b['n_bits']} | {a['exact_windows']}/{a['n_windows']} | {b['exact_windows']}/{b['n_windows']} | {c['passed']} |")
    lines += ['', 'No serial/USB or firmware execution is performed by this scorer. Board verification requires hardware.', '']
    return '\n'.join(lines)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--board-output',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--host-only',action='store_true')
    p.add_argument('--require-unseen-payload',action=argparse.BooleanOptionalAction,default=True)
    p.add_argument('--assert-controls',type=Path)
    p.add_argument('--polarity',choices=('direct','preamble'),default='direct')
    a=p.parse_args(argv)
    if a.host_only == bool(a.board_output):
        p.error('Choose exactly one of --host-only or --board-output')
    out=output_path(a.output); manifest=load_json(a.manifest)
    sessions=parse_manifest(manifest); records=load_windows(sessions)
    board=read_board(a.board_output,records) if a.board_output else None
    qs=np.stack([r['q'] for r in records])
    if digest(AUG) != AUG_SHA:
        raise ValueError('Unexpected AUG v2 model')
    hosts,aug_bits=reference_inference(AUG,qs)
    _,retrained=reference_inference(RETRAINED,qs)
    delegated={'aug_host':tflite_predict(AUG,qs),'retrained_host':tflite_predict(RETRAINED,qs)}
    for i,r in enumerate(records):
        raw=dict(aug_host=hosts[i],aug_cpu=None,aug_npu=None)
        predictions=dict(classic=np.array(list(r['source']['classic_bits']),dtype=np.uint8),
            aug_host=aug_bits[i],retrained_host=retrained[i],aug_cpu=None,aug_npu=None)
        if board:
            b=board[r['session'],r['frame_id']]
            if b['host_int8_hex'] != hosts[i].tobytes().hex():
                raise ValueError('Stored board-run host output differs from fresh host inference')
            for d,k in [('aug_cpu','cpu_int8_hex'),('aug_npu','npu_int8_hex')]:
                raw[d]=np.frombuffer(bytes.fromhex(b[k]),np.int8)
                predictions[d]=decision_bytes(b[k])
        r['kernel_disagreements']={d:int(np.sum(predictions[d]!=delegated[d][i])) for d in delegated}
        r['kernel_disagreement_positions']={d:np.flatnonzero(predictions[d]!=delegated[d][i]).tolist() for d in delegated}
        r['raw']=raw; truth=bits_hex(r['frame_hex']); r['truth_bits']=''.join(map(str,truth))
        r['csv']={}
        for d,pred in predictions.items():
            if pred is not None:
                pred,r[d+'_flipped']=apply_polarity(pred,a.polarity)
            positions=np.flatnonzero(pred!=truth).tolist() if pred is not None else None
            r[d+'_errors']=len(positions) if positions is not None else None
            r['csv'][d]=dict(session=r['session'],frame_id=r['frame_id'],frame_hex=r['frame_hex'],
                input_int8_hex=r['q'].tobytes().hex(),truth_bits=r['truth_bits'],available=pred is not None,
                predicted_bits=''.join(map(str,pred)) if pred is not None else '',
                raw_int8_hex=raw[d].tobytes().hex() if d in raw and raw[d] is not None else '',
                polarity_flipped=r.get(d+'_flipped'),
                reference_xnnpack_disagreement_positions=json.dumps(r['kernel_disagreement_positions'].get(d)),
                errors=r[d+'_errors'],error_positions=json.dumps(positions),exact_window=(not positions) if positions is not None else None)
    summary=dict(created_utc=datetime.now(timezone.utc).isoformat(),host_only=a.host_only,
        manifest=manifest,manifest_schema=SCHEMA,scoring_rule=RULE,polarity=a.polarity,
        kernel_disagreement_windows=[dict(session=r['session'],frame_id=r['frame_id'],positions=r['kernel_disagreement_positions']) for r in records if any(r['kernel_disagreements'].values())],require_unseen_payload=a.require_unseen_payload,
        acquisition_fps=30,resampling_hz=20,link_bit_s=5,
        model_sha256=dict(aug=digest(AUG),retrained=digest(RETRAINED)),
        input_sha256={s['session']:digest(BENCH/s['session']/'frame_decisions.json') for s in sessions},
        source_sha256={n:digest(HERE/n) for n in ('eval_fresh.py','fresh_common.py','eval_controls.py','export_dataset.py','train_eval.py')},
        manifest_sha256=digest(a.manifest),training_dataset_sha256=digest(HERE/'results_v2/data/dataset.npz'),
        excluded=manifest['excluded'],conditions={},sessions={},pooled={},session_map={})
    summary['payload']=payload_check([r['frame_hex'] for r in records],training_words())
    for s in sessions:
        name=s['session']; local=[r for r in records if r['session']==name]
        gains=[r['source'].get('signed_optical_gain',r['source'].get('tracker',{}).get('gain')) for r in local]
        valid=[g for g in gains if g is not None and np.isfinite(g)]
        audit=dict(fitted_preamble_gain_median=float(np.median(valid)) if valid else None,
            weak_fit=sum(bool(r['source'].get('tracker',{}).get('weak_fit')) for r in local),
            fallback=sum(bool(r['source'].get('tracker',{}).get('fallback')) for r in local),
            boundary_hit=sum(bool(r['source'].get('manchester_phase',{}).get('boundary_hit')) for r in local),
            missing_gain=len(gains)-len(valid),
            missing_tracker=sum(not {'weak_fit','fallback'} <= r['source'].get('tracker',{}).keys() for r in local),
            missing_phase=sum('boundary_hit' not in r['source'].get('manchester_phase',{}) for r in local))
        summary['sessions'][name]=dict(**aggregate(local),tracker=audit,**s)
        summary['session_map'].setdefault(s['condition'],{})[str(s['repeat'])]=name
    for c in summary['session_map']:
        summary['conditions'][c]=aggregate([r for r in records if r['condition']==c])
    subsets={'all':records}
    for key in ('lighting','distance_m'):
        for v in sorted({r[key] for r in records}):
            subsets[f'{key}:{v}']=[r for r in records if r[key]==v]
    for name,subset in subsets.items():
        summary['pooled'][name]=aggregate(subset)
    parity_failed=False
    if a.assert_controls:
        try:
            parity(summary,load_json(a.assert_controls))
        except AssertionError:
            parity_failed=True
            print(json.dumps(dict(controls_parity_failed=True, differing_windows=summary['kernel_disagreement_windows']),indent=2))
    summary['accepted']=(not a.require_unseen_payload or summary['payload']['passed']) and not parity_failed
    summary['board_cpu_host_equal']=None if a.host_only else summary['pooled']['all']['cpu_host']['raw_unequal_values']==0
    if summary['board_cpu_host_equal'] is False:
        summary['accepted']=False
    out.mkdir(parents=True)
    for s in sessions:
        for d in DECODERS:
            local=[r['csv'][d] for r in records if r['session']==s['session']]
            with (out/f"{d}__{s['session']}.csv").open('x',newline='') as stream:
                w=csv.DictWriter(stream,fieldnames=list(local[0])); w.writeheader(); w.writerows(local)
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    (out/'REPORT.md').write_text(report(summary))
    print(json.dumps(dict(output=str(out),accepted=summary['accepted'],payload=summary['payload'],
                         controls_parity=len(summary.get('controls_parity',[]))),indent=2))
    return int(not summary['accepted'])


if __name__=='__main__':
    raise SystemExit(main())
