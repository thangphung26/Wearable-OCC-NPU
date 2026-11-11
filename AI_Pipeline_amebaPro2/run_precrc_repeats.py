#!/usr/bin/env python3
"""Freeze the current protocol and collect same-condition accuracy repeats.

Existing capture/replay algorithms are invoked unchanged. Never overwrite,
retry based on BER, remove bad windows, or silently continue after failure.
"""
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'dataset/benchmark'


def now():
    return datetime.now(timezone(timedelta(hours=9))).isoformat()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path,value):
    path.write_text(json.dumps(value,indent=2)+'\n')


def aggregate(out,protocol):
    expected=len(protocol['replay_sessions'])
    sessions=[]
    for name in protocol['replay_sessions']:
        path=BASE/name
        if not (path/'summary.json').exists():continue
        summary=json.loads((path/'summary.json').read_text())
        if not summary['complete'] or summary['error'] is not None:continue
        assert summary['frames_compared']==100 and summary['bits_compared']==4800
        rows=json.loads((path/'frame_decisions.json').read_text())
        assert len(rows)==100
        assert summary['cpu_board_host_unequal_values']==0
        sessions.append(dict(session=name,summary=summary,
            source_sha256={f:sha(path/f) for f in ['summary.json','frame_decisions.json','metadata.json']}))
    pooled=dict(frames=sum(s['summary']['frames_compared'] for s in sessions),
        bits=sum(s['summary']['bits_compared'] for s in sessions),
        backend_disagreements=sum(s['summary']['backend_bit_disagreements'] for s in sessions))
    backends=['cpu','npu']
    if sessions and all('classic_preamble_polarity' in s['summary'] for s in sessions):backends+=['classic']
    for backend in backends:
        errors=sum(s['summary'][backend+'_preamble_polarity']['errors'] for s in sessions)
        pooled[backend]=dict(errors=errors,ber=errors/pooled['bits'] if pooled['bits'] else None,
            exact_windows=sum(s['summary'][backend+'_preamble_polarity']['exact_windows'] for s in sessions))
    save(out/'aggregate.json',dict(complete=len(sessions)==expected,completed_sessions=len(sessions),
        expected_sessions=expected,
        sessions=sessions,pooled=pooled,
        scope='Listed-session timing-assisted replay, not production PDR; conditions and dates recorded in source metadata.'))
    lines=[f'# Goal 2: {protocol["distance_m"]:g}-m accuracy repeatability','',
        f'Completed sessions: {len(sessions)}/{expected}.',
        '', '| Session | Frames | CPU errors / bits | CPU BER (%) | NPU errors / bits | NPU BER (%) | CPU / NPU exact windows |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for s in sessions:
        r=s['summary'];c=r['cpu_preamble_polarity'];n=r['npu_preamble_polarity']
        lines.append(f'| {s["session"]} | {r["frames_compared"]} | {c["errors"]}/{r["bits_compared"]} | '
            f'{c["ber"]*100:.4f} | {n["errors"]}/{r["bits_compared"]} | {n["ber"]*100:.4f} | '
            f'{c["exact_windows"]} / {n["exact_windows"]} |')
    if pooled['bits']:
        lines += ['',f'Pooled CPU: {pooled["cpu"]["errors"]}/{pooled["bits"]} = {pooled["cpu"]["ber"]*100:.4f}%.',
            f'Pooled NPU: {pooled["npu"]["errors"]}/{pooled["bits"]} = {pooled["npu"]["ber"]*100:.4f}%.',
            f'CPU/NPU bit disagreements: {pooled["backend_disagreements"]}/{pooled["bits"]}.']
    if 'classic' in pooled:
        lines+=['','| Session | Classic errors / bits | Classic BER (%) | Classic exact windows |',
                '|---|---:|---:|---:|']
        for s in sessions:
            r=s['summary'];c=r['classic_preamble_polarity']
            lines.append(f'| {s["session"]} | {c["errors"]}/{r["bits_compared"]} | {c["ber"]*100:.4f} | {c["exact_windows"]} |')
        lines.append(f'Pooled Classic: {pooled["classic"]["errors"]}/{pooled["bits"]} = {pooled["classic"]["ber"]*100:.4f}%.')
    lines += ['', '## Fixed scope','',
        f'- New captures: {protocol["distance_m"]:g} m, '+protocol['lighting']+', ROI '+str(protocol['roi'])+'.',
        '- Same firmware/model/preprocessing/alignment, no BER-dependent retries or frame exclusions.',
        '- Replay alignment: '+protocol.get('replay_alignment','legacy')+'; no payload/CRC/model-output ranking.',
        '- Phase selection runs on the host; CNN inference is on board. Not a complete production edge receiver test.',
        '- Each pair executes on the real AmebaPro2 CPU reference and NPU with identical input bytes.',
        '- Same-setup repeats do not establish independent-day or cross-distance generalization.',
        '- These accuracy results do not replace production PDR/FPS or energy measurements.',
        '- Failed captures, if any, remain on disk; batch halts instead of substituting another trial.','']
    (out/'REPORT.md').write_text('\n'.join(lines))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--batch',default='goal2_precrc_repeatability_1m_lights_on_s01')
    p.add_argument('--prepare-only',action='store_true')
    p.add_argument('--run-lock',type=Path)
    p.add_argument('--roi-px',default='92,90,67,60')
    p.add_argument('--capture-prefix',default='goal2_precrc_1m_lights_on_100f')
    p.add_argument('--replay-prefix',default='goal2_precrc_optical_100f')
    p.add_argument('--first-session',type=int,default=2)
    p.add_argument('--repeats',type=int,default=2)
    p.add_argument('--distance-m',type=float,default=1)
    p.add_argument('--tx-port',default='/dev/ttyACM0')
    p.add_argument('--tx-ssh')
    p.add_argument('--lighting',default='room_lights_on')
    p.add_argument('--alignment',choices=['legacy','tracker-v1','tracker-manchester-v1'],default='legacy')
    p.add_argument('--require-preprocess',choices=['jpeg-gray8-v2'])
    p.add_argument('--baseline-session',default='goal2_precrc_optical_100f_s01',
                   help='Existing replay to include, or none to keep a new ROI/day batch separate')
    p.add_argument('--firmware-bin',default='dataset/diagnostics/goal2_cpu_npu/buffered_v1_build/flash_ntz.bin')
    a=p.parse_args()
    if a.run_lock:
        lock=a.run_lock.resolve();out=lock.parent;protocol=json.loads(lock.read_text())
        state=json.loads((out/'status.json').read_text())
        if state['state']!='prepared':p.error('Batch already started; do not resume or overwrite automatically')
    else:
        if Path(a.batch).name!=a.batch or a.batch in ('.','..'):p.error('Invalid batch name')
        for prefix in [a.capture_prefix,a.replay_prefix]:
            if Path(prefix).name!=prefix or prefix in ('.','..'):p.error('Invalid session prefix')
        if a.first_session<1:p.error('first-session must be positive')
        if not 1<=a.repeats<=5:p.error('repeats must be 1..5')
        import math
        if not math.isfinite(a.distance_m) or a.distance_m<=0:p.error('distance-m must be positive')
        try:
            roi=list(map(int,a.roi_px.split(',')))
            assert len(roi)==4 and min(roi[:2])>=0 and min(roi[2:])>0
            assert roi[0]+roi[2]<=320 and roi[1]+roi[3]<=192
        except (ValueError,AssertionError):p.error('ROI must fit 320x192')
        baseline=[] if a.baseline_session=='none' else [a.baseline_session]
        for name in baseline:
            if Path(name).name!=name or not (BASE/name/'summary.json').is_file():p.error('Invalid/missing baseline session')
        out=BASE/a.batch
        captures=[f'{a.capture_prefix}_s{i:02d}' for i in range(a.first_session,a.first_session+a.repeats)]
        replays=[f'{a.replay_prefix}_s{i:02d}' for i in range(a.first_session,a.first_session+a.repeats)]
        if out.exists() or any((BASE/n).exists() for n in captures+replays):
            p.error('Batch or planned session already exists; no overwrite')
        files=['AI_Pipeline_amebaPro2/'+n for n in
            ['benchmark_precrc.py','replay_precrc_buffer.py','run_precrc_repeats.py',
             'preamble_tracker.py','manchester_phase.py']]
        if a.tx_ssh:files+=['AI_Pipeline_amebaPro2/ssh_serial.py']
        files+=['RX/Ameba_CPU_NPU_Benchmark/'+n for n in
            ['Ameba_CPU_NPU_Benchmark.ino','accuracy_bench.h','pipeline_bench.h','pipeline_schedule.h']]
        files+=['TX/VLC_OOK_TX_realtime_sensors/VLC_OOK_TX_realtime_sensors.ino']
        files+=['libraries/VLCInt8Reference/src/'+n for n in
            ['vlc_cpu_model.h','vlc_cpu_nbg.h']]
        files+=['AI_Pipeline_amebaPro2/ameba_deployment/'+n for n in
            ['vlc_edge_cnn_ch4_int8.tflite','vlc_edge_cnn_ch4.nb']]
        binary=(ROOT/a.firmware_bin).resolve()
        try:binary_rel=str(binary.relative_to(ROOT))
        except ValueError:p.error('Firmware archive must be inside the project')
        files+=[binary_rel]
        hashes={f:sha(ROOT/f) for f in files}
        out.mkdir(parents=True);snapshot=out/'locked_sources';snapshot.mkdir()
        for f in files:
            dest=snapshot/f;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/f,dest)
        shutil.copyfile('/tmp/vlc_cpu_reference_host',out/'cpu_reference_host')
        protocol=dict(created_at=now(),capture_sessions=captures,
            replay_sessions=baseline+replays,new_replay_sessions=replays,
            roi=roi,distance_m=a.distance_m,lighting=a.lighting,duration_s=1010,
            frames_per_session=100,sample_hz=20,camera_requested_fps=30,
            rx_port='/dev/ttyUSB0',tx_port=a.tx_port,tx_ssh=a.tx_ssh,
            replay_alignment=a.alignment,require_preprocess=a.require_preprocess,
            cpu_reference_executable_sha256=sha(out/'cpu_reference_host'),source_sha256=hashes,
            firmware_identity='paired-int8-pipeline-v2 accuracy=buffered-v1',
            firmware_hash_scope='Expected archived binary; runtime identity/model checked, not flash read-back.',
            alignment=dict(search_radius_ms=250,step_ms=5,
                criterion='delimiter/preamble squared correlation only',polarity='decoded preamble, normal on tie',
                reject_bad_crc=False,drop_low_fit_frames=False),
            note='Same-condition accuracy repeats; no change to production Goal 1 data. Transport and distance recorded explicitly.')
        if a.alignment!='legacy':
            from preamble_tracker import RULE
            protocol['alignment']['tracker_rule']=RULE
        if a.alignment=='tracker-manchester-v1':
            from manchester_phase import RULE
            protocol['alignment']['phase_rule']=RULE
            protocol['alignment']['criterion']='delimiter/preamble tracker, then unknown-bit Manchester consistency'
        save(out/'protocol_lock.json',protocol)
        save(out/'status.json',dict(state='prepared',updated_at=now(),completed_new_sessions=0))
        aggregate(out,protocol)
        print('[LOCKED]',out/'protocol_lock.json',flush=True)
    if a.prepare_only:return 0
    state=dict(state='running',updated_at=now(),completed_new_sessions=0,current_stage='preflight')
    error=None
    def verify():
        for f,expected in protocol['source_sha256'].items():
            if sha(ROOT/f)!=expected:raise RuntimeError('Locked source changed: '+f)
        if sha(Path('/tmp/vlc_cpu_reference_host'))!=protocol['cpu_reference_executable_sha256']:
            raise RuntimeError('Portable CPU reference binary changed/missing')
    def run(command,stage,index):
        verify();state.update(current_stage=stage,session_number=index,updated_at=now())
        save(out/'status.json',state);print('[STAGE]',stage,flush=True)
        with (out/(stage+'.log')).open('x',buffering=1) as log:
            subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True)
    try:
        verify();save(out/'status.json',state)
        new_replays=protocol.get('new_replay_sessions',protocol['replay_sessions'][1:])
        for index,(capture,replay) in enumerate(zip(protocol['capture_sessions'],new_replays),1):
            command=[sys.executable,'-u','AI_Pipeline_amebaPro2/benchmark_precrc.py','--session',capture,
                '--duration',str(protocol['duration_s']),'--roi-px',','.join(map(str,protocol['roi'])),
                '--lighting',protocol['lighting'],'--distance-m',str(protocol['distance_m']),
                '--tx-port',protocol['tx_port'],'--rx-port',protocol['rx_port']]
            if protocol.get('tx_ssh'):command+=['--tx-ssh',protocol['tx_ssh']]
            if protocol.get('require_preprocess'):command+=['--require-preprocess',protocol['require_preprocess']]
            run(command,capture,index)
            cap=json.loads((BASE/capture/'summary.json').read_text())
            if not cap['complete'] or cap['error'] is not None or cap['results']['frames_compared']!=100:
                raise RuntimeError('Capture incomplete or frame count differs from 100; preserving data')
            run([sys.executable,'-u','AI_Pipeline_amebaPro2/replay_precrc_buffer.py',str(BASE/capture),
                '--session',replay,'--alignment',protocol.get('replay_alignment','legacy'),
                '--validation-capture'],replay,index)
            result=json.loads((BASE/replay/'summary.json').read_text())
            if not result['complete'] or result['error'] is not None or result['frames_compared']!=100:
                raise RuntimeError('Replay incomplete; preserving data')
            state['completed_new_sessions']=index;save(out/'status.json',state);aggregate(out,protocol)
            print('[SCORED]',replay,'CPU',result['cpu_preamble_polarity'],'NPU',result['npu_preamble_polarity'],flush=True)
        state['state']='complete';state['current_stage']='complete'
    except (Exception,KeyboardInterrupt) as exc:
        error=f'{type(exc).__name__}: {exc}';state['state']='failed';state['error']=error
        print('[ERROR]',error,flush=True)
    finally:
        state['updated_at']=now();save(out/'status.json',state);aggregate(out,protocol)
    print('[BATCH]',out,state['state'],flush=True)
    return int(error is not None)


if __name__=='__main__':raise SystemExit(main())
