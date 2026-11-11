#!/usr/bin/env python3
"""Real camera/JPEG/ROI workload plus identical 20-Hz CNN input-bank replay.

Not the production decoder: no preamble/CRC/classic search or live channel PDR.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import time
import serial

ROOT=Path(__file__).resolve().parents[1]


def variant_sources(variant):
    """Return the source manifest and provenance path for the selected build."""
    if variant not in ('original','aug-v2'):
        raise ValueError('Unknown benchmark variant: '+variant)
    sketch='Ameba_CPU_NPU_Benchmark'+('_AUG_V2' if variant=='aug-v2' else '')
    directory='RX/'+sketch+'/'
    files=[directory+n for n in [sketch+'.ino','pipeline_bench.h','pipeline_schedule.h']]
    generated=directory if variant=='aug-v2' else 'libraries/VLCInt8Reference/src/'
    files += [generated+n for n in ['vlc_cpu_model.h','vlc_cpu_bank.h','vlc_cpu_nbg.h']]
    provenance=(directory if variant=='aug-v2' else 'libraries/VLCInt8Reference/')+'provenance.json'
    return files,provenance


def validate_identity(identity,provenance):
    if 'build=paired-int8-pipeline-v2' not in identity or provenance['model_sha256'] not in identity:
        raise RuntimeError('Pipeline build/model identity mismatch; no test sent')


def runtime_configuration(identity):
    """Require the current pixel format and read the board's optical preset."""
    fields=dict(re.findall(r'(\w+)=([^ ]+)',identity))
    if fields.get('preprocess')!='jpeg-gray8-v2':
        raise RuntimeError('Corrected jpeg-gray8-v2 firmware required; no workload sent')
    try:
        exposure=int(fields['exposure_us']);gain=int(fields['gain'])
    except (KeyError,ValueError) as exc:
        raise RuntimeError('Firmware must report exposure_us and gain') from exc
    if exposure<=0 or gain<=0:
        raise RuntimeError('Invalid firmware optical preset')
    return dict(runtime_identity=identity,preprocess=fields['preprocess'],
                exposure_us=exposure,gain=gain)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port',default='/dev/ttyUSB0');p.add_argument('--session',required=True)
    p.add_argument('--variant',choices=['original','aug-v2'],default='original')
    p.add_argument('--duration',type=int,default=60);p.add_argument('--repeats',type=int,default=2)
    p.add_argument('--roi-px',default='92,90,67,60');p.add_argument('--lighting',default='unspecified')
    p.add_argument('--expected-exposure-us',type=int,default=2000)
    p.add_argument('--expected-gain',type=int,default=256)
    a=p.parse_args()
    if not 10<=a.duration<=120 or not 1<=a.repeats<=5:p.error('duration 10..120, repeats 1..5')
    if a.session!=Path(a.session).name or a.session in ('.','..'):p.error('Invalid session name')
    try:
        roi=[int(v) for v in a.roi_px.split(',')]
        assert len(roi)==4 and min(roi[:2])>=0 and min(roi[2:])>0
        assert roi[0]+roi[2]<=320 and roi[1]+roi[3]<=192
    except (ValueError,AssertionError):p.error('ROI must fit 320x192')
    out=ROOT/'dataset/benchmark'/a.session
    if out.exists():p.error('Session exists; never overwrite data')
    out.mkdir(parents=True)
    files,provenance_path=variant_sources(a.variant)
    provenance=json.loads((ROOT/provenance_path).read_text())
    meta=vars(a)|dict(roi=roi,kind='camera_preprocessing_fixed_tensor_replay',
        camera_requested_fps=30,sample_hz=20,warmup_s=10,
        exposure_us=None,gain=None,raw_logging=False,
        source_sha256={f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in files},
        provenance=provenance,build_flags='-O3 -DTF_LITE_STATIC_MEMORY -DNDEBUG',
        note='Actual JPEG decode, 20x12 grid activity, fixed manual ROI, interpolation and '
        'per-bit normalization. CNN uses the same fixed input-bank sequence, NOT the live ROI tensor. '
        'No jobs dropped. Relative job deadline is 50 ms from release; misses are scheduler metrics, '
        'not patient alert deadlines. Wall-time fractions are NOT CPU utilization. '
        'No packet/PDR/BER or classic/CRC scheduling. CPU is reference, not CMSIS-NN optimized.')
    (out/'metadata.json').write_text(json.dumps(meta,indent=2)+'\n')
    reports=[];error=None
    with (out/'events.jsonl').open('w',buffering=1) as log:
        try:
            with serial.Serial(a.port,115200,timeout=.3,write_timeout=2,exclusive=True) as port:
                def receive():
                    line=port.readline().decode(errors='replace').strip()
                    if line:log.write(json.dumps(dict(host_time_ns=time.time_ns(),line=line))+'\n')
                    if '[GOAL2_ERROR]' in line:raise RuntimeError(line)
                    return line
                def wait(prefix,timeout):
                    end=time.monotonic()+timeout
                    while time.monotonic()<end:
                        line=receive()
                        if line.startswith(prefix):return line
                    raise TimeoutError('Missing '+prefix)
                port.write(b'\nGOAL2 INFO\n');port.flush()
                identity=wait('[GOAL2_READY]',12)
                validate_identity(identity,provenance)
                meta.update(runtime_configuration(identity))
                (out/'metadata.json').write_text(json.dumps(meta,indent=2)+'\n')
                if (meta['exposure_us'],meta['gain'])!=(a.expected_exposure_us,a.expected_gain):
                    raise RuntimeError('Runtime exposure/gain differs from requested test preset; no workload sent')
                port.write(('GOAL2 ROI '+' '.join(map(str,roi))+'\n').encode());port.flush()
                if wait('[GOAL2_ROI]',8)!='[GOAL2_ROI] '+','.join(map(str,roi)):
                    raise RuntimeError('ROI acknowledgment mismatch')
                hashes=set()
                for repeat in range(1,a.repeats+1):
                    modes=['camera','cpu','npu']
                    if repeat%2==0:modes.reverse()
                    for mode in modes:
                        print(f'[MEASURE] repeat={repeat} backend={mode} {a.duration}s + 10s warmup',flush=True)
                        port.write(f'GOAL2 PIPE {mode.upper()} {a.duration}\n'.encode());port.flush()
                        began=wait('[GOAL2_PIPE_BEGIN]',20)
                        if f'backend={mode} seconds={a.duration}' not in began or 'cpu_parity=PASS' not in began:
                            raise RuntimeError('Begin/parity mismatch')
                        line=wait('[GOAL2_PIPE_REPORT]',a.duration+40)
                        r=json.loads(line.split('] ',1)[1]);r['repeat']=repeat
                        assert r['build']=='paired-int8-pipeline-v2' and r['backend']==mode
                        assert r['roi']==roi and r['raw_logging'] is False and r['model_input']=='fixed_bank_replay'
                        assert r['jobs']==r['expected_jobs']==a.duration*20
                        assert r['frames']>2 and r['missing_roi']==0
                        assert r['inference_full']['n']==(0 if mode=='camera' else r['jobs'])
                        assert r['cpu_reference_unequal_values']==0
                        hashes.add(r['sequence_hash']);assert len(hashes)==1,'Different bank sample sequence'
                        r['job_rate_hz']=r['jobs']*1e6/r['elapsed_us']
                        r['inference_wall_fraction']=r['inference_full']['n']*r['inference_full']['mean_us']/r['elapsed_us']
                        reports.append(r)
                        (out/'reports.json').write_text(json.dumps(reports,indent=2)+'\n')
                        print(f'[RESULT] {mode}: {r["decoded_jpeg_fps"]:.3f} FPS, '
                              f'inference {r["inference_full"]["mean_us"]/1000:.3f} ms, '
                              f'deadline misses {r["deadline_misses_50ms"]}/{r["jobs"]}',flush=True)
        except (Exception,KeyboardInterrupt) as exc:
            error=f'{type(exc).__name__}: {exc}';print('[ERROR]',error,flush=True)
    result=dict(error=error,complete=error is None and len(reports)==a.repeats*3,
        completed_legs=len(reports),expected_legs=a.repeats*3,
        note=meta['note'])
    (out/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print('[COMPLETE]',out,flush=True);return int(error is not None)


if __name__=='__main__':raise SystemExit(main())
