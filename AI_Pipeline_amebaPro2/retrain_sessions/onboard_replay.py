#!/usr/bin/env python3
"""Replay repeat-3 AUG tensors on the paired CPU/NPU board, without CRC filtering."""
import argparse
import csv
import json
from pathlib import Path
import re
import sys
import time
import numpy as np
from scipy.stats import beta

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'ameba_deployment/aug'))
from common import MODEL, SKETCH, DATA, CACHE, digest, inputs, predict, HISTORY, ROBUSTNESS, REPLAY_OUT

EVAL=re.compile(r'\[GOAL2_EVAL\] cpu=([0-9A-Fa-f]{96}) npu=([0-9A-Fa-f]{96})$')


def metric(pred, truth):
    n=int(truth.size)
    e=int(np.count_nonzero(pred!=truth))
    return dict(errors=e,n_bits=n,ber=e/n,
        ber_95cp=[float(beta.ppf(.025,e,n-e+1)) if e else 0.,
                  float(beta.ppf(.975,e+1,n-e)) if e<n else 1.],
        windows=len(truth),exact_windows=int(np.sum(np.all(pred==truth,axis=1))))


def agreement(a,b):
    delta=np.abs(a.astype(int)-b.astype(int))
    return dict(bit_agreement=float(np.mean((a>=0)==(b>=0))),
                bit_disagreements=int(np.sum((a>=0)!=(b>=0))),
                raw_equal_values=int(np.sum(a==b)),values=int(a.size),
                mae_lsb=float(delta.mean()),max_lsb=int(delta.max()))


def read(port,log):
    line=port.readline().decode(errors='replace').strip()
    if line:
        log.write(json.dumps(dict(time_ns=time.time_ns(),line=line))+'\n')
    if '[GOAL2_ERROR]' in line:
        raise RuntimeError(line)
    return line


def identity(port,log,timeout):
    port.write(b'\nGOAL2 INFO\n')
    port.flush()
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        line=read(port,log)
        if line.startswith('[GOAL2_READY]'):
            fields=dict(re.findall(r'(\w+)=([^ ]+)',line))
            if (fields.get('model_sha256')!=digest(MODEL) or
                fields.get('accuracy')!='buffered-v1' or
                fields.get('build')!='paired-int8-pipeline-v2' or
                fields.get('bank')!='64' or fields.get('pairs')!='512'):
                raise RuntimeError('Board identity/model contract mismatch; no EVAL/RUN sent: '+line)
            return line
    raise TimeoutError('No GOAL2_READY identity')


def evaluate(port,x,log,timeout,retries,interval):
    # EVAL has no request ID. On timeout, re-establish an INFO barrier before retry,
    # so a late response cannot be assigned to the next input.
    for attempt in range(retries+1):
        if attempt:
            identity(port,log,max(timeout,12))
        port.write(b'GOAL2 EVAL '+x.tobytes().hex().encode()+b'\n')
        port.flush()
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            line=read(port,log)
            m=EVAL.fullmatch(line)
            if m:
                result=[np.frombuffer(bytes.fromhex(v),np.int8).copy() for v in m.groups()]
                time.sleep(interval)
                return result
        log.write(json.dumps(dict(event='eval_timeout',attempt=attempt))+'\n')
    raise TimeoutError('EVAL retries exhausted')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port',default='/dev/ttyUSB0')
    p.add_argument('--output',type=Path,default=REPLAY_OUT)
    p.add_argument('--dry-run',action='store_true')
    p.add_argument('--identity-only',action='store_true')
    p.add_argument('--timeout',type=float,default=3)
    p.add_argument('--retries',type=int,default=2)
    p.add_argument('--interval',type=float,default=.05)
    a=p.parse_args()
    if not np.isfinite(a.timeout) or a.timeout<1 or not 0<=a.retries<=5 or not np.isfinite(a.interval) or a.interval<.01:
        p.error('timeout >=1, retries 0..5, interval >=.01 required')
    if a.dry_run and a.identity_only:
        p.error('dry-run and identity-only are mutually exclusive')
    d,ids,groups=inputs(with_offsets=not a.identity_only)
    hosts={offset:predict(x) for offset,x in groups}
    config=dict(model_sha256=digest(MODEL),offsets_ms=[o for o,_ in groups],
        windows_per_offset=len(ids),sessions=sorted(set(d['session'][ids].tolist())),
        acquisition_fps=30,resampling_hz=20,link_bit_s=5,
        host_resolver='BUILTIN_WITHOUT_DEFAULT_DELEGATES',
        note='Pre-CRC direct bits. CP intervals on pooled bits are descriptive; captured bits and offsets are correlated.',
        source_sha256={str(Path(__file__).name):digest(__file__), 'model_header':digest(SKETCH/'vlc_cpu_model.h'),
            'dataset.npz':digest(DATA),'windows_offsets.npz':digest(CACHE),
            'robustness.py':digest(ROBUSTNESS),
            'history_all.json':digest(HISTORY)})
    if a.dry_run:
        print(json.dumps(dict(valid=True,**config),indent=2))
        return 0
    import serial
    if a.identity_only:
        with serial.Serial(a.port,115200,timeout=.3,write_timeout=2) as port:
            print(identity(port,sys.stdout,12))
        return 0
    if a.output.exists():
        p.error('Output exists; refusing to replace any measurements')
    a.output.mkdir(parents=True)
    (a.output/'config.json').write_text(json.dumps(config,indent=2)+'\n')
    report=dict(complete=False,conditions=[],**config)
    error=None
    try:
        with (a.output/'events.jsonl').open('x',buffering=1) as log:
            with serial.Serial(a.port,115200,timeout=.3,write_timeout=2) as port:
                report['identity']=identity(port,log,12)
                fields=['offset_ms','session','frame_id','input_int8_hex','truth_bits',
                        'host_int8_hex','cpu_int8_hex','npu_int8_hex']
                with (a.output/'raw.csv').open('x',newline='',buffering=1) as stream:
                    w=csv.DictWriter(stream,fieldnames=fields)
                    w.writeheader()
                    for offset,xs in groups:
                        cpu,npu=[],[]
                        for j,(idx,x) in enumerate(zip(ids,xs)):
                            c,n=evaluate(port,x,log,a.timeout,a.retries,a.interval)
                            cpu.append(c);npu.append(n)
                            w.writerow(dict(offset_ms=offset,session=str(d['session'][idx]),
                                frame_id=int(d['frame_index'][idx]),input_int8_hex=x.tobytes().hex(),
                                truth_bits=''.join(map(str,d['y'][idx].tolist())),
                                host_int8_hex=hosts[offset][j].tobytes().hex(),
                                cpu_int8_hex=c.tobytes().hex(),npu_int8_hex=n.tobytes().hex()))
                            if (j+1)%100==0:
                                print(f'offset={offset:+} ms completed={j+1}/800',flush=True)
                        c,n=np.stack(cpu),np.stack(npu)
                        truth=d['y'][ids]
                        report['conditions'].append(dict(offset_ms=offset,
                            cpu=metric(c>=0,truth),npu=metric(n>=0,truth),
                            host=metric(hosts[offset]>=0,truth),cpu_npu=agreement(c,n),
                            cpu_host=agreement(c,hosts[offset]),npu_host=agreement(n,hosts[offset])))
                        (a.output/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
        report['complete']=True
    except (Exception,KeyboardInterrupt) as exc:
        error=f'{type(exc).__name__}: {exc}'
    report['error']=error
    (a.output/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(output=str(a.output),complete=report['complete'],error=error)))
    return int(error is not None)


if __name__=='__main__':
    raise SystemExit(main())
