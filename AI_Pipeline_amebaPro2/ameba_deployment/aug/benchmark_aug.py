#!/usr/bin/env python3
"""Capture paired SAME-BOARD CPU-reference / NPU INT8 inference timings.

Requires Ameba_CPU_NPU_Benchmark_AUG firmware, not the normal receiver firmware.
Does not control TX, measure channel BER/PDR, or claim pipeline FPS.
"""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import time
import serial

from common import ROOT, HERE, SKETCH, MODEL, digest, WORK, LATENCY_OUT, benchmark_paths
PAIR=re.compile(r'\[GOAL2_PAIR\] i=(\d+) sample=(\d+) cpu_core_us=(\d+) cpu_full_us=(\d+) npu_core_us=(\d+) npu_full_us=(\d+)$')
OUTPUT=re.compile(r'\[GOAL2_OUTPUT\] sample=(\d+) expected=([0-9A-F]{96}) npu=([0-9A-F]{96})$')


def describe(values):
    ordered=sorted(values)
    return dict(n=len(values),mean_us=statistics.mean(values),median_us=statistics.median(values),
        p95_nearest_rank_us=ordered[math.ceil(.95*len(ordered))-1],max_us=max(values),min_us=min(values))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port',default='/dev/ttyUSB0');p.add_argument('--session',required=True)
    p.add_argument('--dry-run',action='store_true');p.add_argument('--rounds',type=int,default=3);p.add_argument('--timeout',type=float,default=600)
    a=p.parse_args()
    if not 1<=a.rounds<=10 or a.timeout<30:p.error('rounds 1..10, timeout >=30 required')
    if a.session!=Path(a.session).name or a.session in ('.','..'):p.error('Invalid session name')
    out=LATENCY_OUT/a.session
    if out.exists():p.error('Session exists; never overwrite measurements')
    provenance=json.loads((SKETCH/'provenance.json').read_text())
    paths=[str((SKETCH/name).relative_to(ROOT)) for name in
        (SKETCH.name+'.ino', 'vlc_cpu_model.h', 'vlc_cpu_bank.h', 'vlc_cpu_nbg.h')]
    metadata=vars(a)|benchmark_paths(a.session)|dict(kind='same_board_reference_microbenchmark',source_sha256={
        s:hashlib.sha256((ROOT/s).read_bytes()).hexdigest() for s in paths},provenance=provenance,
        build_flags='-O3 -DTF_LITE_STATIC_MEMORY -DNDEBUG',
        timing='CPU core: complete 12-op static graph; NPU core: vip_run_network. '
            'Full API includes input copy; NPU also includes cache/buffer/API/output copy. '
            'UART and quality checks outside measured calls; AB/BA alternating order. '
            'Video capture domain active; JPEG/ROI processing inactive. Not CPU utilization or power.',
        baseline='Official TensorFlow Lite 2.14 reference kernels; not optimized CMSIS-NN or best achievable CPU.')
    if a.dry_run:
        print(json.dumps(metadata,indent=2));return 0
    check=json.loads((WORK/'check.json').read_text())
    if check['model_sha256']!=digest(MODEL) or provenance.get('nbg_sha256')!=check['nbg_sha256']:
        p.error('Convert, check, and export AUG headers before timing')
    out.mkdir(parents=True)
    (out/'metadata.json').write_text(json.dumps(metadata,indent=2)+'\n')
    rows=[];quality=[];error=None;completed=0
    with (out/'events.jsonl').open('w',buffering=1) as log:
        try:
            with serial.Serial(a.port,115200,timeout=.3,write_timeout=2) as port:
                def read():
                    s=port.readline().decode(errors='replace').strip()
                    if s:log.write(json.dumps(dict(host_time_ns=time.time_ns(),line=s))+'\n')
                    if '[GOAL2_ERROR]' in s:raise RuntimeError(s)
                    return s
                port.write(b'\nGOAL2 INFO\n');port.flush();end=time.monotonic()+12
                ready=None
                while time.monotonic()<end:
                    s=read()
                    if s.startswith('[GOAL2_READY]'):ready=s;break
                if ready is None or provenance['model_sha256'] not in ready:
                    raise RuntimeError('Benchmark firmware identity/model mismatch; no RUN sent')
                if 'bank=64 pairs=512' not in ready:raise RuntimeError('Unexpected bank/timing contract')
                for round_id in range(1,a.rounds+1):
                    print(f'[RUN] round {round_id}/{a.rounds}; UART quiet during paired timing',flush=True)
                    port.write(b'GOAL2 RUN\n');port.flush();end=time.monotonic()+a.timeout
                    pairs=[];outputs=[];parity=False;done=False
                    while time.monotonic()<end:
                        s=read()
                        if s=='[GOAL2_CPU_PARITY] unequal_values=0':parity=True
                        m=PAIR.fullmatch(s)
                        if m:
                            v=list(map(int,m.groups()));pairs.append(dict(zip(
                                ['round','i','sample','cpu_core_us','cpu_full_us','npu_core_us','npu_full_us'],
                                [round_id]+v)))
                        m=OUTPUT.fullmatch(s)
                        if m:outputs.append((int(m[1]),bytes.fromhex(m[2]),bytes.fromhex(m[3])))
                        if s.startswith('[GOAL2_COMPLETE]'):done=True;break
                    if not done:raise TimeoutError('Incomplete paired benchmark')
                    if not parity or len(pairs)!=512 or [r['i'] for r in pairs]!=list(range(512)):
                        raise RuntimeError('Missing CPU parity or timing rows')
                    if sorted(i for i,_,_ in outputs)!=list(range(64)):
                        raise RuntimeError('Missing quality outputs')
                    if any(r['sample']!=r['i']%64 for r in pairs):raise RuntimeError('Sample order mismatch')
                    if any(not 0<r[k]<a.timeout*1e6 for r in pairs for k in
                           ('cpu_core_us','cpu_full_us','npu_core_us','npu_full_us')):
                        raise RuntimeError('Invalid timing value')
                    diffs=[];bit_errors=0
                    for idx,cpu,npu in outputs:
                        c=[v if v<128 else v-256 for v in cpu];n=[v if v<128 else v-256 for v in npu]
                        diffs.extend(abs(x-y) for x,y in zip(c,n))
                        bit_errors+=sum((x>=0)!=(y>=0) for x,y in zip(c,n))
                    quality.append(dict(round=round_id,bank_windows=64,cpu_reference_unequal_values=0,
                        cpu_npu_mae_lsb=statistics.mean(diffs),cpu_npu_max_lsb=max(diffs),
                        cpu_npu_bit_disagreements=bit_errors,compared_bits=64*48))
                    rows.extend(pairs);completed+=1
                    (out/'quality.json').write_text(json.dumps(quality,indent=2)+'\n')
                    with (out/'paired_timings.csv').open('w',newline='') as f:
                        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
                    print(f'[DONE] round {round_id}: CPU full {statistics.mean(r["cpu_full_us"] for r in pairs)/1000:.3f} ms; '
                        f'NPU full {statistics.mean(r["npu_full_us"] for r in pairs)/1000:.3f} ms',flush=True)
        except (Exception,KeyboardInterrupt) as exc:
            error=f'{type(exc).__name__}: {exc}';print('[ERROR]',error,flush=True)
    result=dict(error=error,complete=error is None and completed==a.rounds,
        completed_rounds=completed,requested_rounds=a.rounds,quality=quality,
        timing={k:describe([r[k] for r in rows]) for k in
                ('cpu_core_us','cpu_full_us','npu_core_us','npu_full_us')} if rows else {},
        note='Same-board CPU reference microbenchmark. Not channel BER/PDR, production FPS, '
             'CPU utilization, power, or a claim against optimized CPU implementations.')
    if rows:
        result['full_api_mean_ratio_cpu_over_npu']=result['timing']['cpu_full_us']['mean_us']/result['timing']['npu_full_us']['mean_us']
    (out/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print('[COMPLETE]',out,flush=True);return int(error is not None)


if __name__=='__main__':raise SystemExit(main())
