"""Host numerical test of AUG-local graph, never a board timing result."""
import json
import subprocess
import tempfile
import unittest
import numpy as np
from common import ROOT, HERE, SKETCH, baseline, predict, digest, MODEL, PARITY


class CPUReferenceTest(unittest.TestCase):
    def test_aug_graph(self):
        src = ROOT/'libraries/VLCInt8Reference/src'
        d, ids = baseline()
        x = np.concatenate([d['input_int8'][ids],
            np.random.default_rng(20261016).integers(-128,128,(128,192),dtype=np.int8),
            np.full((1,192),-128,np.int8), np.full((1,192),127,np.int8), np.zeros((1,192),np.int8)])
        with tempfile.TemporaryDirectory() as td:
            exe = td+'/cpu'
            subprocess.run(['g++','-std=c++11','-O3','-DNDEBUG','-DTF_LITE_STATIC_MEMORY',
                '-I'+str(SKETCH), '-I'+str(src),
                str(ROOT/'RX/Ameba_RX_NPU_Receiver/tests/cpu_reference_host.cpp'),
                str(src/'tensorflow/lite/kernels/internal/common.cc'),
                str(src/'tensorflow/lite/kernels/internal/runtime_shape.cc'),'-o',exe],check=True)
            raw = subprocess.run([exe],input=x.tobytes(),stdout=subprocess.PIPE,check=True).stdout
        z = np.frombuffer(raw,np.int8).reshape(-1,48)
        result = dict(kind='host_correctness_only',model_sha256=digest(MODEL),results={})
        for name, ref in [('reference',True),('builtin_no_delegates',False)]:
            y = predict(x, ref)
            diff = np.abs(z.astype(int)-y.astype(int))
            result['results'][name] = dict(windows=len(x),values=int(diff.size),
                unequal_values=int(np.count_nonzero(diff)),mae_lsb=float(diff.mean()),
                max_lsb=int(diff.max()),bit_disagreements=int(np.sum((z>=0)!=(y>=0))))
        PARITY.write_text(json.dumps(result,indent=2)+'\n')
        self.assertEqual(result['results']['reference']['unequal_values'],0)
        self.assertEqual(result['results']['builtin_no_delegates']['bit_disagreements'],0)
        print(json.dumps(result))


if __name__ == '__main__':
    unittest.main()
