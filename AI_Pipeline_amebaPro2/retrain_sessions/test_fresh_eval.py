"""Synthetic contracts; never opens serial or alters benchmark captures."""
import sys
sys.dont_write_bytecode = True
import copy
import csv
import io
import unittest
from unittest.mock import Mock, patch
import numpy as np
import fresh_common as f
import onboard_replay_sessions as board
import eval_fresh as scorer
import robustness_fresh as sweep


def manifest():
    return dict(version=1,conditions=[dict(name='3m_off',distance_m=3,lighting='off',sessions=[
        dict(session='replay_a',repeat=1),dict(session='replay_b',repeat=2)])],excluded=[])


class Contracts(unittest.TestCase):
    def test_manifest(self):
        m=manifest(); self.assertEqual(len(f.parse_manifest(m)),2)
        for change in ('duplicate','path','repeat','lighting','excluded'):
            bad=copy.deepcopy(m)
            if change=='duplicate': bad['conditions'][0]['sessions'][1]['session']='replay_a'
            if change=='path': bad['conditions'][0]['sessions'][0]['session']='../bad'
            if change=='repeat': bad['conditions'][0]['sessions'][0]['repeat']=True
            if change=='lighting': bad['conditions'][0]['lighting']='unknown'
            if change=='excluded': del bad['excluded']
            with self.subTest(change=change), self.assertRaises((ValueError,KeyError)):
                f.parse_manifest(bad)

    def test_exclusions(self):
        m=manifest(); m['excluded']=[dict(session='replay_a',reason='occlusion')]
        self.assertEqual([r['session'] for r in f.parse_manifest(m)],['replay_b'])
        m['excluded'][0]['reason']=' '
        with self.assertRaises(ValueError): f.parse_manifest(m)

    def test_cp(self):
        self.assertAlmostEqual(f.ci(0,10)[1],1-.025**.1)
        self.assertEqual(f.ci(0,10)[0],0)
        self.assertEqual(f.ci(10,10)[1],1)
        self.assertAlmostEqual(f.ci(5,10)[0],.18708602844739852)
        self.assertAlmostEqual(f.ci(5,10)[1],.8129139715526015)

    def test_polarity_and_threshold(self):
        self.assertEqual(f.bits_hex('800000000001').tolist(),[1]+[0]*46+[1])
        self.assertEqual(f.decision_bytes('00807fff').tolist(),[1,0,1,0])
        rows=[dict(classic_errors=48),dict(classic_errors=0)]
        self.assertEqual(f.metric(rows,'classic')['errors'],48)
        # Classic is literal, including when a hypothetical optical gain is negative.
        with patch.object(f,'load_json',side_effect=[dict(complete=True,error=None,frames_compared=1,bits_compared=48),
                dict(alignment='tracker-manchester-v1',source_capture='capture'),
                [dict(payload_mode='test',frame_id=1,frame_hex='000000000000'),
                 dict(payload_mode='test',frame_id=2,frame_hex='000000000001')],
                [dict(frame_id=1,frame_hex='000000000000',input_int8_hex='80'*192,
                      classic_bits='1'*48,signed_optical_gain=-1)]]):
            r=f.load_windows([dict(session='replay_a')])[0]
            self.assertEqual(r['source']['classic_bits'],'1'*48)

    def test_preamble_polarity(self):
        for prefix, flipped in [('0101',True),('1010',False),('0000',False),('1111',False)]:
            bits=np.array(list(prefix+'01'*22),np.uint8)
            result,flag=f.apply_polarity(bits,'preamble')
            self.assertEqual(flag,flipped)
            np.testing.assert_array_equal(result,1-bits if flipped else bits)
            direct,flag=f.apply_polarity(bits)
            self.assertFalse(flag)
            np.testing.assert_array_equal(direct,bits)

    def test_reference_kernel_selection(self):
        from tensorflow.lite.python.interpreter import OpResolverType
        it=Mock()
        it.get_input_details.return_value=[dict(dtype=np.int8,shape=(1,48,1,4),quantization=(1/255,-128),index=0)]
        it.get_output_details.return_value=[dict(dtype=np.int8,shape=(1,48,1),quantization=(1/256,-128),index=1)]
        raw=np.array([-1,0]*24,np.int8).reshape(1,48,1)
        it.get_tensor.return_value=raw
        with patch.object(sys.modules['tensorflow.lite.python.interpreter'],'Interpreter',return_value=it) as factory:
            for path in (f.AUG,f.RETRAINED):
                actual,bits=f.reference_inference(path,[np.zeros(192,np.int8)])
                factory.assert_called_with(model_path=str(path),num_threads=1,
                    experimental_op_resolver_type=OpResolverType.BUILTIN_WITHOUT_DEFAULT_DELEGATES)
                np.testing.assert_array_equal(bits,np.array([[0,1]*24]))
                np.testing.assert_array_equal(actual,raw.reshape(1,48))

    def test_sweep_zero_equality_synthetic(self):
        from pathlib import Path
        # A Manchester synthetic frame sampled on the same centered 20 Hz grid.
        truth=np.array([1,0]*24,np.uint8)
        window=np.repeat(np.stack([truth,1-truth],axis=1),2,axis=1).reshape(192).astype(float)
        frame=dict(session='synthetic',frame_id=1,distance_m=3,lighting='off',condition='3m_off',
                   frame_hex='aaaaaaaaaaaa',start=1.,values='synthetic')
        def sample(values,t):
            return window[int(round((t-1.)/.05-.5))]
        with patch.object(sweep,'sample',side_effect=sample):
            rebuilt=sweep.offset_windows([frame],0)
        np.testing.assert_array_equal(rebuilt[0],window)
        q=np.frombuffer(sweep.normalize(window),np.int8)
        reference=np.stack([truth])
        with patch.object(sweep,'reference_inference',return_value=(np.zeros((1,48),np.int8),reference)):
            rows=sweep.score_windows([frame],rebuilt)
        # Main scorer path uses stored q and Classic bits, same shared polarity.
        main_rows=[]
        for pred in [truth]:
            corrected,flip=f.apply_polarity(pred,'preamble')
            main_rows.append({d+'_errors':int(np.sum(corrected!=truth)) for d in sweep.DECODERS})
        pooled={g:dict(metrics={d:json_metric for d,json_metric in entry['metrics'].items()},
                       flipped_windows=entry['flipped_windows']) for g,entry in sweep.grouped(rows).items()}
        import json
        summary=json.loads(json.dumps(dict(sessions={'synthetic':{}},pooled=pooled)))
        files={}
        for d in sweep.DECODERS:
            self.assertEqual(f.metric(main_rows,d),sweep.grouped(rows)['all']['metrics'][d])
            stream=io.StringIO()
            writer=csv.DictWriter(stream,fieldnames=['session','frame_id','input_int8_hex','predicted_bits','errors','polarity_flipped'])
            writer.writeheader(); writer.writerow(dict(session='synthetic',frame_id=1,input_int8_hex=q.tobytes().hex(),
                predicted_bits=''.join(map(str,truth)),errors=0,polarity_flipped=False))
            files[f'{d}__synthetic.csv']=stream.getvalue()
        def opened(path, *args, **kwargs):
            return io.StringIO(files[path.name])
        with patch.object(Path,'open',opened):
            sweep.check_zero(rows,summary,Path('synthetic'))
            rows[0]['aug_host_bits']='0'*48
            with self.assertRaises(AssertionError): sweep.check_zero(rows,summary,Path('synthetic'))

    def test_duplicate_windows_rejected(self):
        row=dict(frame_id=1,frame_hex='000000000000',input_int8_hex='80'*192,classic_bits='0'*48)
        with patch.object(f,'load_json',side_effect=[dict(complete=True,error=None,frames_compared=2,bits_compared=96),
                dict(alignment='tracker-manchester-v1',source_capture='capture'),
                [dict(payload_mode='test',frame_id=1,frame_hex='000000000000')],[row,row]]):
            with self.assertRaises(ValueError): f.load_windows([dict(session='replay_a')])

    def test_eval_timeout_retry_identity_barrier(self):
        port=Mock(); log=io.StringIO()
        # First attempt expires immediately; INFO identity is required before retry.
        with patch.object(board,'identity') as ready,patch.object(board,'read',return_value='[GOAL2_EVAL] cpu='+('00'*48)+' npu='+('01'*48)), \
             patch.object(board.time,'monotonic',side_effect=[0,4,5,5]),patch.object(board.time,'sleep'):
            c,n=board.evaluate(port,np.zeros(192,np.int8),log,3,1,.05)
        ready.assert_called_once_with(port,log,12)
        self.assertEqual(port.write.call_count,2)
        self.assertEqual(c.tolist(),[0]*48)
        self.assertEqual(n.tolist(),[1]*48)
        self.assertIn('eval_timeout',log.getvalue())

    def test_payload(self):
        p=f.payload_check(['000000000000','000000000001'],{'000000000000'})
        self.assertEqual(p['overlap_count'],1)
        self.assertEqual(p['distinct_words'],2)
        self.assertEqual(p['nearest_hamming_min'],0)
        self.assertEqual(p['nearest_hamming_mean'],.5)
        self.assertFalse(p['passed'])
        self.assertTrue(f.payload_check(['ffffffffffff'],{'000000000000'})['passed'])

    def test_refuse_overwrite(self):
        with patch.object(f.Path,'exists',return_value=True):
            with self.assertRaises(FileExistsError): f.output_path(f.FRESH/'synthetic_existing')
        with self.assertRaises(ValueError): f.output_path(f.HERE/'outside')

    def test_identity_mismatch_no_eval(self):
        port=Mock(); port.readline.return_value=(
            '[GOAL2_READY] model_sha256=wrong accuracy=buffered-v1 build=paired-int8-pipeline-v2 bank=64 pairs=512\n').encode()
        with patch.object(board,'digest',return_value=f.AUG_SHA), self.assertRaises(RuntimeError):
            board.replay(port,[dict(q=np.zeros(192,np.int8))],np.zeros((1,48),np.int8),io.StringIO(),Mock())
        self.assertEqual(port.write.call_args_list,[unittest.mock.call(b'\nGOAL2 INFO\n')])

    def test_identity_valid_and_all_contract_fields(self):
        line=f'[GOAL2_READY] model_sha256={f.AUG_SHA} accuracy=buffered-v1 build=paired-int8-pipeline-v2 bank=64 pairs=512'
        with patch.object(board,'digest',return_value=f.AUG_SHA):
            port=Mock();port.readline.return_value=line.encode()
            self.assertEqual(board.identity(port,io.StringIO()),line)
            port.readline.return_value=line.replace('pairs=512','pairs=1').encode()
            with self.assertRaises(RuntimeError): board.identity(port,io.StringIO())

    def test_board_mismatch_retained(self):
        port=Mock(); h=np.zeros(48,np.int8); c=h.copy(); c[3]=-1
        records=[dict(session='a',frame_id=i,frame_hex='0'*12,q=np.zeros(192,np.int8)) for i in (1,2)]
        writer=Mock()
        with patch.object(board,'identity',return_value='ready'),patch.object(board,'evaluate',return_value=(c,h)):
            _, mismatches=board.replay(port,records,[h,h],io.StringIO(),writer)
        self.assertEqual(writer.writerow.call_count,2)
        self.assertEqual(len(mismatches),2)
        self.assertEqual(mismatches[0]['positions'],[3])

    def test_agreement_and_unavailable(self):
        self.assertEqual(scorer.agreement([dict(raw=dict(a=None,b=None))],'a','b'),dict(available=False))
        result=scorer.agreement([dict(raw=dict(a=np.array([-1,0,1]),b=np.array([0,0,2])))],'a','b')
        self.assertEqual(result['threshold_disagreements'],1)
        self.assertEqual(result['raw_equal_values'],1)

    def test_dry_run_never_imports_serial(self):
        with patch.object(board,'digest',return_value=f.AUG_SHA),patch.object(board,'output_path',return_value=f.FRESH/'mock'), \
             patch.object(board,'load_windows',return_value=[dict(q=np.zeros(192,np.int8))]), \
             patch.object(board,'raw_aug',return_value=np.zeros((1,48),np.int8)),patch.dict(sys.modules,{'serial':None}), \
             patch('sys.stdout',new_callable=io.StringIO):
            self.assertEqual(board.main(['--sessions','synthetic','--output',str(f.FRESH/'mock'),'--dry-run']),0)


if __name__=='__main__':
    unittest.main()
