#!/usr/bin/env python3
"""Replay stored session tensors on AUG_V2 only; never open serial in dry-run."""
import sys
sys.dont_write_bytecode = True
import argparse
import csv
import io
import json
import re
import time
import numpy as np
from fresh_common import (AUG, AUG_SHA, RULE, digest, load_json, parse_manifest,
                          load_windows, output_path, raw_aug, session_name)

EVAL = re.compile(r'\[GOAL2_EVAL\] cpu=([0-9A-Fa-f]{96}) npu=([0-9A-Fa-f]{96})$')


def read(port, log):
    line = port.readline().decode(errors='replace').strip()
    if line:
        log.write(json.dumps(dict(time_ns=time.time_ns(), line=line))+'\n')
        log.flush()
    if '[GOAL2_ERROR]' in line:
        raise RuntimeError(line)
    return line


def identity(port, log, timeout=12):
    expected = digest(AUG)
    if expected != AUG_SHA:
        raise RuntimeError('Unexpected local AUG v2 model; no EVAL sent')
    port.write(b'\nGOAL2 INFO\n'); port.flush()
    end = time.monotonic()+timeout
    while time.monotonic() < end:
        line = read(port, log)
        if line.startswith('[GOAL2_READY]'):
            fields = dict(re.findall(r'(\w+)=([^ ]+)', line))
            contract = dict(model_sha256=expected, accuracy='buffered-v1',
                            build='paired-int8-pipeline-v2', bank='64', pairs='512')
            if any(fields.get(k) != v for k,v in contract.items()):
                raise RuntimeError('Board identity/model contract mismatch; no EVAL sent: '+line)
            return line
    raise TimeoutError('No GOAL2_READY identity; no EVAL sent')


def evaluate(port, q, log, timeout, retries, interval):
    # Like onboard_replay.py: INFO barrier after timeout, since EVAL has no ID.
    for attempt in range(retries+1):
        if attempt:
            identity(port, log, max(timeout,12))
        port.write(b'GOAL2 EVAL '+q.tobytes().hex().encode()+b'\n'); port.flush()
        end = time.monotonic()+timeout
        while time.monotonic() < end:
            match = EVAL.fullmatch(read(port, log))
            if match:
                time.sleep(interval)
                return [np.frombuffer(bytes.fromhex(v), np.int8).copy() for v in match.groups()]
        log.write(json.dumps(dict(event='eval_timeout', attempt=attempt))+'\n'); log.flush()
    raise TimeoutError('EVAL retries exhausted')


def replay(port, records, hosts, log, writer, timeout=3, retries=0, interval=.05):
    ready = identity(port, log)
    mismatches = []
    for r,h in zip(records, hosts):
        c,n = evaluate(port,r['q'],log,timeout,retries,interval)
        positions = np.flatnonzero(c != h).tolist()
        writer.writerow(dict(session=r['session'], frame_id=r['frame_id'], frame_hex=r['frame_hex'],
            input_int8_hex=r['q'].tobytes().hex(), host_int8_hex=h.tobytes().hex(),
            cpu_int8_hex=c.tobytes().hex(), npu_int8_hex=n.tobytes().hex(),
            cpu_host_equal=not positions, cpu_host_unequal_values=len(positions)))
        if positions:
            item = dict(session=r['session'], frame_id=r['frame_id'], positions=positions,
                        cpu=c[positions].tolist(), host=h[positions].tolist())
            mismatches.append(item)
            print('CPU_HOST_MISMATCH '+json.dumps(item), flush=True)
    return ready, mismatches


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    group = p.add_mutually_exclusive_group()
    group.add_argument('--sessions', nargs='+')
    group.add_argument('--manifest', type=str)
    p.add_argument('--port', default='/dev/ttyUSB0')
    p.add_argument('--output', type=str)
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--identity-only', action='store_true')
    p.add_argument('--timeout', type=float, default=3)
    p.add_argument('--retries', type=int, default=0, help='Default 0 sends each window once; opt-in timeout retries use INFO barrier')
    p.add_argument('--interval', type=float, default=.05)
    a = p.parse_args(argv)
    if not np.isfinite(a.timeout) or a.timeout < 1 or not 0 <= a.retries <= 5 or not np.isfinite(a.interval) or a.interval < .01:
        p.error('Require timeout >=1, retries 0..5, interval >=.01')
    if a.dry_run and a.identity_only:
        p.error('--dry-run and --identity-only are mutually exclusive')
    if digest(AUG) != AUG_SHA:
        raise ValueError('Unexpected AUG v2 model')
    if a.identity_only:
        import serial
        with serial.Serial(a.port,115200,timeout=.3,write_timeout=2) as port:
            print(identity(port,sys.stdout))
        return 0
    if not (a.sessions or a.manifest) or not a.output:
        p.error('--sessions or --manifest, and --output required')
    out = output_path(a.output)
    sessions = parse_manifest(load_json(a.manifest)) if a.manifest else [dict(session=session_name(n)) for n in a.sessions]
    if len({s['session'] for s in sessions}) != len(sessions):
        raise ValueError('Duplicate sessions')
    records = load_windows(sessions)
    hosts = raw_aug(np.stack([r['q'] for r in records]))
    config = dict(model_sha256=digest(AUG), sessions=[s['session'] for s in sessions],
        windows=len(records), scoring_rule=RULE, retries=a.retries,
        input_sha256={s['session']:digest(__import__('fresh_common').BENCH/s['session']/'frame_decisions.json') for s in sessions})
    if a.dry_run:
        print(json.dumps(dict(valid=True, serial_opened=False, **config), indent=2)); return 0
    out.mkdir(parents=True)
    (out/'config.json').write_text(json.dumps(config,indent=2)+'\n')
    report = dict(complete=False, error=None, **config)
    try:
        import serial
        with (out/'events.jsonl').open('x',buffering=1) as log, (out/'raw.csv').open('x',newline='',buffering=1) as stream:
            writer = csv.DictWriter(stream,fieldnames=['session','frame_id','frame_hex','input_int8_hex',
                'host_int8_hex','cpu_int8_hex','npu_int8_hex','cpu_host_equal','cpu_host_unequal_values'])
            writer.writeheader()
            with serial.Serial(a.port,115200,timeout=.3,write_timeout=2) as port:
                ready, mismatches = replay(port,records,hosts,log,writer,a.timeout,a.retries,a.interval)
            report.update(identity=ready, cpu_host_mismatches=mismatches,
                          cpu_host_equal=not mismatches, complete=True)
    except (Exception,KeyboardInterrupt) as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    (out/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(output=str(out),complete=report['complete'],error=report['error'])))
    return int(not report['complete'])


if __name__ == '__main__':
    raise SystemExit(main())
