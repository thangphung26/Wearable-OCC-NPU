#!/usr/bin/env python3
"""Save unchanged JPEGs after logged TX LED transitions; no model or ROI edits.

LED command self-terminates after 20 s. Snapshot labels are timing-assisted
over SSH, not hardware-triggered exposure timestamps. Preserve timing evidence.
"""
import json
from pathlib import Path
import queue
import re
import threading
import time
import serial
import cv2
import numpy as np
from ssh_serial import SSHSerial

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'dataset/benchmark/controlled_led_snapshots_gray8v2_5m_20261025_s01'


def main():
    if OUT.exists():raise FileExistsError(OUT)
    OUT.mkdir();q=queue.Queue();stop=threading.Event();tx=None;events=[];shots=[];error=None
    with (OUT/'events.jsonl').open('w',buffering=1) as log:
        def log_event(source,line):
            e=dict(source=source,mono_ns=time.monotonic_ns(),line=line)
            events.append(e);log.write(json.dumps(e)+'\n');return e
        def tx_reader():
            try:
                while not stop.is_set():
                    line=tx.readline().decode(errors='replace').strip()
                    if line:q.put(dict(line=line,mono_ns=time.monotonic_ns()))
            except Exception as e:q.put(dict(line='[READ_ERROR] '+str(e),mono_ns=time.monotonic_ns()))
        def receive_tx(timeout=.3):
            try:e=q.get(timeout=timeout)
            except queue.Empty:return None
            e['source']='tx';events.append(e);log.write(json.dumps(e)+'\n')
            if '[READ_ERROR]' in e['line']:raise RuntimeError(e['line'])
            return e
        def wait_tx(prefix,timeout):
            end=time.monotonic()+timeout
            while time.monotonic()<end:
                e=receive_tx()
                if e and e['line'].startswith(prefix):return e
            raise TimeoutError(prefix)
        try:
            with serial.Serial('/dev/ttyUSB0',115200,timeout=.15,write_timeout=2,exclusive=True) as rx:
                rx.write(b'\nGOAL2 INFO\n');rx.flush();end=time.monotonic()+6
                while time.monotonic()<end:
                    line=rx.readline().decode(errors='replace').strip()
                    if line:log_event('rx',line)
                    if line.startswith('[GOAL2_READY]'):
                        if 'preprocess=jpeg-gray8-v2 exposure_us=2000 gain=256' not in line:raise RuntimeError('Wrong RX')
                        break
                else:raise TimeoutError('RX identity')
                tx=SSHSerial('thang@100.116.106.109','COM3',115200)
                threading.Thread(target=tx_reader,daemon=True).start()
                wait_tx('[TX_GT]',15) # Verify TX before issuing any command.
                tx.write(b'LED CONTRAST 1000\n');tx.flush()
                wait_tx('[LED_CONTRAST] BEGIN',5)
                for index in range(6):
                    # Use a newly received transition, not a stale queued event.
                    while True:
                        level=wait_tx('[LED_LEVEL]',4)
                        if time.monotonic()-level['mono_ns']/1e9<.15:break
                    on=int(re.search(r'on=([01])',level['line'])[1])
                    time.sleep(.08)
                    sent=time.monotonic_ns();rx.write(b'SNAP\n');rx.flush();end=time.monotonic()+6
                    while time.monotonic()<end:
                        line=rx.readline()
                        if line:header_event=log_event('rx',line.decode(errors='replace').strip())
                        if not line.startswith(b'[SNAPSHOT_BEGIN]'):continue
                        parts=line.split();size=int(parts[1]);roi=list(map(int,parts[2:]))
                        data=bytearray();end_binary=time.monotonic()+6
                        while len(data)<size and time.monotonic()<end_binary:data.extend(rx.read(size-len(data)))
                        if len(data)!=size:raise RuntimeError('Short JPEG')
                        end_marker=time.monotonic()+2
                        while time.monotonic()<end_marker:
                            s=rx.readline().strip()
                            if s==b'[SNAPSHOT_END]':log_event('rx',s.decode());break
                        else:raise RuntimeError('Missing snapshot end')
                        file=OUT/f'{index+1:02d}_tx_{"on" if on else "off"}.jpg';file.write_bytes(data)
                        gray=cv2.imdecode(np.frombuffer(data,np.uint8),cv2.IMREAD_GRAYSCALE)
                        if gray is None:raise RuntimeError('Invalid JPEG')
                        x,y,w,h=roi;crop=gray[y:y+h,x:x+w]
                        shots.append(dict(file=file.name,commanded_on=on,roi=roi,
                            shape=list(gray.shape),mean=float(crop.mean()),max=int(crop.max()),
                            fraction_ge250=float(np.mean(crop>=250)),
                            tx_level=level,request_mono_ns=sent,header_mono_ns=header_event['mono_ns'],
                            header_after_level_ms=(header_event['mono_ns']-level['mono_ns'])/1e6))
                        print(shots[-1],flush=True)
                        break
                    else:raise TimeoutError('Snapshot')
                # Drain TX until the contrast mode ends automatically; restore
                # ordinary transmission without changing any sensor settings.
                wait_tx('[LED_CONTRAST] END',25)
        except (Exception,KeyboardInterrupt) as e:error=type(e).__name__+': '+str(e)
        finally:
            stop.set()
            if tx:tx.close()
            result=dict(complete=error is None,error=error,shots=shots,
                scope='Current scene; commanded LED levels, approximate SSH/serial timing. No hardware exposure timestamps; no corrected BER.')
            (OUT/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
            print(OUT, 'error=',error)
    return int(error is not None)


if __name__=='__main__':raise SystemExit(main())
