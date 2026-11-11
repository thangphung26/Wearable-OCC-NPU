"""Synthetic timing tests. No recordings, payload labels or serial access."""
import unittest
from preamble_tracker import PreambleTracker


def waveform(gain=20, shift=.08, drift=0):
    v=[]
    for i in range(900):
        t=(i+1)*.05
        level=0
        for frame in range(4):
            start=1+frame*10+shift+frame*drift
            if start <= t < start+.8:
                level=[1,0,0,1,1,0,0,1][int((t-start)/.1)]
                break
        v.append(60+gain*level)
    return v


class TrackerTests(unittest.TestCase):
    def test_normal_and_inverted(self):
        for gain in [20,-20]:
            tr=PreambleTracker();v=waveform(gain)
            for i in range(4):
                r=tr.align(v,1+i*10)
                self.assertLess(abs(r['offset']-.08),.06)
                self.assertEqual(r['locked_polarity'],1 if gain>0 else -1)

    def test_rejects_one_bit_jump(self):
        # Candidate with opposite polarity and perfect fit must not steal lock.
        from unittest.mock import patch
        tr=PreambleTracker()
        first=[dict(start=1.08,offset=.08,fit=.95,gain=20)]
        second=[dict(start=11.08,offset=.08,fit=.5,gain=15),
                dict(start=10.88,offset=-.12,fit=1,gain=-25)]
        with patch('preamble_tracker.candidates',side_effect=[first,second]):
            tr.align([],1);r=tr.align([],11)
        self.assertAlmostEqual(r['offset'],.08)
        self.assertFalse(r['fallback'])

    def test_gentle_drift(self):
        tr=PreambleTracker();v=waveform(drift=.005)
        for i in range(4):
            r=tr.align(v,1+i*10)
            self.assertLess(abs(r['offset']-(.08+i*.005)),.06)

    def test_flat_signal_keeps_window(self):
        tr=PreambleTracker();tr.align(waveform(),1)
        r=tr.align([60]*900,11)
        self.assertTrue(r['fallback']);self.assertTrue(r['weak_fit'])
        self.assertEqual(r['locked_polarity'],1)

    def test_time_order(self):
        tr=PreambleTracker();tr.align(waveform(),1)
        with self.assertRaises(ValueError):tr.align(waveform(),1)


if __name__=='__main__':unittest.main()
