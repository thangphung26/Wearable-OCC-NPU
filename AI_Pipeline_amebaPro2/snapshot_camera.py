#!/usr/bin/env python3
"""Save an unchanged serial camera JPEG for diagnostics, with no GUI/retries."""
import argparse
import json
from pathlib import Path
import time
import serial
import cv2
import numpy as np


def main():
    p=argparse.ArgumentParser();p.add_argument('output',type=Path);a=p.parse_args()
    if a.output.exists():p.error('Refusing overwrite')
    with serial.Serial('/dev/ttyUSB0',115200,timeout=.25,write_timeout=2,exclusive=True) as port:
        port.write(b'\nSNAP\n');port.flush();end=time.monotonic()+12
        while time.monotonic()<end:
            line=port.readline()
            if not line.startswith(b'[SNAPSHOT_BEGIN]'):continue
            fields=line.split();size=int(fields[1]);roi=list(map(int,fields[2:]))
            data=bytearray();deadline=time.monotonic()+12
            while len(data)<size and time.monotonic()<deadline:data.extend(port.read(size-len(data)))
            if len(data)!=size:raise RuntimeError('Short JPEG')
            end=time.monotonic()+3
            while time.monotonic()<end:
                if port.readline().strip()==b'[SNAPSHOT_END]':break
            else:raise RuntimeError('Missing end marker')
            gray=cv2.imdecode(np.frombuffer(data,np.uint8),cv2.IMREAD_GRAYSCALE)
            if gray is None:raise RuntimeError('Invalid JPEG')
            a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_bytes(data)
            x,y,w,h=roi;crop=gray[y:y+h,x:x+w]
            print(json.dumps(dict(path=str(a.output),roi=roi,shape=list(gray.shape),
                mean=float(crop.mean()),max=int(crop.max()),fraction_ge250=float(np.mean(crop>=250)))))
            return
        raise TimeoutError('No snapshot')


if __name__=='__main__':main()
