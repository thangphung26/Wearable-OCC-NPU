#!/usr/bin/env python3
"""Diagnostic optical alignment using ONLY the fixed delimiter/preamble.

Reuses a captured buffer; does not recollect or overwrite it. Not held-out
validation: this alignment rule was added after inspecting the first pilot.
Never ranks candidates by payload BER, CRC, or either CNN's output.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import time
import serial
from benchmark_precrc import MODEL_SHA, ROOT, normalize, decisions, preamble_polarity
from preamble_tracker import PreambleTracker, RULE as TRACKER_RULE
from manchester_phase import refine as refine_manchester, RULE as PHASE_RULE


def sample(values, t):
    pos=t/.05-1;k=math.floor(pos)
    if k<0 or k+1>=len(values):raise ValueError('Outside captured buffer')
    f=pos-k;return values[k]*(1-f)+values[k+1]*f


def align(values, nominal):
    # 400-ms LOW delimiter followed by Manchester preamble 1010.
    # Half-bit centers, 12 points; fit offset+signed gain, score correlation^2.
    template=[0]*4+[1,0,0,1,1,0,0,1]
    mean=sum(template)/12;vtemplate=sum((b-mean)**2 for b in template)
    candidates=[]
    for step in range(-50,51):
        t=nominal+step*.005
        try:y=[sample(values,t-.35+i*.1) for i in range(12)]
        except ValueError:continue
        ym=sum(y)/12;variance=sum((v-ym)**2 for v in y)
        cross=sum((v-ym)*(b-mean) for v,b in zip(y,template))
        score=cross*cross/(variance*vtemplate) if variance>1e-9 else 0
        candidates.append((score,-abs(step),-step,t,cross/vtemplate))
    if not candidates:raise ValueError('No delimiter/preamble fully captured')
    score,_,_,t,gain=max(candidates)
    return t,score,gain


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('capture',type=Path)
    p.add_argument('--session',required=True);p.add_argument('--port',default='/dev/ttyUSB0')
    p.add_argument('--alignment',choices=['legacy','tracker-v1','tracker-manchester-v1'],default='legacy',
                   help='Legacy remains unchanged; tracker-v1 is a separate development synchronizer')
    p.add_argument('--validation-capture',action='store_true',
                   help='Fresh capture collected after freezing this alignment rule; not a held-out dataset claim')
    a=p.parse_args()
    if Path(a.session).name!=a.session or a.session in ('.','..'):p.error('Invalid session')
    out=ROOT/'dataset/benchmark'/a.session
    if out.exists():p.error('Session exists')
    original=json.loads((a.capture/'summary.json').read_text())
    if not original['complete']:p.error('Incomplete source capture')
    values=json.loads((a.capture/'samples.json').read_text())
    old=json.loads((a.capture/'frame_decisions.json').read_text())
    out.mkdir(parents=True);rows=[]
    sources=[a.capture/f for f in ['summary.json','samples.json','frame_decisions.json','events.jsonl']]
    sources.append(Path(__file__).resolve())
    use_tracker=a.alignment!='legacy'
    if use_tracker:sources.append(Path(__file__).with_name('preamble_tracker.py'))
    if a.alignment=='tracker-manchester-v1':sources.append(Path(__file__).with_name('manchester_phase.py'))
    kind='fresh_capture_fixed_rule_check' if a.validation_capture else 'development_optical_alignment_replay'
    caveat=('Fresh capture after freezing the pilot alignment rule; same setup, not independent-day or held-out generalization. '
            'Not blind production PDR or performance.' if a.validation_capture else
            'Rule developed after first pilot. Requires fresh capture validation. Not blind production PDR or performance.')
    meta=dict(kind=kind,source_capture=str(a.capture.resolve()),
        source_sha256={str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in sources},
        model_sha256=MODEL_SHA,
        rule='Max delimiter/preamble squared correlation over +/-250 ms, 5-ms step; signed gain; no CRC or payload ranking.',
        caveat=caveat)
    meta['alignment']=a.alignment
    if use_tracker:
        meta['tracker_rule']=TRACKER_RULE
        meta['limitations']=['Initial wrong phase/polarity can persist; no automatic reacquisition.',
            'A fixed ROI/polarity and gradual timestamp offset are assumed.',
            'All weak/fallback windows retained; not blind synchronization or production PDR.']
    if a.alignment=='tracker-manchester-v1':
        meta['phase_rule']=PHASE_RULE
        meta['limitations'].append('Local phase uses unknown-bit Manchester structure across the full frame; not whole-bit reacquisition.')
    (out/'metadata.json').write_text(json.dumps(meta,indent=2)+'\n')
    error=None;tracker=PreambleTracker()
    try:
        with serial.Serial(a.port,115200,timeout=.3,write_timeout=2,exclusive=True) as port:
            def wait(prefix):
                end=time.monotonic()+10
                while time.monotonic()<end:
                    s=port.readline().decode(errors='replace').strip()
                    if '[GOAL2_ERROR]' in s:raise RuntimeError(s)
                    if s.startswith(prefix):return s
                raise TimeoutError(prefix)
            port.write(b'\nGOAL2 INFO\n');identity=wait('[GOAL2_READY]')
            if MODEL_SHA not in identity or 'accuracy=buffered-v1' not in identity:raise RuntimeError('Wrong RX/model')
            for r in old:
                tracking=None
                if use_tracker:
                    tracking=tracker.align(values,r['start_offset_s'])
                    t,score,gain=tracking['start'],tracking['fit'],tracking['gain']
                else:t,score,gain=align(values,r['start_offset_s'])
                window=[sample(values,t+(i+.5)*.05) for i in range(192)]
                phase=None
                if a.alignment=='tracker-manchester-v1':
                    phase,window=refine_manchester(values,t)
                    t=phase['start']
                x=normalize(window)
                port.write(('GOAL2 EVAL '+x.hex()+'\n').encode());s=wait('[GOAL2_EVAL]')
                outputs=dict(part.split('=') for part in s.split()[1:])
                truth=[(int(r['frame_hex'],16)>>b)&1 for b in range(47,-1,-1)]
                row=dict(frame_id=r['frame_id'],frame_hex=r['frame_hex'],input_int8_hex=x.hex(),
                    start_offset_s=t,nominal_start_s=r['start_offset_s'],
                    optical_shift_ms=(t-r['start_offset_s'])*1000,
                    preamble_fit_r2=score,signed_optical_gain=gain)
                if tracking is not None:
                    row['tracker']=tracking
                    if phase is not None:row['manchester_phase']=phase
                    classic=[int(window[j]+window[j+1]>window[j+2]+window[j+3]) for j in range(0,192,4)]
                    row['classic_bits']=''.join(map(str,classic))
                    row['classic_errors_direct']=sum(v!=w for v,w in zip(classic,truth))
                    row['classic_errors_preamble_polarity']=sum(v!=w for v,w in zip(preamble_polarity(classic),truth))
                for backend in ['cpu','npu']:
                    row[backend+'_hex']=outputs[backend]
                    b=decisions(outputs[backend])
                    row[backend+'_errors_direct']=sum(v!=w for v,w in zip(b,truth))
                    row[backend+'_errors_preamble_polarity']=sum(v!=w for v,w in zip(preamble_polarity(b),truth))
                row['backend_bit_disagreements']=sum(v!=w for v,w in zip(decisions(outputs['cpu']),decisions(outputs['npu'])))
                rows.append(row)
    except Exception as exc:error=f'{type(exc).__name__}: {exc}'
    result=dict(error=error,complete=error is None,frames_compared=len(rows),bits_compared=len(rows)*48,
        backend_bit_disagreements=sum(r['backend_bit_disagreements'] for r in rows))
    if rows:
        for backend in (['classic','cpu','npu'] if use_tracker else ['cpu','npu']):
            for policy in ['direct','preamble_polarity']:
                key=backend+'_errors_'+policy;errors=sum(r[key] for r in rows)
                result[backend+'_'+policy]=dict(errors=errors,ber=errors/(len(rows)*48),exact_windows=sum(r[key]==0 for r in rows))
        # Verify the actual on-board CPU output against the independently tested
        # portable reference. This is correctness only, not CPU timing.
        host=subprocess.run(['/tmp/vlc_cpu_reference_host'],input=b''.join(bytes.fromhex(r['input_int8_hex']) for r in rows),
                            stdout=subprocess.PIPE,check=True).stdout
        board=b''.join(bytes.fromhex(r['cpu_hex']) for r in rows)
        if len(host)!=len(board):raise RuntimeError('Host reference length mismatch')
        result['cpu_board_host_unequal_values']=sum(v!=w for v,w in zip(host,board))
        if result['cpu_board_host_unequal_values']:
            error='CPU board/portable reference parity failed'
            result.update(error=error,complete=False)
    (out/'frame_decisions.json').write_text(json.dumps(rows,indent=2)+'\n')
    (out/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    text=['# Optical preamble-aligned pre-CRC BER','',
          meta['rule'] if a.alignment=='legacy' else json.dumps(dict(tracker=TRACKER_RULE,phase=meta.get('phase_rule')),sort_keys=True),'',meta['caveat'],'',
          '| Backend / polarity | Bit errors | Pre-CRC BER | Exact windows |',
          '|---|---:|---:|---:|']
    for backend in (['classic','cpu','npu'] if use_tracker else ['cpu','npu']):
        for policy in ['direct','preamble_polarity']:
            if backend+'_'+policy not in result:continue
            r=result[backend+'_'+policy]
            text.append(f'| {backend.upper()} / {policy} | {r["errors"]}/{result["bits_compared"]} | {r["ber"]*100:.3f}% | {r["exact_windows"]}/{len(rows)} |')
    text+=['','All source frames retained, including CRC failures. No best-payload selection.',
           f'Backend bit disagreements: {result["backend_bit_disagreements"]}.',
           f'Error: {error}','', '## Optical alignment (no payload used)','',
           '| Frame ID | Shift (ms) | Preamble fit R2 | Signed gain (luma) |',
           '|---|---:|---:|---:|']
    for r in rows:text.append(f'| {r["frame_id"]} | {r["optical_shift_ms"]:.1f} | {r["preamble_fit_r2"]:.3f} | {r["signed_optical_gain"]:.2f} |')
    (out/'REPORT.md').write_text('\n'.join(text)+'\n')
    print(json.dumps(result,indent=2));print(out)
    return int(error is not None)


if __name__=='__main__':raise SystemExit(main())
