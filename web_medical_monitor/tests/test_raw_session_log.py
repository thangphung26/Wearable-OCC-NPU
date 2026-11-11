"""Raw archives tested with mock serial reads only; no devices or sockets."""
from datetime import datetime, timezone
import inspect
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "backend"))
import raw_session_log
from raw_session_log import RawSessionLog
from serial_receiver import SerialReceiver
import server


class RawSessionLogTests(unittest.TestCase):
    def read_lines(self, receiver, lines):
        serial = Mock(is_open=True)
        source = iter(lines)

        def readline(limit):
            self.assertEqual(limit, 16385)
            try:
                return next(source)
            except StopIteration:
                receiver.is_running = False
                return b""

        serial.readline.side_effect = readline
        receiver.ser = serial
        receiver.is_running = True
        receiver._read_loop()

    def test_off_by_default(self):
        receiver = SerialReceiver()
        self.assertIsNone(receiver.raw_logger)
        self.assertIsNone(inspect.signature(server.run_server).parameters["raw_log_dir"].default)
        with patch.object(raw_session_log.Path, "mkdir") as mkdir:
            self.read_lines(receiver, [b"startup\n", b"\n"])
        mkdir.assert_not_called()

    def test_files_order_decoding_parser_kinds_and_metadata(self):
        packet = dict(patient=1, hr=75, spo2=98, temp_c=37.5, imu=0,
                      battery=7, alert=0, seq=1)
        with tempfile.TemporaryDirectory() as root:
            logger = RawSessionLog(root)
            expected = {}
            for role, port in (("RX", "/mock/rx"), ("TX", "/mock/tx")):
                got = []
                receiver = SerialReceiver(role=role, on_telemetry=got.append)
                receiver.port_name = port
                receiver.raw_logger = logger
                logger.connection(role, port, 115200)
                prefix = "[TX_TELEMETRY] " if role == "TX" else "[TELEMETRY:NPU] "
                lines = [b" boot \xff\r\n", b"\n", (prefix + json.dumps(packet) + "\n").encode(),
                         (prefix + "{bad}\n").encode()]
                expected[role.lower()] = lines
                self.read_lines(receiver, lines)
                self.assertEqual(len(got), 1)
            logger.close()
            self.assertTrue(logger.enabled)
            self.assertEqual(sorted(p.name for p in logger.directory.iterdir()),
                             ["rx_raw.jsonl", "session_meta.json", "tx_raw.jsonl"])
            for role in ("rx", "tx"):
                rows = [json.loads(line) for line in (logger.directory / (role + "_raw.jsonl")).read_text().splitlines()]
                self.assertEqual([r["line"] for r in rows], [line.decode("utf-8", errors="replace") for line in expected[role]])
                self.assertEqual([r["kind"] for r in rows], ["ignored", "ignored", "accepted", "rejected"])
                self.assertTrue(all(r["port"] == "/mock/" + role for r in rows))
                timestamps = [r["host_time_ns"] for r in rows]
                self.assertTrue(all(isinstance(t, int) and t > 0 for t in timestamps))
                self.assertEqual(timestamps, sorted(timestamps))
            meta = json.loads((logger.directory / "session_meta.json").read_text())
            self.assertEqual(meta["line_counts"], {"RX": 4, "TX": 4})
            self.assertEqual(meta["ports"], {"RX": "/mock/rx", "TX": "/mock/tx"})
            self.assertEqual(meta["baud"], {"RX": 115200, "TX": 115200})
            self.assertGreaterEqual(meta["end_time_ns"], meta["start_time_ns"])
            self.assertIn("end_time", meta)
            import hashlib
            for filename, digest in meta["source_sha256"].items():
                self.assertEqual(digest, hashlib.sha256((APP / filename).read_bytes()).hexdigest())

    def test_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as root, patch.object(raw_session_log, "datetime") as clock:
            clock.now.return_value = datetime(2026, 11, 9, tzinfo=timezone.utc)
            first = RawSessionLog(root)
            first.close()
            before = {p.name: p.read_bytes() for p in first.directory.iterdir()}
            with self.assertRaises(FileExistsError):
                RawSessionLog(root)
            self.assertEqual(before, {p.name: p.read_bytes() for p in first.directory.iterdir()})

    def test_disk_error_disables_archive_without_affecting_parser(self):
        with tempfile.TemporaryDirectory() as root, patch("builtins.print") as warning:
            logger = RawSessionLog(root)
            stream = logger.streams["RX"]
            logger.streams["RX"] = Mock()
            logger.streams["RX"].write.side_effect = OSError("disk full")
            receiver = SerialReceiver(on_status=Mock())
            receiver.raw_logger = logger
            self.read_lines(receiver, [b"[STATUS] frames=1\n", b"[STATUS] frames=2\n"])
            logger.close()
            stream.close()
            self.assertFalse(logger.enabled)
            self.assertEqual(receiver.last_stats["frames"], 2)
            self.assertTrue(any("WARNING: Raw serial logging disabled" in str(c) for c in warning.call_args_list))

    def test_slow_disk_does_not_block_reader_and_each_write_is_flushed(self):
        with tempfile.TemporaryDirectory() as root:
            logger = RawSessionLog(root)
            stream = logger.streams["RX"]
            entered, release = threading.Event(), threading.Event()
            written = threading.Event()
            proxy = Mock()

            def write(data):
                entered.set()
                release.wait(2)
                result = stream.write(data)
                written.set()
                return result

            proxy.write.side_effect = write
            logger.streams["RX"] = proxy
            try:
                receiver = SerialReceiver(on_status=Mock())
                receiver.raw_logger = logger
                self.read_lines(receiver, [b"[STATUS] frames=3\n"])
                self.assertTrue(entered.wait(1))
                self.assertEqual(receiver.last_stats["frames"], 3)
                release.set()
                self.assertTrue(written.wait(1))
                self.assertEqual(json.loads((logger.directory / "rx_raw.jsonl").read_text())["line"],
                                 "[STATUS] frames=3\n")
            finally:
                release.set()
                logger.close()
                stream.close()

    def test_server_closes_readers_before_archive(self):
        state = server.SystemState()
        with tempfile.TemporaryDirectory() as root, patch.object(server, "GLOBAL_STATE", state), patch.object(
                server, "_run_server") as serve:
            def run(*args):
                self.assertIsNotNone(state.serial_receiver.raw_logger)
                self.assertIs(state.serial_receiver.raw_logger, state.tx_receiver.raw_logger)
            serve.side_effect = run
            server.run_server(raw_log_dir=root)
            self.assertIsNone(state.serial_receiver.raw_logger)
            meta = json.loads(next(Path(root).glob("*/session_meta.json")).read_text())
            self.assertEqual(meta["line_counts"], {"RX": 0, "TX": 0})

    def test_startup_disk_error_warns_and_server_still_runs(self):
        with patch.object(server, "RawSessionLog", side_effect=OSError("read-only disk")), patch.object(
                server, "_run_server") as serve, patch("builtins.print") as warning:
            server.run_server(raw_log_dir="unused")
        serve.assert_called_once_with(8080, "127.0.0.1", 10.0)
        self.assertTrue(any("WARNING: Raw serial logging disabled" in str(c) for c in warning.call_args_list))

    def test_cli_flag_defaults_off_and_forwards_directory(self):
        sys.path.insert(0, str(APP))
        import start_monitor
        for extra, expected in (([], None), (["--raw-log-dir", "archive"], "archive")):
            with patch.object(sys, "argv", ["start_monitor.py", *extra]), patch.object(
                    start_monitor, "run_server") as run, patch("builtins.print"):
                start_monitor.main()
            self.assertEqual(run.call_args.kwargs["raw_log_dir"], expected)


if __name__ == "__main__":
    unittest.main()
