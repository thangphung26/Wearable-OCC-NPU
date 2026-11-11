"""Gate real exported metadata and saved Acuity dequantized simulator tensors."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from common import HERE, MODEL, baseline, digest, predict, WORK, NBG_NAME, META_NAME

def check_metadata(meta):
    for key,shape,scale in [('Inputs',[1,48,1,4],1/255),('Outputs',[1,48,1],1/256)]:
        assert len(meta[key])==1
        t=next(iter(meta[key].values()))
        assert t['shape']==shape and t['format']=='nhwc', t
        q=t['quantize']
        assert q['qtype']=='i8' and q['zero_point']==-128, q
        assert abs(q['scale']-scale)<1e-8, q


def simulator_agreement(y, host):
    assert y.shape==host.shape==(800,48) and np.isfinite(y).all()
    assert ((y>=-1e-6)&(y<=1+1e-6)).all(), 'Expect dequantized probability outputs'
    value=float(np.mean((y>=.5)==(host>=0)))
    assert value>=.999, value
    return value

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work',type=Path,default=WORK)
    p.add_argument('--simulator-outputs',type=Path,
                   help='Optional .npy float probabilities with shape (800,48), in manifest order')
    a=p.parse_args()
    check_metadata(json.loads((a.work/META_NAME).read_text()))
    d,ids=baseline()
    manifest=json.loads((a.work/'manifest.json').read_text())
    assert manifest['model_sha256']==digest(MODEL)
    assert manifest['input_sha256']==hashlib.sha256(d['input_int8'][ids].tobytes()).hexdigest()
    host=predict(d['input_int8'][ids])
    if a.simulator_outputs:
        y=np.load(a.simulator_outputs,allow_pickle=False)
    else:
        ys=[]
        for j in range(800):
            files=list((a.work/'simulator').rglob(f'iter_{j}_output_*out0*.tensor'))
            assert len(files)==1, (j,files)
            ys.append(np.loadtxt(files[0]).reshape(48))
        y=np.stack(ys)
    agreement=simulator_agreement(y,host)
    result=dict(agreement=agreement,model_sha256=digest(MODEL),
                nbg_sha256=digest(a.work/NBG_NAME),windows=800)
    (a.work/'check.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))
if __name__=='__main__':
    main()
