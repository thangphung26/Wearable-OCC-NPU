import io
import sys
import unittest
from unittest.mock import patch
import numpy as np
from common import HERE, PIPE
from check_nbg import check_metadata, simulator_agreement
sys.path.insert(0,str(PIPE/'retrain_sessions'))
from onboard_replay import EVAL, identity, evaluate, metric, agreement


class ToolsTest(unittest.TestCase):
    def test_metadata_rejects_uint8(self):
        import json
        m=json.loads((HERE.parent/'vlc_edge_cnn_ch4_nbg_meta.json').read_text())
        check_metadata(m)
        next(iter(m['Inputs'].values()))['quantize']['qtype']='u8'
        with self.assertRaises(AssertionError):
            check_metadata(m)

    def test_simulator_gate_boundary(self):
        host=np.full((800,48),-128,np.int8)
        sim=np.zeros((800,48),float)
        sim.flat[:38]=1
        self.assertGreaterEqual(simulator_agreement(sim,host),.999)
        sim.flat[38]=1
        with self.assertRaises(AssertionError):
            simulator_agreement(sim,host)
        sim.flat[0]=np.nan
        with self.assertRaises(AssertionError):
            simulator_agreement(sim,host)

    def test_threshold_and_ci(self):
        m=metric(np.zeros((2,48),bool),np.zeros((2,48),np.int8))
        self.assertEqual(m['errors'],0)
        self.assertGreater(m['ber_95cp'][1],0)
        a=np.array([[-128,-1,0,127]],np.int8)
        b=np.array([[-127,0,1,126]],np.int8)
        self.assertEqual(agreement(a,b)['bit_disagreements'],1)

    def test_protocol_exact_lengths(self):
        self.assertIsNotNone(EVAL.fullmatch('[GOAL2_EVAL] cpu='+'80'*48+' npu='+'00'*48))
        self.assertIsNone(EVAL.fullmatch('[GOAL2_EVAL] cpu='+'80'*47+' npu='+'00'*48))

    def test_wrong_identity_sends_no_eval(self):
        class Port:
            def __init__(self): self.sent=[]
            def write(self,v): self.sent.append(v)
            def flush(self): pass
            def readline(self): return b'[GOAL2_READY] model_sha256=wrong\n'
        p=Port()
        with self.assertRaises(RuntimeError):
            identity(p,io.StringIO(),1)
        self.assertEqual(p.sent,[b'\nGOAL2 INFO\n'])

    def test_eval_decodes_signed_bytes(self):
        class Port:
            def write(self,v): pass
            def flush(self): pass
            def readline(self): return ('[GOAL2_EVAL] cpu='+'80'*48+' npu='+'7f'*48+'\n').encode()
        with patch('onboard_replay.time.sleep'):
            c,n=evaluate(Port(),np.zeros(192,np.int8),io.StringIO(),1,0,.01)
        self.assertTrue(np.all(c==-128))
        self.assertTrue(np.all(n==127))
