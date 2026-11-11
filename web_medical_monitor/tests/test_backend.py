"""Backend integrity tests: stdlib only, no sockets or physical serial ports."""
import inspect
import io
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'backend'))
import server
from patient_simulator import PatientSimulator
from serial_receiver import SerialReceiver
from thresholds import validate_telemetry


def packet(**changes):
    data = dict(patient=1, hr=75, spo2=98, temp_c=37.5, imu=0,
                battery=7, alert=0, seq=1)
    data.update(changes)
    return data


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.state = server.SystemState()
        self.global_patch = patch.object(server, 'GLOBAL_STATE', self.state)
        self.global_patch.start()
        self.addCleanup(self.global_patch.stop)
        self.handler = object.__new__(server.MedicalMonitorHandler)
        self.handler.send_json_response = Mock()

    def post(self, path, data):
        self.handler.path = path
        raw = data if isinstance(data, bytes) else json.dumps(data).encode()
        self.handler.headers = {'Content-Length': str(len(raw))}
        self.handler.rfile = io.BytesIO(raw)
        self.handler.send_json_response.reset_mock()
        self.handler.do_POST()
        args = self.handler.send_json_response.call_args.args
        return args[1] if len(args) > 1 else 200

    def ingest(self, **changes):
        self.state.set_mode('api')
        return self.state.process_telemetry(packet(**changes), 'api')

    def test_no_waveform_and_snapshot_worker_period(self):
        self.assertFalse(hasattr(PatientSimulator, 'generate_ecg_chunk'))
        self.assertFalse(hasattr(PatientSimulator, 'generate_ppg_chunk'))
        with patch.object(self.state, 'broadcast_sse') as emit, patch.object(self.state, 'simulate_once') as simulate:
            with patch.object(server.time, 'monotonic', side_effect=[100, 109.9, 110]), patch.object(server.time, 'sleep', side_effect=[None, None, RuntimeError('stop')]):
                with self.assertRaises(RuntimeError):
                    server.background_worker()
            self.assertEqual(simulate.call_count, 2)
            self.assertEqual({c.args[0] for c in emit.call_args_list}, {'receiver_status'})

    def test_simulation_controls_guard_all_other_modes(self):
        for mode in ('serial', 'wifi', 'api'):
            self.state.set_mode(mode)
            q = self.state.register_client()
            seq = self.state.simulator.sequence
            self.assertEqual(self.post('/api/patient', {'patient': 4}), 409)
            for action in ('trigger', 'clear'):
                self.assertEqual(self.post('/api/alert', {'action': action}), 409)
            self.assertFalse(self.state.simulate_once())
            self.assertEqual(self.state.simulator.sequence, seq)
            self.assertTrue(q.empty())
            self.assertIsNone(self.state.current_telemetry)

    def test_patient_simulation_success(self):
        self.assertEqual(self.post('/api/patient', {'patient': 4, 'auto_cycle': False}), 200)
        self.assertEqual(self.state.current_telemetry['patient'], 4)

    def test_crc_rejection_preserves_every_measurement_state(self):
        self.ingest(hr=145)
        self.state.active_alert['acknowledged'] = True
        before = (self.state.current_telemetry, list(self.state.history),
                  self.state.last_telemetry_monotonic, dict(self.state.active_alert), dict(self.state.last_sequences))
        self.assertFalse(self.ingest(seq=2, crc_valid=False))
        after = (self.state.current_telemetry, self.state.history, self.state.last_telemetry_monotonic,
                 self.state.active_alert, self.state.last_sequences)
        self.assertEqual(before, after)
        self.assertEqual(self.state.get_receiver_stats()['rejected_crc'], 1)
        self.assertEqual(self.state.get_receiver_stats()['valid'], 1)

    def test_crc_false_counts_even_with_malformed_medical_fields(self):
        self.assertFalse(self.ingest(hr="bad", crc_valid=False))
        self.state.set_mode("serial")
        self.state.serial_receiver.parse_line('[TELEMETRY:NPU] ' + json.dumps(packet(hr=[], crc_valid=False)))
        self.assertEqual(self.post('/api/telemetry', packet(hr=None, crc_valid=False)), 400)
        # The earlier API scope was reset; the inactive API rejection is ignored.
        self.assertEqual(self.state.get_receiver_stats()['rejected_crc'], 1)
        self.assertIsNone(self.state.current_telemetry)

    def test_crc_missing_is_unverified_and_explicit_true_preserved(self):
        self.ingest()
        self.assertEqual(self.state.current_telemetry['crc_status'], 'unverified')
        self.assertNotIn('crc_valid', self.state.current_telemetry)
        self.ingest(seq=2, crc_valid=True)
        self.assertIs(self.state.current_telemetry['crc_valid'], True)
        self.assertEqual(self.state.current_telemetry['crc_status'], 'valid')

    def test_uart_wire_format_and_crc_false_unchanged(self):
        got = []
        rx = SerialReceiver(on_telemetry=got.append)
        for decoder in ('NPU', 'CLASSIC', 'CLASSIC_PLL'):
            rx.parse_line(f'[TELEMETRY:{decoder}] ' + json.dumps(packet(crc_valid=False)))
            self.assertIs(got[-1]['crc_valid'], False)
            self.assertEqual(got[-1]['decoder'], 'Ameba_' + decoder)
        rx.parse_line('[TELEMETRY:NPU] ' + json.dumps(packet()))
        self.assertNotIn('crc_valid', got[-1])

    def test_alarm_patient_and_reason_change_create_new_episode(self):
        self.state.set_mode('api')
        with patch.object(server.time, 'time', side_effect=[1, 2, 3]):
            self.ingest(hr=145)
            first = dict(self.state.active_alert)
            self.state.active_alert['acknowledged'] = True
            self.ingest(seq=2, hr=145, spo2=90)
            second = dict(self.state.active_alert)
            self.assertFalse(second['acknowledged'])
            self.assertGreater(second['timestamp_ms'], first['timestamp_ms'])
            self.assertNotEqual(second['episode_id'], first['episode_id'])
            self.state.active_alert['acknowledged'] = True
            self.ingest(patient=2, hr=145)
            self.assertFalse(self.state.active_alert['acknowledged'])
            self.assertGreater(self.state.active_alert['timestamp_ms'], second['timestamp_ms'])
            self.assertTrue(self.state.alarms[1]['active'])

    def test_unavailable_does_not_recover_alarm(self):
        self.ingest(hr=145)
        self.state.active_alert['acknowledged'] = True
        previous = dict(self.state.active_alert)
        self.ingest(seq=2, hr=0, spo2=0, temp_c=0)
        self.assertEqual(self.state.active_alert, previous)
        self.ingest(seq=3, hr=75, hr_valid=False)
        self.assertEqual(self.state.active_alert, previous)
        self.ingest(seq=4)
        self.assertFalse(self.state.active_alert['active'])

    def test_alarm_same_reason_survives_value_jitter(self):
        self.ingest(hr=145)
        self.state.active_alert['acknowledged'] = True
        episode = self.state.active_alert['episode_id']
        self.ingest(seq=2, hr=146)
        self.assertEqual(self.state.active_alert['episode_id'], episode)
        self.assertTrue(self.state.active_alert['acknowledged'])

    def test_shared_warning_boundaries_for_every_path(self):
        fixtures = [dict(hr=50, spo2=94, temp_c=38.9, imu=1),
                    dict(hr=49), dict(hr=140), dict(hr=141), dict(spo2=93),
                    dict(temp_c=39.0), dict(imu=2), dict(imu=3),
                    dict(hr=0, spo2=0, temp_c=0)]
        for changes in fixtures:
            data = packet(**changes)
            expected = validate_telemetry(data)[2]['dashboard_warnings']
            for mode in ('api', 'wifi', 'serial', 'simulation'):
                state = server.SystemState()
                state.set_mode(mode)
                self.assertTrue(state.process_telemetry(data, mode))
                self.assertEqual(state.current_telemetry['dashboard_warnings'], expected)
                self.assertEqual(state.current_telemetry['tx_alert_bit'], 0)
            got = []
            SerialReceiver(on_telemetry=got.append).parse_line('[TELEMETRY:NPU] ' + json.dumps(data))
            self.assertEqual(got[0]['dashboard_warnings'], expected)
        sim = PatientSimulator().get_current_telemetry()
        self.assertEqual(sim['dashboard_warnings'], validate_telemetry(sim)[2]['dashboard_warnings'])

    def test_duplicate_does_not_refresh_age_history_or_alarm(self):
        with patch.object(server.time, 'monotonic', return_value=100):
            self.ingest(hr=145)
        self.state.active_alert['acknowledged'] = True
        self.assertFalse(self.ingest(hr=75))
        with patch.object(server.time, 'monotonic', return_value=126):
            stats = self.state.get_receiver_stats()
        self.assertEqual(stats['data_age_sec'], 26)
        self.assertTrue(stats['stale'])
        self.assertEqual(stats['duplicate_seq'], 1)
        self.assertEqual(len(self.state.history), 1)
        self.assertTrue(self.state.active_alert['acknowledged'])

    def test_sequence_wrap_and_patient_identity(self):
        for seq in (14, 15, 0, 1):
            self.assertTrue(self.ingest(seq=seq))
        self.assertTrue(self.ingest(patient=2, seq=1))
        self.assertFalse(self.ingest(patient=1, seq=1))

    def test_mode_and_source_switch_wait_for_new_snapshot(self):
        self.ingest()
        self.state.set_mode('serial')
        stats = self.state.get_receiver_stats()
        self.assertIsNone(self.state.current_telemetry)
        self.assertIsNone(stats['data_age_sec'])
        self.assertEqual(stats['state'], 'waiting for serial')
        self.assertFalse(self.state.process_telemetry(packet(), 'simulation'))
        self.state.process_telemetry(packet(), 'serial')
        self.state.set_mode('serial', force=True)
        self.assertIsNone(self.state.current_telemetry)

    def test_counters_reset_on_source_switch_and_forced_uart_reconnect(self):
        self.state.simulate_once()
        with patch.object(server.time, 'time', return_value=1234):
            self.state.set_mode('api')
        stats = self.state.get_receiver_stats()
        self.assertEqual(stats['counters_since_ms'], 1234000)
        self.assertEqual(stats['source'], 'api')
        for key in ('valid', 'rejected_crc', 'duplicate_seq', 'invalid_packets'):
            self.assertEqual(stats[key], 0)
        self.assertFalse(self.state.process_telemetry(packet(crc_valid=False), 'simulation'))
        self.assertFalse(self.state.process_telemetry({}, 'serial'))
        self.ingest()
        self.ingest()
        self.ingest(seq=2, crc_valid=False)
        self.ingest(seq=2, hr='bad')
        stats = self.state.get_receiver_stats()
        self.assertEqual([stats[k] for k in ('valid', 'rejected_crc', 'duplicate_seq', 'invalid_packets')], [1, 1, 1, 1])
        self.assertEqual(stats['counters_since_ms'], 1234000)
        self.state.set_mode('serial')
        self.state.process_telemetry(packet(), 'serial')
        self.state.set_mode('serial', force=True)
        stats = self.state.get_receiver_stats()
        self.assertEqual(stats['source'], 'serial')
        self.assertEqual([stats[k] for k in ('valid', 'rejected_crc', 'duplicate_seq', 'invalid_packets')], [0, 0, 0, 0])
        self.assertTrue(self.state.process_telemetry(packet(), 'serial'))

    def test_simulation_provenance_and_rates(self):
        self.state.simulate_once()
        data = self.state.current_telemetry
        self.assertEqual(data['source'], 'simulation')
        self.assertEqual(data['decoder'], 'simulated')
        for key in ('fps', 'crc_valid', 'roi_box', 'signal_amplitude'):
            self.assertNotIn(key, data)
        stats = self.state.get_receiver_stats()
        self.assertEqual((stats['acquisition_fps'], stats['resampling_hz'], stats['link_bit_s'], stats['snapshot_period_s']), (None, 20, 5, 10))
        self.state.set_mode('serial')
        self.assertIsNone(self.state.get_receiver_stats()['acquisition_fps'])
        self.state.serial_receiver.parse_line('[STATUS] frames=100 fps=30.00 valid=10 rejected=2')
        stats = self.state.get_receiver_stats()
        self.assertEqual(stats['acquisition_fps'], 30)
        self.assertEqual(stats['receiver_rejected_crc'], 2)
        self.assertEqual(stats['rejected_crc'], 0)
        self.state.set_mode('simulation')
        self.assertIsNone(self.state.get_receiver_stats()['acquisition_fps'])

    def test_comparison_identity_age_fields_and_zero(self):
        self.assertIsNone(self.state.comparison_stats['accuracy_pct'])
        self.state.set_mode('serial')
        with patch.object(server.time, 'monotonic', return_value=100):
            self.state.on_tx_telemetry(packet(patient=2))
            self.state.on_tx_telemetry(packet())
        self.state.on_serial_telemetry(packet(patient=3))
        self.assertEqual(self.state.comparison_stats['total_compared'], 0)
        with patch.object(server.time, 'monotonic', return_value=126):
            self.state.on_serial_telemetry(packet())
        self.assertEqual(self.state.comparison_stats['total_compared'], 0)
        self.state.on_tx_telemetry(packet(seq=2))
        self.state.on_serial_telemetry(packet(seq=2, battery=6))
        self.assertFalse(self.state.comparison_history[-1]['is_match'])
        self.state.on_tx_telemetry(packet(seq=3))
        self.state.on_serial_telemetry(packet(seq=3))
        item = self.state.comparison_history[-1]
        self.assertTrue(item['is_match'])
        self.assertEqual(item['status'], 'field match')
        self.assertEqual(item['compared_fields'], list(server.COMPARED_FIELDS))
        self.assertEqual(self.state.comparison_stats['accuracy_pct'], 50)
        self.state.on_tx_telemetry(packet(seq='<img>'))
        self.assertNotIn((1, '<img>'), self.state.tx_cache)

    def test_rejected_rx_never_compared(self):
        self.state.set_mode('serial')
        self.state.on_tx_telemetry(packet())
        self.state.on_serial_telemetry(packet(crc_valid=False))
        self.assertEqual(self.state.comparison_stats['total_compared'], 0)
        self.assertEqual(self.state.get_receiver_stats()['rejected_crc'], 1)

    def test_strict_integer_boolean_and_finite_fields(self):
        for key, value in [('hr', 75.9), ('hr', '75'), ('seq', True), ('seq', 16), ('seq', -1),
                           ('alert', 'false'), ('crc_valid', 'false'), ('hr_valid', 'false'),
                           ('ppg_valid', 0), ('temp_c', float('nan')), ('temp_c', True)]:
            with self.subTest(key=key, value=value):
                self.assertFalse(validate_telemetry(packet(**{key: value}))[0])
        for data in ([], None, 'text', 42):
            self.assertFalse(validate_telemetry(data)[0])
        self.assertEqual(self.post('/api/patient', {'patient': 2, 'auto_cycle': 'false'}), 400)
        self.assertEqual(self.state.simulator.profile_index, 0)

    def test_malformed_http_body_no_mutation(self):
        self.state.simulate_once()
        before = self.state.current_telemetry
        for raw in (b'[]', b'null', b'"abc"', b'{', b'', b'\xff', b'{"x":NaN}', b'{"x":1e309}'):
            self.assertEqual(self.post('/api/reset', raw), 400)
            self.assertIs(self.state.current_telemetry, before)
            self.assertEqual(len(self.state.history), 1)
        self.assertEqual(self.post('/api/reset', b'x' * (server.MAX_JSON_BODY_BYTES + 1)), 413)
        self.assertEqual(len(self.state.history), 1)
        self.assertEqual(self.post('/api/telemetry', packet(seq='bad')), 400)
        self.assertEqual(self.state.mode, 'simulation')

    def test_serial_errors_skip_line_and_continue(self):
        got = []
        rx = SerialReceiver(on_telemetry=got.append)
        for value in ({}, {'hr': []}, packet(hr='bad'), packet(temp_c=None), packet(crc_valid='false')):
            rx.parse_line('[TELEMETRY:NPU] ' + json.dumps(value))
        rx.parse_line('[STATUS] fps=1.2.3 roi_activity=...')
        rx.parse_line('[TELEMETRY:NPU] {bad json}')
        rx.parse_line('[TELEMETRY:NPU] ' + json.dumps(packet()))
        self.assertEqual(len(got), 1)

    def test_tx_only_never_opens_rx(self):
        with patch.object(self.state.serial_receiver, 'connect') as rx, patch.object(self.state.tx_receiver, 'connect', return_value=True) as tx:
            self.assertEqual(self.post('/api/connect_dual', {'tx_port': '/fake', 'tx_baud': 115200}), 200)
            rx.assert_not_called()
            tx.assert_called_once_with('/fake', 115200)
        self.assertEqual(self.state.mode, 'simulation')

    def test_connection_parameters_validate_before_connect(self):
        with patch.object(self.state.serial_receiver, 'connect') as rx, patch.object(self.state.tx_receiver, 'connect') as tx:
            self.assertEqual(self.post('/api/connect_dual', {'rx_port': '/fake', 'tx_baud': 1.5}), 400)
            rx.assert_not_called()
            tx.assert_not_called()

    def test_connect_disconnect_serialized(self):
        rx = SerialReceiver()
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        def connecting(*args):
            entered.set()
            release.wait(2)
        def disconnecting():
            rx.disconnect()
            finished.set()
        with patch.object(rx, '_connect', side_effect=connecting), patch.object(rx, '_disconnect') as disconnect:
            first = threading.Thread(target=rx.connect, args=('/fake',))
            second = threading.Thread(target=disconnecting)
            first.start()
            self.assertTrue(entered.wait(1))
            second.start()
            self.assertFalse(finished.wait(0.02))
            release.set()
            first.join(2)
            second.join(2)
            self.assertTrue(finished.is_set())
            disconnect.assert_called_once()
        self.assertTrue(hasattr(self.state.connection_lock, 'acquire'))

    def test_bounded_retention_and_csv_stream(self):
        self.state.set_mode('serial')
        for i in range(260):
            data = packet(seq=i % 16)
            self.state.on_tx_telemetry(data)
            self.state.on_serial_telemetry(data)
            self.state.log_message(str(i))
        self.assertEqual(len(self.state.history), 200)
        self.assertEqual(len(self.state.event_logs), 100)
        self.assertEqual(len(self.state.comparison_history), 60)
        self.assertLessEqual(len(self.state.tx_cache), 256)
        self.handler.wfile = Mock()
        self.handler.send_response = Mock()
        self.handler.send_header = Mock()
        self.handler.end_headers = Mock()
        self.handler.handle_export_csv()
        self.assertEqual(self.handler.wfile.write.call_count, 61)
        self.assertIn(b'tx_patient', self.handler.wfile.write.call_args_list[0].args[0])

    def test_local_bind_and_no_wildcard_cors(self):
        self.assertEqual(inspect.signature(server.run_server).parameters['host'].default, '127.0.0.1')
        self.assertEqual(inspect.signature(server.udp_receiver_worker).parameters['host'].default, '127.0.0.1')
        self.assertIn('default="127.0.0.1"', (APP / 'start_monitor.py').read_text())
        self.assertNotIn('Access-Control-Allow-Origin', inspect.getsource(server))
        self.handler.path = '/api/reset'
        self.handler.headers = {'Origin': 'http://foreign.example', 'Host': 'localhost:8080'}
        self.handler.do_POST()
        self.assertEqual(self.handler.send_json_response.call_args.args[1], 403)

    def test_http_get_status_and_root_without_socket(self):
        self.handler.send_response = Mock()
        self.handler.send_header = Mock()
        self.handler.end_headers = Mock()
        self.handler.wfile = io.BytesIO()
        self.handler.path = '/'
        self.handler.do_GET()
        self.handler.send_response.assert_called_with(200)
        self.assertIn(b'benchmarkReference', self.handler.wfile.getvalue())
        self.handler.path = '/api/status'
        self.handler.do_GET()
        status = self.handler.send_json_response.call_args.args[0]
        self.assertIsNone(status['current_telemetry'])
        self.assertEqual(status['receiver_stats']['state'], 'waiting for simulation')
        self.assertEqual(self.post('/api/patient', {'patient': 4}), 200)
        self.assertEqual(self.post('/api/mode', {'mode': 'serial'}), 200)
        self.assertEqual(self.post('/api/patient', {'patient': 1}), 409)
        self.handler.path = '/api/status'
        self.handler.do_GET()
        self.assertEqual(self.handler.send_json_response.call_args.args[0]['receiver_stats']['state'], 'waiting for serial')

    def test_public_bind_warns_and_sim_period_configurable(self):
        fake_server = Mock()
        fake_server.serve_forever.side_effect = KeyboardInterrupt
        with patch.object(server, 'ThreadingHTTPServer', return_value=fake_server), patch.object(server.threading, 'Thread'), patch('builtins.print') as out:
            server.run_server(port=8099, host='0.0.0.0', sim_period=12)
        self.assertEqual(self.state.sim_period, 12)
        self.assertTrue(any('WARNING:' in str(call) for call in out.call_args_list))
        fake_server.server_close.assert_called_once()
        for period in (0, -1, float('nan')):
            with self.assertRaises(ValueError):
                server.run_server(sim_period=period)

    def test_reconnect_refuses_second_reader_when_old_thread_stuck(self):
        rx = SerialReceiver()
        rx.thread = Mock()
        rx.thread.is_alive.return_value = True
        with patch('serial_receiver.SERIAL_AVAILABLE', True), patch('serial_receiver.serial') as serial:
            self.assertFalse(rx.connect('/fake'))
            rx.thread.join.assert_called_once_with(timeout=2.0)
            serial.Serial.assert_not_called()

    def test_thresholds_exposed_from_shared_rules(self):
        import thresholds
        limits = self.state.get_receiver_stats()['thresholds']
        self.assertEqual(limits, dict(hr_low=thresholds.HR_LOW, hr_high=thresholds.HR_HIGH,
                                     spo2_low=thresholds.SPO2_LOW, temp_high_c=thresholds.TEMP_HIGH_C,
                                     imu_warning_states=[2, 3]))

    def test_ack_requires_matching_patient_episode_and_preserves_other_patients(self):
        self.ingest(hr=145)
        first = dict(self.state.active_alert)
        self.assertEqual(self.post('/api/alert', {'action': 'acknowledge'}), 400)
        self.ingest(seq=2, hr=145, spo2=90)
        second = dict(self.state.active_alert)
        self.assertEqual(self.post('/api/alert', dict(action='acknowledge', patient=1,
                                                     episode_id=first['episode_id'])), 409)
        self.assertFalse(self.state.active_alert['acknowledged'])
        self.ingest(patient=2, hr=145)
        self.assertEqual(self.post('/api/alert', dict(action='acknowledge', patient=1,
                                                     episode_id=second['episode_id'])), 200)
        self.assertTrue(self.state.alarms[1]['acknowledged'])
        self.assertFalse(self.state.active_alert['acknowledged'])
        self.assertEqual(self.post('/api/alert', dict(action='acknowledge', patient=2,
                           episode_id=self.state.active_alert['episode_id'])), 200)
        self.assertTrue(self.state.active_alert['acknowledged'])
        self.ingest(patient=2, seq=2)
        self.assertEqual(self.post('/api/alert', dict(action='acknowledge', patient=2,
                                                     episode_id=second['episode_id'])), 409)

    def test_simulator_imu_protocol_labels(self):
        expected = ['IMU state 0 — still', 'IMU state 1 — moving',
                    'IMU state 2 — fall flag', 'IMU state 3 — protocol flag']
        simulator = PatientSimulator()
        for patient in (1, 2, 4, 6):
            simulator.set_patient(patient)
            data = simulator.get_current_telemetry()
            self.assertEqual(data['imu_desc'], expected[data['imu']])

    def test_benchmark_measured_artifact(self):
        ref = server.load_benchmark_reference()
        self.assertEqual([(m['cpu_ms'], m['npu_ms']) for m in ref['models']], [(24.996, 3.019), (22.234, 2.901)])
        root = APP.parents[1]
        original = (root / ref['models'][0]['source']).read_text()
        self.assertIn('24.996 & 3.019', original)
        augmented = json.loads((root / ref['models'][1]['source']).read_text())
        self.assertEqual(round(augmented['timing']['cpu_full_us']['mean_us'] / 1000, 3), 22.234)
        self.assertEqual(round(augmented['timing']['npu_full_us']['mean_us'] / 1000, 3), 2.901)
        html = (APP / 'frontend/index.html').read_text()
        self.assertNotIn('48.50', html)
        self.assertNotIn('1.94', html)


if __name__ == '__main__':
    unittest.main()
