"""Replay saved hardware evidence without opening serial ports or live servers."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'backend'))
import server

FIXTURES = APP.parent / 'results/real_sensor_demo/20261108_final_monitor'


def fixture(name):
    return json.loads((FIXTURES / (name + '.json')).read_text())


class HardwareComparisonTests(unittest.TestCase):
    def setUp(self):
        self.state = server.SystemState()
        self.state.set_mode('serial')

    def test_replay_all_52_pairs_bit_exact_despite_temperature_quantisation(self):
        pairs = fixture('comparison')['history']
        self.assertEqual(len(pairs), 52)
        for pair in pairs:
            self.state.on_tx_telemetry(pair['tx'])
            self.state.on_serial_telemetry(pair['rx'])
            latest = self.state.comparison_stats['latest_match']
            self.assertTrue(latest['is_match'])
            self.assertEqual(latest['match_basis'], 'frame_hex')
            self.assertIs(latest['tx']['imu_valid'], True)
            self.assertEqual(latest['rx']['crc_status'], 'verified-by-host')
        stats = self.state.comparison_stats
        self.assertEqual((stats['total_matched'], stats['total_compared']), (52, 52))
        self.assertEqual(stats['accuracy_pct'], 100)
        differences = [p for p in self.state.comparison_history if not p['fields_match']]
        self.assertEqual(len(differences), 1)
        self.assertEqual(differences[0]['field_mismatches'], ['temp_c'])
        self.assertEqual(differences[0]['tx']['temp_c'], 36.2)
        self.assertEqual(differences[0]['rx']['temp_c'], 36.3)

    def test_frame_mismatch_wins_over_identical_fields_and_fields_are_fallback(self):
        pair = fixture('comparison')['history'][0]
        tx, rx = pair['tx'], dict(pair['rx'])
        rx['frame_hex'] = '0x000000000000'
        result = server.compare_records(tx, rx)
        self.assertFalse(result['is_match'])
        self.assertTrue(result['fields_match'])
        self.assertEqual(result['match_basis'], 'frame_hex')
        rx.pop('frame_hex')
        self.assertEqual(server.compare_records(tx, rx)['match_basis'], 'fields')
        self.assertTrue(server.compare_records(tx, rx)['is_match'])
        rx['hr'] += 1
        self.assertFalse(server.compare_records(tx, rx)['is_match'])
        rx['frame_hex'] = 'malformed'
        self.assertFalse(server.compare_records(tx, rx)['is_match'])
        self.assertTrue(server.compare_records(tx, dict(tx, frame_hex=tx['frame_hex'].lower()[2:]))['is_match'])

    def test_crc_every_saved_rx_frame_and_sse_evidence(self):
        for row in fixture('history'):
            self.assertEqual(server.host_crc_status(row['frame_hex']), 'verified-by-host')
        row = fixture('status')['current_telemetry']
        with patch.object(self.state, 'broadcast_sse') as emit:
            self.state.on_serial_telemetry(row)
        telemetry = next(c.args[1] for c in emit.call_args_list if c.args[0] == 'telemetry')
        self.assertEqual(telemetry['crc_status'], 'verified-by-host')
        self.assertEqual(self.state.history[-1]['crc_status'], 'verified-by-host')
        damaged = int(row['frame_hex'], 16) ^ 1
        self.assertEqual(server.host_crc_status(f'{damaged:012X}'), 'rejected')
        accepted = self.state.current_telemetry
        self.state.on_serial_telemetry(dict(row, seq=12, frame_hex=f'{damaged:012X}'))
        self.assertIs(self.state.current_telemetry, accepted)
        self.assertEqual(self.state.receiver_stats['rejected_crc'], 1)
        for malformed in (None, '', 'garbage', 'AABB', 123, '0xA141C258E24300'):
            self.assertEqual(server.host_crc_status(malformed), 'unverified')
        self.state.on_serial_telemetry(dict(row, seq=12, frame_hex=None))
        self.assertEqual(self.state.current_telemetry['crc_status'], 'unverified')

    def test_tx_validity_boolean_normalisation_and_missing_flag(self):
        tx = fixture('comparison')['history'][0]['tx']
        for flag, expected in ((True, True), (False, False), (1, True), (0, False)):
            self.state.on_tx_telemetry(dict(tx, imu_valid=flag))
            self.assertIs(self.state.comparison_stats['latest_tx']['imu_valid'], expected)
        for flag in ('true', None, 2):
            self.state.on_tx_telemetry(dict(tx, imu_valid=flag))
            self.assertNotIn('imu_valid', self.state.comparison_stats['latest_tx'])
        tx.pop('imu_valid')
        self.state.on_tx_telemetry(tx)
        self.assertNotIn('imu_valid', self.state.comparison_stats['latest_tx'])


class HonestAccountingTests(unittest.TestCase):
    RUN = "20261108_final_track_imu"

    def test_recorded_artifacts_preserved_and_all_50_rx_accounted(self):
        replay = server.load_replay(self.RUN)
        root = APP.parent / "results/real_sensor_demo" / self.RUN
        for name in ("history", "status", "comparison"):
            self.assertEqual(replay[name], json.loads((root / (name + ".json")).read_text()))
        self.assertEqual(replay["accounting"]["stats"],
                         {"rx_bit_exact": 48, "rx_unmatched": 2, "rx_unchecked": 0})
        unmatched = [r for r in replay["accounting"]["history"] if r["tx_match"] is False]
        self.assertEqual([r["frame_hex"] for r in unmatched],
                         ["0xA146B0CD8019", "0xA147C4723F7B"])
        self.assertEqual([r["temp_c"] for r in unmatched], [39.1, 36.8])
        for row in unmatched:
            self.assertEqual(server.host_crc_status(row["frame_hex"]), "verified-by-host")
            self.assertEqual(row["tx_match_status"], "no TX frame")

    def test_live_callbacks_recompute_fixture_without_serial_hardware(self):
        replay = server.load_replay(self.RUN)
        txs = [p["tx"] for p in replay["comparison"]["history"]]
        txs.append(replay["comparison"]["stats"]["latest_tx"])
        events = [(r["timestamp_ms"], "tx", r) for r in txs]
        events += [(r["timestamp_ms"], "rx", r) for r in replay["history"]]
        state = server.SystemState()
        state.set_mode("serial")
        state.on_tx_connection(True, "test")
        for timestamp, role, row in sorted(events, key=lambda e: e[0]):
            with patch.object(server.time, "monotonic", return_value=timestamp / 1000), patch.object(
                    server.time, "time", return_value=timestamp / 1000):
                (state.on_tx_telemetry if role == "tx" else state.on_serial_telemetry)(row)
        self.assertEqual(len(state.history), 50)
        self.assertEqual({k: state.comparison_stats[k] for k in replay["accounting"]["stats"]},
                         replay["accounting"]["stats"])
        self.assertEqual(sum(r["tx_match"] is False for r in state.history), 2)
        self.assertEqual(sum(p["status"] == "no TX frame" for p in state.comparison_history), 2)

    def test_coverage_boundaries_disconnect_and_future_tx_evidence(self):
        rx = fixture("history")[0]
        for connected, offset, expected in ((False, 0, None), (True, 25, True),
                                            (True, -25, True), (True, 25.001, None)):
            self.assertIs(server.rx_accounting(rx, 100, [(100 + offset, rx)], connected)["tx_match"], expected)
        other = dict(rx, frame_hex="0xA146B0CD8019")
        self.assertIs(server.rx_accounting(other, 100, [(100, rx)], True)["tx_match"], False)
        state = server.SystemState()
        state.set_mode("serial")
        state.on_tx_connection(True, "test")
        with patch.object(server.time, "monotonic", return_value=100):
            state.on_serial_telemetry(rx)
        self.assertIsNone(state.history[-1]["tx_match"])
        with patch.object(server.time, "monotonic", return_value=110):
            state.on_tx_telemetry(rx)
        self.assertIs(state.history[-1]["tx_match"], True)
        self.assertEqual(state.comparison_stats["rx_unchecked"], 0)
        self.assertEqual(state.comparison_stats["rx_bit_exact"], 1)

    def test_replay_get_validation_missing_and_no_live_mutation(self):
        handler = object.__new__(server.MedicalMonitorHandler)
        from unittest.mock import Mock
        handler.send_json_response = Mock()
        for name, expected in ((self.RUN, 200), ("../status", 400),
                               ("%2e%2e", 400), ("bad-name", 400), ("missing_run", 404)):
            handler.path = "/api/replay/" + name
            handler.send_json_response.reset_mock()
            handler.do_GET()
            args = handler.send_json_response.call_args.args
            self.assertEqual(args[1] if len(args) > 1 else 200, expected)
        with self.assertRaises(ValueError):
            server.load_replay("/tmp")



if __name__ == '__main__':
    unittest.main()
