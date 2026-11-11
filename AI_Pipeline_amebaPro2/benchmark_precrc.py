#!/usr/bin/env python3
"""Buffered optical capture, then SAME-BOARD CPU/NPU pre-CRC evaluation.

Timing-assisted diagnostic, not a blind synchronization/PDR experiment.
No CRC gate or ground-truth-based phase/polarity optimization is used.
Only stdlib + pyserial; original sessions are never overwritten.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import queue
import re
import threading
import time
import serial

ROOT = Path(__file__).resolve().parents[1]
MODEL_SHA = 'ce60f7f60045592f3618d717001c2962538d601175f952994802e5b9c04c8175'


def normalize(values):
    out = []
    for i in range(0, 192, 4):
        v = values[i:i+4]; lo, hi = min(v), max(v)
        out.extend(max(-128, min(127, int((x-lo)/(hi-lo)*255+.5)-128))
                   if hi-lo > .5 else -128 for x in v)
    return bytes(x & 255 for x in out)


def decisions(raw):
    return [int(x < 128) for x in bytes.fromhex(raw)]  # signed INT8 >= 0


def preamble_polarity(bits):
    # Observable preamble only. Fixed tie-break; never inspect TX payload.
    errors = sum(a != b for a, b in zip(bits[:4], [1, 0, 1, 0]))
    return [1-b for b in bits] if errors > 2 else bits


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--session', required=True)
    p.add_argument('--rx-port', default='/dev/ttyUSB0')
    p.add_argument('--tx-port', default='/dev/ttyACM0')
    p.add_argument('--tx-ssh', help='SSH target for a Windows TX serial port (Python + pyserial required)')
    p.add_argument('--distance-m', type=float, default=1)
    p.add_argument('--duration', type=int, default=120)
    p.add_argument('--roi-px', default='92,90,67,60')
    p.add_argument('--lighting', required=True)
    p.add_argument('--require-preprocess',choices=['jpeg-gray8-v2'],
                   help='Fail closed if RX runtime lacks the requested preprocessing identity')
    a = p.parse_args()
    if not math.isfinite(a.distance_m) or a.distance_m<=0:p.error('distance-m must be positive')
    if not 10 <= a.duration <= 1200 or Path(a.session).name != a.session or a.session in ('.','..'):
        p.error('duration 10..1200 and a plain session name required')
    try:
        roi = list(map(int, a.roi_px.split(',')))
        assert len(roi)==4 and min(roi[:2])>=0 and min(roi[2:])>0
        assert roi[0]+roi[2]<=320 and roi[1]+roi[3]<=192
    except (ValueError, AssertionError): p.error('invalid ROI')
    out = ROOT/'dataset/benchmark'/a.session
    if out.exists(): p.error('session exists; refusing overwrite')
    out.mkdir(parents=True)
    sources = ['RX/Ameba_CPU_NPU_Benchmark/'+s for s in
               ['Ameba_CPU_NPU_Benchmark.ino','accuracy_bench.h','pipeline_bench.h']]
    sources += ['AI_Pipeline_amebaPro2/benchmark_precrc.py']
    if a.tx_ssh:sources += ['AI_Pipeline_amebaPro2/ssh_serial.py']
    meta = vars(a) | dict(roi=roi, distance_m=a.distance_m, sample_hz=20,
        model_sha256=MODEL_SHA, kind='timing_assisted_precrc_same_board',
        source_sha256={s:hashlib.sha256((ROOT/s).read_bytes()).hexdigest() for s in sources},
        limitations=['Not production FPS or blind packet PDR.',
            'Host serial timing (including SSH transport if selected) is approximate; no payload-based phase search.',
            'No CRC, physiological-range, or exact-payload acceptance gate.',
            'Polarity uses decoded preamble only, normal on tie.',
            'CPU uses reference kernels, not optimized CPU inference.'])
    (out/'metadata.json').write_text(json.dumps(meta,indent=2)+'\n')
    events=queue.Queue(); stop=threading.Event(); threads=[]; ports={}; tx=[]; samples=[]; scored=[]
    error=None; report={}; capture_stats=None; tx_verified=False
    with (out/'events.jsonl').open('w', buffering=1) as log:
        def reader(name, port):
            try:
                while not stop.is_set():
                    line=port.readline().decode(errors='replace').strip()
                    if line:events.put(dict(source=name,mono_ns=time.monotonic_ns(),line=line))
            except Exception as exc:events.put(dict(source=name,mono_ns=time.monotonic_ns(),line='[READ_ERROR] '+str(exc)))
        def receive(timeout=.3):
            try:e=events.get(timeout=timeout)
            except queue.Empty:return None
            log.write(json.dumps(e)+'\n')
            line=e['line']
            if '[GOAL2_ERROR]' in line or '[READ_ERROR]' in line:raise RuntimeError(line)
            if e['source']=='tx' and line.startswith('[TX_GT]'):
                fields=dict(re.findall(r'(\w+)=([^ ]+)',line))
                tx.append(fields|dict(mono_ns=e['mono_ns'],line_bytes=len(line)+2))
            return e
        def wait(source, prefix, timeout):
            end=time.monotonic()+timeout
            while time.monotonic()<end:
                e=receive()
                if e and e['source']==source and e['line'].startswith(prefix):return e
            raise TimeoutError('Missing '+prefix)
        def send(name, cmd):ports[name].write((cmd+'\n').encode());ports[name].flush()
        try:
            for name, device in [('rx',a.rx_port),('tx',a.tx_port)]:
                if name=='tx' and a.tx_ssh:
                    from ssh_serial import SSHSerial
                    ports[name]=SSHSerial(a.tx_ssh,device,115200,timeout=.15,write_timeout=2)
                else:
                    ports[name]=serial.Serial(device,115200,timeout=.15,write_timeout=2,exclusive=True)
                t=threading.Thread(target=reader,args=(name,ports[name]),daemon=True);t.start();threads.append(t)
            send('rx','\nGOAL2 INFO')
            identity=wait('rx','[GOAL2_READY]',10)['line']
            if 'accuracy=buffered-v1' not in identity or MODEL_SHA not in identity:raise RuntimeError('Wrong RX firmware/model')
            if a.require_preprocess and 'preprocess='+a.require_preprocess not in identity:
                raise RuntimeError('Wrong RX preprocessing firmware')
            if a.require_preprocess=='jpeg-gray8-v2' and 'exposure_us=2000 gain=256' not in identity:
                raise RuntimeError('Wrong exposure/gain build identity')
            meta['runtime_identity']=identity
            (out/'metadata.json').write_text(json.dumps(meta,indent=2)+'\n')
            wait('tx','[TX_GT]',15)  # Verify the connected TX BEFORE sending commands.
            tx_verified=True
            send('tx','PAYLOAD TEST ON')
            wait('tx','[PAYLOAD_TEST] ON',5)
            wait('tx','[TX_GT]',15)
            if tx[-1].get('payload_mode')!='test':raise RuntimeError('Synthetic payload mode not active')
            send('rx','GOAL2 ROI '+' '.join(map(str,roi)))
            if wait('rx','[GOAL2_ROI]',5)['line']!='[GOAL2_ROI] '+','.join(map(str,roi)):raise RuntimeError('ROI mismatch')
            sent_ns=time.monotonic_ns();send('rx',f'GOAL2 CAPTURE {a.duration}')
            begin=wait('rx','[GOAL2_CAPTURE_BEGIN]',5)
            # Firmware starts just after this BEGIN line has drained from UART.
            origin=begin['mono_ns']/1e9
            print(f'[CAPTURE] {a.duration}s; no raw UART during acquisition',flush=True)
            end=time.monotonic()+a.duration+150
            while time.monotonic()<end:
                e=receive()
                if not e or e['source']!='rx':continue
                line=e['line']
                if line.startswith('[GOAL2_SAMPLE]'):
                    m=re.fullmatch(r'\[GOAL2_SAMPLE\] i=(\d+) value=([\d.]+)',line)
                    if not m or int(m[1])!=len(samples):raise RuntimeError('Sample sequence gap')
                    samples.append(float(m[2]))
                elif line.startswith('[GOAL2_CAPTURE_STATS]'):capture_stats=line
                elif line.startswith('[GOAL2_CAPTURE_COMPLETE]'):break
            if len(samples)!=a.duration*20 or not capture_stats:raise RuntimeError('Incomplete capture')
            send('tx','PAYLOAD TEST OFF');wait('tx','[PAYLOAD_TEST] OFF',5)
            (out/'samples.json').write_text(json.dumps(samples)+'\n')
            # TX unwrapping works across micros() wrap. Use only synthetic test
            # records, minimum transport offset; no alignment fitted to decoded bits.
            truths=[r for r in tx if r.get('payload_mode')=='test']
            wrap=0;prev=None
            for r in truths:
                us=int(r['tx_us'])
                if prev is not None and us<prev:wrap+=2**32
                prev=us;r['unwrapped_us']=us+wrap
            offsets=[r['mono_ns']/1e9-r['unwrapped_us']/1e6-r['line_bytes']*10/115200 for r in truths]
            clock_offset=min(offsets)
            # All frames entirely inside the buffer, regardless of CRC outcome.
            for r in truths:
                start=r['unwrapped_us']/1e6+clock_offset-origin
                positions=[(start+(i+.5)*.05)/.05-1 for i in range(192)]
                if min(positions)<0 or max(positions)>=len(samples)-1:continue
                values=[]
                for pos in positions:
                    k=math.floor(pos);f=pos-k;values.append(samples[k]*(1-f)+samples[k+1]*f)
                x=normalize(values);send('rx','GOAL2 EVAL '+x.hex())
                result=wait('rx','[GOAL2_EVAL]',5)['line']
                m=re.fullmatch(r'\[GOAL2_EVAL\] cpu=([0-9A-F]{96}) npu=([0-9A-F]{96})',result)
                if not m:raise RuntimeError('Malformed backend outputs')
                truth=[(int(r['frame_hex'],16)>>b)&1 for b in range(47,-1,-1)]
                row=dict(frame_id=int(r['frame_id']),frame_hex=r['frame_hex'],input_int8_hex=x.hex(),
                         start_offset_s=start,cpu_hex=m[1],npu_hex=m[2])
                cpu,npu=decisions(m[1]),decisions(m[2])
                row['backend_bit_disagreements']=sum(c!=n for c,n in zip(cpu,npu))
                for name,bits in [('cpu',cpu),('npu',npu)]:
                    row[name+'_errors_direct']=sum(c!=t for c,t in zip(bits,truth))
                    row[name+'_errors_preamble_polarity']=sum(c!=t for c,t in zip(preamble_polarity(bits),truth))
                scored.append(row)
            if not scored:raise RuntimeError('No fully captured TX frames')
            report=dict(frames_compared=len(scored),bits_compared=len(scored)*48,
                capture_stats=capture_stats,rx_begin_command_to_ack_ms=(begin['mono_ns']-sent_ns)/1e6,
                tx_serial_offset_spread_ms=(max(offsets)-min(offsets))*1000,
                sample_range=max(samples)-min(samples),model_sha256=MODEL_SHA,
                backend_bit_disagreements=sum(r['backend_bit_disagreements'] for r in scored))
            for name in ['cpu','npu']:
                for policy in ['direct','preamble_polarity']:
                    key=name+'_errors_'+policy;err=sum(r[key] for r in scored)
                    report[name+'_'+policy]=dict(errors=err,ber=err/report['bits_compared'],
                        exact_windows=sum(r[key]==0 for r in scored))
            print(json.dumps(report,indent=2),flush=True)
        except (Exception,KeyboardInterrupt) as exc:error=f'{type(exc).__name__}: {exc}';print('[ERROR]',error,flush=True)
        finally:
            if tx_verified:
                try:send('tx','PAYLOAD TEST OFF')
                except Exception:pass
            stop.set()
            for t in threads:t.join(1)
            for port in ports.values():port.close()
    (out/'frame_decisions.json').write_text(json.dumps(scored,indent=2)+'\n')
    (out/'tx_ground_truth.json').write_text(json.dumps(tx,indent=2)+'\n')
    (out/'summary.json').write_text(json.dumps(dict(error=error,complete=error is None,results=report),indent=2)+'\n')
    lines=['# Same-board CPU / NPU pre-CRC diagnostic','',
        f'Status: {"complete" if error is None else error}',
        '', 'Timing-assisted windows from buffered live ROI; not production packet PDR.',
        'Both backends execute on AmebaPro2 using identical INT8 input bytes.',
        'Every fully captured test frame is scored, including CRC failures.', '',
        '| Backend / polarity | Bit errors / compared bits | Pre-CRC BER | Exact windows |',
        '|---|---:|---:|---:|']
    if report:
        for name in ['cpu','npu']:
            for policy in ['direct','preamble_polarity']:
                r=report[name+'_'+policy]
                lines.append(f'| {name.upper()} / {policy} | {r["errors"]}/{report["bits_compared"]} | '
                             f'{100*r["ber"]:.3f}% | {r["exact_windows"]}/{report["frames_compared"]} |')
        lines += ['',f'Backend bit disagreements: {report["backend_bit_disagreements"]}/{report["bits_compared"]}.',
            f'Captured ROI range: {report["sample_range"]:.3f} luma units.',
            f'RX command-to-ack: {report["rx_begin_command_to_ack_ms"]:.3f} ms.',
            f'TX serial-offset spread: {report["tx_serial_offset_spread_ms"]:.3f} ms.',
            '',capture_stats]
    lines += ['', '## Limitations', '']+['- '+s for s in meta['limitations']]
    lines += ['- Serial timing uncertainty is not a calibrated optical synchronization error bound.',
              '- No timing or polarity is chosen by minimizing errors against TX payload.',
              '- CPU/NPU backend disagreement is not BER against TX ground truth.',
              '- Capture excludes CNN workload; do not use its FPS for the production pipeline.',
              '- Pilot data are diagnostic; repeatability and synchronization must be checked before paper claims.','']
    (out/'REPORT.md').write_text('\n'.join(lines))
    print('[COMPLETE]',out,flush=True)
    return int(error is not None)


if __name__=='__main__':raise SystemExit(main())
