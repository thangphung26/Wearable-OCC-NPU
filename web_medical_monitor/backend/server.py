"""
High-performance, Zero-Dependency Python HTTP & Server-Sent Events (SSE) Web Server.
Serves the Medical Patient Monitoring Web Dashboard and streams real-time VLC NPU telemetry.
"""

import csv
import io
import math
import json
import os
import queue
import re
import socket
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from typing import Dict, Any, List, Set, Optional

from patient_simulator import PatientSimulator, DATASET_PROFILES
from serial_receiver import SerialReceiver, list_available_ports, SERIAL_AVAILABLE
from raw_session_log import RawSessionLog

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
STALE_AFTER_SEC = 25.0
MAX_JSON_BODY_BYTES = 16 * 1024


def finite_json_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite JSON number")
    return number


from thresholds import (validate_telemetry, strict_int, strict_bool, recovery_available,
                        HR_LOW, HR_HIGH, SPO2_LOW, TEMP_HIGH_C)

TX_MAX_AGE_SEC = 25.0
COMPARED_FIELDS = ("patient", "seq", "hr", "spo2", "temp_c", "imu", "battery", "alert")


def frame_bytes(frame_hex):
    if not isinstance(frame_hex, str) or not re.fullmatch(r"(?:0[xX])?[0-9a-fA-F]{12}", frame_hex):
        return None
    return bytes.fromhex(frame_hex[-12:])


def host_crc_status(frame_hex):
    frame = frame_bytes(frame_hex)
    if frame is None:
        return "unverified"
    crc = 0
    for byte in frame[:5]:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ (0x07 if crc & 0x80 else 0)) & 0xFF
    return "verified-by-host" if crc == frame[5] else "rejected"


def compare_records(tx, rx):
    fields_match = all(rx[field] == tx[field] for field in COMPARED_FIELDS)
    tx_frame, rx_frame = frame_bytes(tx.get("frame_hex")), frame_bytes(rx.get("frame_hex"))
    basis = "frame_hex" if tx.get("frame_hex") and rx.get("frame_hex") else "fields"
    match = (tx_frame is not None and rx_frame is not None and tx_frame == rx_frame) if basis == "frame_hex" else fields_match
    return {"is_match": match, "match_basis": basis, "fields_match": fields_match,
            "field_mismatches": [field for field in COMPARED_FIELDS if rx[field] != tx[field]]}


def rx_accounting(rx, received_at, candidates, connected):
    """A CRC pass alone is not evidence that TX transmitted this frame."""
    frame = frame_bytes(rx.get("frame_hex"))
    covered = [(seen, tx) for seen, tx in candidates
               if abs(seen - received_at) <= TX_MAX_AGE_SEC]
    if frame is None or not connected or not covered:
        return {"tx_match": None, "match_basis": None}
    matched = any(frame_bytes(tx.get("frame_hex")) == frame for _, tx in covered)
    return {"tx_match": matched, "match_basis": "frame_hex",
            "tx_match_status": "bit-exact frame match" if matched else "no TX frame"}


def load_replay(run_name):
    if not re.fullmatch(r"[A-Za-z0-9_]+", run_name):
        raise ValueError("Invalid replay name")
    root = os.path.realpath(os.path.join(BASE_DIR, "../results/real_sensor_demo"))
    folder = os.path.realpath(os.path.join(root, run_name))
    if os.path.dirname(folder) != root:
        raise ValueError("Invalid replay path")
    result = {}
    for name in ("history", "status", "comparison"):
        with open(os.path.join(folder, name + ".json"), encoding="utf-8") as stream:
            result[name] = json.load(stream)
    # Preserve all three recorded artifacts; derived annotations live alongside them.
    comparison = result["comparison"]
    txs = [item["tx"] for item in comparison["history"] if item.get("tx")]
    if comparison["stats"].get("latest_tx"):
        txs.append(comparison["stats"]["latest_tx"])
    candidates = [(tx["timestamp_ms"] / 1000, tx) for tx in txs]
    annotated = [dict(rx, **rx_accounting(rx, rx["timestamp_ms"] / 1000, candidates,
                    comparison["stats"].get("tx_connected", False))) for rx in result["history"]]
    result["accounting"] = {"history": annotated, "stats": {
        "rx_bit_exact": sum(r["tx_match"] is True for r in annotated),
        "rx_unmatched": sum(r["tx_match"] is False for r in annotated),
        "rx_unchecked": sum(r["tx_match"] is None for r in annotated)}}
    return result


def load_benchmark_reference():
    with open(os.path.join(BASE_DIR, "benchmark_reference.json"), encoding="utf-8") as stream:
        return json.load(stream)


class SystemState:
    def __init__(self):
        self.lock = threading.RLock()
        self.connection_lock = threading.RLock()
        self.sim_period = 10.0
        self.mode = "simulation"  # simulation, serial, wifi, or api
        self.simulator = PatientSimulator()
        self.serial_receiver = SerialReceiver(
            on_telemetry=self.on_serial_telemetry,
            on_status=self.on_serial_status,
            on_log=self.log_message,
            on_connection=self.on_serial_connection,
            role="RX"
        )
        self.tx_receiver = SerialReceiver(
            on_telemetry=self.on_tx_telemetry,
            on_log=self.log_message,
            on_connection=self.on_tx_connection,
            role="TX"
        )
        self.tx_cache = {}
        self.rx_pending = []
        self.comparison_history: List[Dict[str, Any]] = []
        self.comparison_stats: Dict[str, Any] = {
            "rx_bit_exact": 0, "rx_unmatched": 0, "rx_unchecked": 0,
            "total_compared": 0,
            "total_matched": 0,
            "accuracy_pct": None,
            "compared_fields": list(COMPARED_FIELDS),
            "tx_max_age_s": TX_MAX_AGE_SEC,
            "tx_port": None,
            "rx_port": None,
            "tx_connected": False,
            "rx_connected": False,
            "latest_tx": None,
            "latest_rx": None,
            "latest_match": None
        }
        self.history: List[Dict[str, Any]] = []
        self.max_history = 200
        self.sse_clients: Set[queue.Queue] = set()
        self.event_logs: List[Dict[str, Any]] = []
        self.max_logs = 100

        self.active_alert = {
            "active": False,
            "acknowledged": False,
            "patient": 1,
            "reasons": [],
            "timestamp": "",
            "timestamp_ms": 0
        }
        self.current_telemetry = None
        self.last_telemetry_monotonic = None
        self.last_source = "simulation"
        self.last_sequences = {}
        self.alarms = {}
        self.episode_id = 0
        self.receiver_stats = {"valid": 0, "rejected_crc": 0, "duplicate_seq": 0,
                               "invalid_packets": 0, "connected_port": None,
                               "counters_since_ms": int(time.time() * 1000)}
        self.hardware_stats = {}

    def set_mode(self, mode, force=False):
        with self.lock:
            if mode == self.mode and not force:
                return
            self.mode = mode
            for key in ("valid", "rejected_crc", "duplicate_seq", "invalid_packets"):
                self.receiver_stats[key] = 0
            self.receiver_stats["counters_since_ms"] = int(time.time() * 1000)
            self.current_telemetry = None
            self.last_telemetry_monotonic = None
            self.last_source = mode
            self.last_sequences.clear()
            self.tx_cache.clear()
            self.rx_pending.clear()
            for key in ("rx_bit_exact", "rx_unmatched", "rx_unchecked"):
                self.comparison_stats[key] = 0
            self.hardware_stats.clear()
            self.active_alert = {"active": False, "acknowledged": False, "patient": None,
                                 "reasons": [], "timestamp": "", "timestamp_ms": 0}
            self.alarms.clear()
            self.broadcast_sse("mode", {"mode": mode})
            self.broadcast_sse("alert", self.active_alert)
            self.broadcast_sse("receiver_status", self.get_receiver_stats())

    def simulate_once(self):
        with self.lock:
            if self.mode != "simulation":
                return False
            return self.process_telemetry(self.simulator.get_current_telemetry(), "simulation")

    def register_client(self) -> queue.Queue:
        q = queue.Queue(maxsize=100)
        with self.lock:
            self.sse_clients.add(q)
        return q

    def unregister_client(self, q: queue.Queue):
        with self.lock:
            self.sse_clients.discard(q)

    def broadcast_sse(self, event_type: str, data: Any):
        payload = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
        dead_clients = []
        with self.lock:
            for q in self.sse_clients:
                try:
                    q.put_nowait(payload)
                except queue.Full:
                    dead_clients.append(q)
            for q in dead_clients:
                self.sse_clients.discard(q)

    def log_message(self, text: str, level: str = "info"):
        log_entry = {
            "time": time.strftime("%H:%M:%S"),
            "timestamp_ms": int(time.time() * 1000),
            "level": level,
            "text": text
        }
        with self.lock:
            self.event_logs.append(log_entry)
            if len(self.event_logs) > self.max_logs:
                self.event_logs.pop(0)
        self.broadcast_sse("log", log_entry)

    def process_telemetry(self, telemetry, source="unknown"):
        with self.lock:
            if source != self.mode:
                return False
            # Explicit CRC failure wins even if other fields are malformed.
            if isinstance(telemetry, dict) and (telemetry.get("crc_valid") is False or
                    source == "serial" and "crc_valid" not in telemetry and
                    host_crc_status(telemetry.get("frame_hex")) == "rejected"):
                self.receiver_stats["rejected_crc"] += 1
                self.broadcast_sse("receiver_status", self.get_receiver_stats())
                return False
            valid, error, clean = validate_telemetry(telemetry)
            if not valid:
                self.receiver_stats["invalid_packets"] += 1
                return False
            key = (source, clean["patient"])
            if self.last_sequences.get(key) == clean["seq"]:
                self.receiver_stats["duplicate_seq"] += 1
                self.broadcast_sse("receiver_status", self.get_receiver_stats())
                return False
            self.last_sequences[key] = clean["seq"]
            self.receiver_stats["valid"] += 1
            clean["timestamp"] = time.strftime("%H:%M:%S")
            clean["timestamp_ms"] = int(time.time() * 1000)
            clean.setdefault("patient_name", f"Patient #{clean['patient']}")
            clean.setdefault("decoder", "simulated" if source == "simulation" else source.upper())
            clean["source"] = source
            if source == "simulation":
                clean["decoder"] = "simulated"
                for metric in ("fps", "acquisition_fps", "roi_box", "signal_amplitude", "crc_valid"):
                    clean.pop(metric, None)
                clean["crc_status"] = "unverified"
            elif source == "serial" and clean["crc_status"] == "unverified":
                clean["crc_status"] = host_crc_status(clean.get("frame_hex"))
            self.last_telemetry_monotonic = time.monotonic()
            self.last_source = source
            clean["tx_match"] = None
            clean["match_basis"] = None
            if source == "serial":
                received_at = time.monotonic()
                if self.comparison_stats["tx_connected"]:
                    self.rx_pending.append((received_at, clean))
                self.comparison_stats["rx_unchecked"] += 1
                self.update_rx_accounting(received_at)
            self.current_telemetry = clean
            self.history.append(clean)
            del self.history[:-self.max_history]
            patient = clean["patient"]
            previous = self.alarms.get(patient, {"active": False})
            reasons = clean["alert_reasons"]
            # Missing readings preserve the unresolved episode, including acknowledgment.
            if not recovery_available(clean) and previous["active"]:
                reasons = sorted(set(reasons) | set(previous["reasons"]))
            if reasons:
                if (not previous["active"] or set(previous["reasons"]) != set(reasons)
                        or self.active_alert.get("patient") != patient):
                    self.episode_id += 1
                    previous = {"active": True, "acknowledged": False, "patient": patient,
                                "reasons": reasons, "timestamp": clean["timestamp"],
                                "timestamp_ms": clean["timestamp_ms"], "episode_id": self.episode_id}
            elif recovery_available(clean):
                previous = {"active": False, "acknowledged": False, "patient": patient,
                            "reasons": [], "timestamp": "", "timestamp_ms": 0}
            else:
                previous = {"active": False, "acknowledged": False, "patient": patient,
                            "reasons": [], "timestamp": "", "timestamp_ms": 0}
            self.alarms[patient] = previous
            self.active_alert = previous
            self.broadcast_sse("telemetry", clean)
            self.broadcast_sse("alert", self.active_alert)
            self.broadcast_sse("receiver_status", self.get_receiver_stats())
            return True

    def get_receiver_stats(self):
        with self.lock:
            stats = dict(self.receiver_stats)
            age = None if self.last_telemetry_monotonic is None else max(0, time.monotonic() - self.last_telemetry_monotonic)
            if self.mode == "serial":
                stats.update(self.hardware_stats)
            stats.update({
                "valid_packets": stats["valid"], "duplicate_packets": stats["duplicate_seq"],
                "source": self.last_source, "data_age_sec": round(age, 1) if age is not None else None,
                "stale": age is not None and age >= STALE_AFTER_SEC,
                "state": f"waiting for {self.mode}" if age is None else ("stale" if age >= STALE_AFTER_SEC else "current"),
                "acquisition_fps": self.hardware_stats.get("acquisition_fps") if self.mode == "serial" else None,
                "thresholds": {"hr_low": HR_LOW, "hr_high": HR_HIGH, "spo2_low": SPO2_LOW,
                               "temp_high_c": TEMP_HIGH_C, "imu_warning_states": [2, 3]},
                "resampling_hz": 20, "link_bit_s": 5, "snapshot_period_s": self.sim_period if self.mode == "simulation" else 10.0,
                "serial_connected": self.serial_receiver.is_connected(),
                "tx_connected": self.tx_receiver.is_connected(), "comparison_stats": dict(self.comparison_stats),
            })
            return stats

    def update_rx_accounting(self, now):
        keys = {True: "rx_bit_exact", False: "rx_unmatched", None: "rx_unchecked"}
        for received_at, rx in self.rx_pending:
            result = rx_accounting(rx, received_at, self.tx_cache.values(),
                                   self.comparison_stats["tx_connected"])
            old, new = rx.get("tx_match"), result["tx_match"]
            # Never lose established evidence when old TX entries leave the cache.
            if old is True or new is None:
                continue
            if old is not new:
                self.comparison_stats[keys[old]] -= 1
                self.comparison_stats[keys[new]] += 1
            rx.update(result)
        self.rx_pending = [(seen, rx) for seen, rx in self.rx_pending
                           if now - seen <= TX_MAX_AGE_SEC]

    def on_tx_telemetry(self, data):
        valid, _, data = validate_telemetry(data)
        if not valid or data.get("crc_valid") is False:
            return
        # Older TX ground-truth logs encode this flag as 0/1; expose a JSON boolean.
        if "imu_valid" in data:
            flag = data["imu_valid"]
            if type(flag) is bool or type(flag) is int and flag in (0, 1):
                data["imu_valid"] = bool(flag)
            else:
                data.pop("imu_valid")
        with self.lock:
            now = time.monotonic()
            self.tx_cache = {k: v for k, v in self.tx_cache.items() if now - v[0] <= TX_MAX_AGE_SEC}
            self.tx_cache[(data["patient"], data["seq"])] = (now, data)
            self.comparison_stats["latest_tx"] = data
            self.update_rx_accounting(now)
        self.broadcast_sse("tx_telemetry", data)
        self.broadcast_sse("comparison_stats", self.comparison_stats)

    def on_tx_connection(self, connected: bool, port: Optional[str]):
        with self.lock:
            self.tx_cache.clear()
            self.rx_pending.clear()
            self.comparison_stats["tx_port"] = port if connected else None
            self.comparison_stats["tx_connected"] = connected
        self.broadcast_sse("receiver_status", self.get_receiver_stats())
        self.broadcast_sse("comparison_stats", self.comparison_stats)

    def on_serial_telemetry(self, data):
        with self.lock:
            if self.mode != "serial" or not self.process_telemetry(data, "serial"):
                return
            data = self.current_telemetry
            self.comparison_stats["latest_rx"] = data
            if data.get("tx_match") is False:
                item = {"timestamp": data["timestamp"], "seq": data["seq"], "patient": data["patient"],
                        "is_match": False, "match_basis": "frame_hex", "status": "no TX frame",
                        "tx": None, "rx": data, "compared_fields": list(COMPARED_FIELDS)}
                self.comparison_stats["latest_match"] = item
                self.comparison_stats["match_basis"] = "frame_hex"
                self.comparison_history.append(item)
                del self.comparison_history[:-60]
                self.broadcast_sse("comparison", item)
                self.broadcast_sse("comparison_stats", self.comparison_stats)
                return
            cached = next(((seen, tx) for seen, tx in self.tx_cache.values()
                           if frame_bytes(data.get("frame_hex")) is not None
                           and frame_bytes(tx.get("frame_hex")) == frame_bytes(data.get("frame_hex"))
                           and time.monotonic() - seen <= TX_MAX_AGE_SEC), None)
            if cached is None:
                cached = self.tx_cache.get((data["patient"], data["seq"]))
            if cached is None or time.monotonic() - cached[0] > TX_MAX_AGE_SEC:
                return
            tx = cached[1]
            result = compare_records(tx, data)
            match = result["is_match"]
            self.comparison_stats["total_compared"] += 1
            self.comparison_stats["total_matched"] += int(match)
            pct = round(100 * self.comparison_stats["total_matched"] / self.comparison_stats["total_compared"], 1)
            self.comparison_stats["accuracy_pct"] = pct
            self.comparison_stats["match_basis"] = result["match_basis"]
            item = {"timestamp": data["timestamp"], "seq": data["seq"], "patient": data["patient"],
                    **result, "accuracy_pct": pct, "tx": tx, "rx": data,
                    "compared_fields": list(COMPARED_FIELDS),
                    "status": ("bit-exact frame" if result["match_basis"] == "frame_hex" else "field") + (" match" if match else " mismatch")}
            self.comparison_stats["latest_match"] = item
            self.comparison_history.append(item)
            del self.comparison_history[:-60]
            self.broadcast_sse("comparison", item)
            self.broadcast_sse("comparison_stats", self.comparison_stats)

    def on_serial_status(self, stats):
        with self.lock:
            # Device totals have a different lifetime from dashboard ingestion counters.
            self.hardware_stats.update({"receiver_" + k if k in ("valid_packets", "rejected_crc") else k: v
                                        for k, v in stats.items()})
        self.broadcast_sse("receiver_status", self.get_receiver_stats())

    def on_serial_connection(self, connected: bool, port: Optional[str]):
        with self.lock:
            self.receiver_stats["connected_port"] = port if connected else None
            self.comparison_stats["rx_port"] = port if connected else None
            self.comparison_stats["rx_connected"] = connected
        self.broadcast_sse("receiver_status", self.get_receiver_stats())
        self.broadcast_sse("comparison_stats", self.comparison_stats)


GLOBAL_STATE = SystemState()


class MedicalMonitorHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress default noisy access logs to terminal
        pass

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self.serve_file(os.path.join(FRONTEND_DIR, "index.html"), "text/html; charset=utf-8")
        elif path.startswith("/static/"):
            rel_path = path[len("/static/"):]
            self.serve_static(rel_path)
        elif path in ("/styles.css", "/charts.js", "/app.js"):
            self.serve_static(path.lstrip("/"))
        elif path == "/api/stream":
            self.handle_sse()
        elif path == "/api/status":
            self.handle_get_status()
        elif path == "/api/history":
            self.handle_get_history()
        elif path.startswith("/api/replay/"):
            try:
                self.send_json_response(load_replay(path[len("/api/replay/"):]))
            except ValueError as exc:
                self.send_json_response({"message": str(exc)}, 400)
            except (OSError, json.JSONDecodeError):
                self.send_json_response({"message": "Replay not found or unreadable"}, 404)
        elif path == "/api/comparison":
            self.handle_get_comparison()
        elif path == "/api/export_csv":
            self.handle_export_csv()
        elif path == "/api/ports":
            self.handle_get_ports()
        elif path == "/api/benchmark":
            self.send_json_response(load_benchmark_reference())
        elif path == "/api/profiles":
            self.handle_get_profiles()
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Resource not found")

    def do_POST(self):
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            self.send_json_response({"status": "error", "message": "Same-origin requests only"}, 403)
            return
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            body = self.read_json_body()
        except ValueError as exc:
            self.send_json_response({"status": "error", "message": str(exc)}, getattr(exc, "status", 400))
            return
        try:
            self.dispatch_post(path, body)
        except (TypeError, ValueError) as exc:
            self.send_json_response({"status": "error", "message": str(exc)}, 400)

    def dispatch_post(self, path, body):

        if path == "/api/telemetry":
            self.handle_post_telemetry(body)
        elif path == "/api/mode":
            self.handle_post_mode(body)
        elif path == "/api/patient":
            self.handle_post_patient(body)
        elif path == "/api/alert":
            self.handle_post_alert(body)
        elif path == "/api/connect":
            self.handle_post_connect(body)
        elif path == "/api/connect_dual":
            self.handle_post_connect_dual(body)
        elif path == "/api/disconnect":
            self.handle_post_disconnect(body)
        elif path == "/api/disconnect_tx":
            self.handle_post_disconnect_tx(body)
        elif path == "/api/reset":
            self.handle_post_reset()
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Endpoint not found")

    def read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > MAX_JSON_BODY_BYTES:
                exc = ValueError("JSON body too large")
                exc.status = 413
                raise exc
            if length <= 0:
                raise ValueError("JSON object required")
            raw = self.rfile.read(length)
            body = json.loads(raw.decode("utf-8"), parse_float=finite_json_float, parse_constant=lambda x: (_ for _ in ()).throw(ValueError("Non-finite JSON")))
            if not isinstance(body, dict):
                raise ValueError("JSON object required")
            return body
        except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
            raise ValueError("Malformed JSON") from exc

    def send_json_response(self, data: Any, status: int = HTTPStatus.OK):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_static(self, rel_path: str):
        # Prevent directory traversal
        safe_path = os.path.realpath(os.path.join(FRONTEND_DIR, rel_path))
        if os.path.commonpath((FRONTEND_DIR, safe_path)) != FRONTEND_DIR or not os.path.isfile(safe_path):
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return

        ext = os.path.splitext(safe_path)[1].lower()
        mime_types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".ico": "image/x-icon",
        }
        content_type = mime_types.get(ext, "application/octet-stream")
        self.serve_file(safe_path, content_type)

    def serve_file(self, filepath: str, content_type: str):
        try:
            with open(filepath, "rb") as f:
                content = f.read()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, f"Error reading file: {e}")

    def handle_sse(self):
        """Server-Sent Events handler with instant initial sync."""
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        client_queue = GLOBAL_STATE.register_client()

        # Send initial snapshot
        init_data = {
            "mode": GLOBAL_STATE.mode,
            "telemetry": GLOBAL_STATE.current_telemetry,
            "alert": GLOBAL_STATE.active_alert,
            "alarms": dict(GLOBAL_STATE.alarms),
            "stats": GLOBAL_STATE.get_receiver_stats(),
            "serial_available": SERIAL_AVAILABLE,
            "serial_connected": GLOBAL_STATE.serial_receiver.is_connected()
        }
        init_payload = f"event: init\ndata: {json.dumps(init_data)}\n\n"
        try:
            self.wfile.write(init_payload.encode("utf-8"))
            self.wfile.flush()
        except Exception:
            GLOBAL_STATE.unregister_client(client_queue)
            return

        try:
            while True:
                try:
                    msg = client_queue.get(timeout=20.0)
                    self.wfile.write(msg.encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    # Keep-alive comment
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            GLOBAL_STATE.unregister_client(client_queue)

    def handle_get_status(self):
        data = {
            "mode": GLOBAL_STATE.mode,
            "current_telemetry": GLOBAL_STATE.current_telemetry,
            "alert": GLOBAL_STATE.active_alert,
            "alarms": dict(GLOBAL_STATE.alarms),
            "receiver_stats": GLOBAL_STATE.get_receiver_stats(),
            "serial_available": SERIAL_AVAILABLE,
            "serial_connected": GLOBAL_STATE.serial_receiver.is_connected(),
            "connected_port": GLOBAL_STATE.serial_receiver.port_name
        }
        self.send_json_response(data)

    def handle_get_history(self):
        with GLOBAL_STATE.lock:
            query = parse_qs(urlparse(self.path).query)
            patient = query.get("patient", [None])[0]
            source = query.get("source", [None])[0]
            history_copy = [row for row in GLOBAL_STATE.history
                            if (patient is None or str(row["patient"]) == patient)
                            and (source is None or row["source"] == source)]
        self.send_json_response(history_copy)

    def handle_get_comparison(self):
        with GLOBAL_STATE.lock:
            stats = dict(GLOBAL_STATE.comparison_stats)
            history = list(GLOBAL_STATE.comparison_history)
        self.send_json_response({
            "stats": stats,
            "history": history
        })

    def handle_export_csv(self):
        # Export the bounded current-session comparison buffer row by row.
        with GLOBAL_STATE.lock:
            rows = list(GLOBAL_STATE.comparison_history)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", 'attachment; filename="vlc_experiment_comparison.csv"')
        self.end_headers()
        def write_row(row):
            buffer = io.StringIO()
            csv.writer(buffer).writerow(row)
            self.wfile.write(buffer.getvalue().encode("utf-8"))
        write_row(["timestamp", "patient", "seq", "status"] + [side + "_" + field for side in ("tx", "rx") for field in COMPARED_FIELDS])
        for item in rows:
            write_row([item["timestamp"], item["patient"], item["seq"], item["status"]] +
                      [(item.get(side) or {}).get(field, "") for side in ("tx", "rx") for field in COMPARED_FIELDS])

    def handle_get_ports(self):
        ports = list_available_ports()
        self.send_json_response({
            "ports": ports,
            "serial_available": SERIAL_AVAILABLE,
            "connected_port": GLOBAL_STATE.serial_receiver.port_name,
            "is_connected": GLOBAL_STATE.serial_receiver.is_connected(),
            "tx_connected_port": GLOBAL_STATE.tx_receiver.port_name,
            "tx_is_connected": GLOBAL_STATE.tx_receiver.is_connected()
        })

    def handle_get_profiles(self):
        self.send_json_response(DATASET_PROFILES)

    def handle_post_telemetry(self, body: Dict[str, Any]):
        if not body:
            self.send_json_response({"status": "error", "message": "Empty body"}, HTTPStatus.BAD_REQUEST)
            return

        if body.get("crc_valid") is False:
            with GLOBAL_STATE.lock:
                GLOBAL_STATE.process_telemetry(body, "api")
            self.send_json_response({"status": "error", "message": "CRC rejected"}, 400)
            return
        valid, error, clean = validate_telemetry(body)
        if not valid:
            self.send_json_response({"status": "error", "message": error}, 400)
            return
        with GLOBAL_STATE.lock:
            GLOBAL_STATE.set_mode("api")
            accepted = GLOBAL_STATE.process_telemetry(clean, "api")
        self.send_json_response({"status": "ok", "accepted": accepted, "received": GLOBAL_STATE.current_telemetry})

    def handle_post_mode(self, body):
        new_mode = body.get("mode")
        if new_mode not in ("simulation", "serial", "wifi", "api"):
            self.send_json_response({"status": "error", "message": "Invalid mode"}, 400)
            return
        GLOBAL_STATE.set_mode(new_mode)
        self.send_json_response({"status": "ok", "mode": new_mode})

    def handle_post_patient(self, body):
        with GLOBAL_STATE.lock:
            if GLOBAL_STATE.mode != "simulation":
                self.send_json_response({"status": "error", "message": "Simulation mode required"}, 409)
                return
            patient = strict_int(body.get("patient", 1))
            auto_cycle = strict_bool(body.get("auto_cycle", False))
            if not GLOBAL_STATE.simulator.set_patient(patient):
                self.send_json_response({"status": "error", "message": "Patient not found"}, 400)
                return
            GLOBAL_STATE.simulator.auto_cycle = auto_cycle
            GLOBAL_STATE.simulate_once()
        self.send_json_response({"status": "ok", "patient": patient})

    def handle_post_alert(self, body):
        action = body.get("action")
        with GLOBAL_STATE.lock:
            if action in ("trigger", "clear"):
                if GLOBAL_STATE.mode != "simulation":
                    self.send_json_response({"status": "error", "message": "Simulation mode required"}, 409)
                    return
                if action == "trigger":
                    GLOBAL_STATE.simulator.trigger_alert()
                else:
                    GLOBAL_STATE.simulator.clear_alert()
                GLOBAL_STATE.simulate_once()
            elif action == "acknowledge":
                patient = strict_int(body.get("patient"))
                episode_id = strict_int(body.get("episode_id"))
                episode = GLOBAL_STATE.alarms.get(patient)
                if not episode or not episode["active"] or episode.get("episode_id") != episode_id:
                    self.send_json_response({"status": "error", "message": "Alarm episode changed or resolved"}, 409)
                    return
                episode["acknowledged"] = True
                GLOBAL_STATE.broadcast_sse("alert", episode)
            else:
                self.send_json_response({"status": "error", "message": "Invalid action"}, 400)
                return
        self.send_json_response({"status": "ok", "action": action})

    @staticmethod
    def validate_connection(port, baud):
        if not isinstance(port, str) or not port or len(port) > 256 or not 1 <= baud <= 4000000:
            raise ValueError("Invalid serial port or baud rate")

    def handle_post_connect(self, body: Dict[str, Any]):
        with GLOBAL_STATE.connection_lock:
            return self._post_connect(body)

    def _post_connect(self, body):
        port = body.get("port", "/dev/ttyACM0")
        baud = strict_int(body.get("baudrate", 115200))
        self.validate_connection(port, baud)
        success = GLOBAL_STATE.serial_receiver.connect(port, baud)
        if success:
            GLOBAL_STATE.receiver_stats["connected_port"] = port
            GLOBAL_STATE.set_mode("serial", force=True)
            self.send_json_response({"status": "ok", "connected": True, "port": port})
        else:
            self.send_json_response({
                "status": "error",
                "connected": False,
                "message": "Failed to connect serial port. Check USB cable or permissions."
            }, HTTPStatus.SERVICE_UNAVAILABLE)

    def handle_post_disconnect(self, body: Dict[str, Any]):
        with GLOBAL_STATE.connection_lock:
            return self._post_disconnect(body)

    def _post_disconnect(self, body):
        GLOBAL_STATE.serial_receiver.disconnect()
        GLOBAL_STATE.receiver_stats["connected_port"] = None
        self.send_json_response({"status": "ok", "connected": False})

    def handle_post_connect_dual(self, body: Dict[str, Any]):
        with GLOBAL_STATE.connection_lock:
            return self._post_connect_dual(body)

    def _post_connect_dual(self, body):
        rx_port = body.get("rx_port")
        tx_port = body.get("tx_port", "/dev/ttyACM0")
        rx_baud = strict_int(body.get("rx_baud", 115200))
        tx_baud = strict_int(body.get("tx_baud", 115200))

        self.validate_connection(tx_port, tx_baud)
        if rx_port is not None:
            self.validate_connection(rx_port, rx_baud)
        rx_ok = GLOBAL_STATE.serial_receiver.connect(rx_port, rx_baud) if rx_port else GLOBAL_STATE.serial_receiver.is_connected()
        tx_ok = GLOBAL_STATE.tx_receiver.connect(tx_port, tx_baud)

        if rx_ok and rx_port:
            GLOBAL_STATE.receiver_stats["connected_port"] = rx_port
            GLOBAL_STATE.set_mode("serial", force=True)

        res = {
            "status": "ok" if (rx_ok or tx_ok) else "error",
            "rx_connected": rx_ok,
            "rx_port": rx_port,
            "tx_connected": tx_ok,
            "tx_port": tx_port,
            "message": f"RX {'connected' if rx_ok else 'failed'}, TX {'connected' if tx_ok else 'failed'}"
        }
        status_code = HTTPStatus.OK if (rx_ok or tx_ok) else HTTPStatus.SERVICE_UNAVAILABLE
        self.send_json_response(res, status_code)

    def handle_post_disconnect_tx(self, body: Dict[str, Any]):
        with GLOBAL_STATE.connection_lock:
            return self._post_disconnect_tx(body)

    def _post_disconnect_tx(self, body):
        GLOBAL_STATE.tx_receiver.disconnect()
        with GLOBAL_STATE.lock:
            GLOBAL_STATE.comparison_stats["tx_port"] = None
            GLOBAL_STATE.comparison_stats["tx_connected"] = False
        self.send_json_response({"status": "ok", "tx_connected": False})

    def handle_post_reset(self):
        with GLOBAL_STATE.lock:
            GLOBAL_STATE.history.clear()
            GLOBAL_STATE.event_logs.clear()
        GLOBAL_STATE.log_message("🧹 Telemetry history and event logs cleared.")
        self.send_json_response({"status": "ok"})


def background_worker():
    """Only snapshots and status are streamed; no synthesized waveform."""
    last = None
    while True:
        now = time.monotonic()
        if GLOBAL_STATE.mode == "simulation" and (last is None or now - last >= GLOBAL_STATE.sim_period):
            GLOBAL_STATE.simulate_once()
            last = now
        GLOBAL_STATE.broadcast_sse("receiver_status", GLOBAL_STATE.get_receiver_stats())
        time.sleep(0.5)


def udp_receiver_worker(udp_port: int = 8082, host: str = "127.0.0.1"):
    """
    Lightweight, non-blocking UDP receiver for AmebaPro2 camera WiFi telemetry.
    Allows Ameba to send a 1-line UDP broadcast/unicast packet in <1ms without dropping video frames.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((host, udp_port))
        GLOBAL_STATE.log_message(f"📡 WiFi UDP receiver listening for AmebaPro2 telemetry on port {udp_port}")
        while True:
            data, addr = sock.recvfrom(2048)
            text = data.decode("utf-8", errors="replace").strip()
            try:
                m = re.search(r"(\{.*\})", text)
                if m:
                    payload = json.loads(m.group(1))
                    if not isinstance(payload, dict):
                        continue
                    payload.setdefault("decoder", "Ameba_WiFi_NPU")
                    payload.setdefault("timestamp", time.strftime("%H:%M:%S"))
                    payload.setdefault("timestamp_ms", int(time.time() * 1000))
                    if GLOBAL_STATE.mode != "wifi":
                        continue
                    if not GLOBAL_STATE.process_telemetry(payload, source="wifi"):
                        continue
                    GLOBAL_STATE.log_message(
                        f"📶 [WiFi UDP from {addr[0]}] Patient #{payload.get('patient')}: "
                        f"HR={payload.get('hr')} SpO2={payload.get('spo2')}% "
                        f"Temp={payload.get('temp_c')}°C Alert={payload.get('alert')}"
                    )
            except Exception:
                pass
    except Exception as e:
        GLOBAL_STATE.log_message(f"UDP socket error: {e}", level="warning")


def run_server(port: int = 8080, host: str = "127.0.0.1", sim_period: float = 10.0,
               raw_log_dir=None):
    if not math.isfinite(sim_period) or sim_period <= 0:
        raise ValueError("Simulation period must be positive and finite")
    logger = None
    if raw_log_dir is not None:
        try:
            logger = RawSessionLog(raw_log_dir)
        except FileExistsError:
            raise
        except OSError as exc:
            print(f"WARNING: Raw serial logging disabled: {exc}", file=sys.stderr, flush=True)
        if logger:
            print(f"Raw serial session: {logger.directory}")
            GLOBAL_STATE.serial_receiver.raw_logger = logger
            GLOBAL_STATE.tx_receiver.raw_logger = logger
    try:
        _run_server(port, host, sim_period)
    finally:
        if logger:
            GLOBAL_STATE.serial_receiver.disconnect()
            GLOBAL_STATE.tx_receiver.disconnect()
            GLOBAL_STATE.serial_receiver.raw_logger = None
            GLOBAL_STATE.tx_receiver.raw_logger = None
            logger.close()


def _run_server(port, host, sim_period):
    GLOBAL_STATE.sim_period = sim_period
    if host == "0.0.0.0":
        print("WARNING: Binding all interfaces exposes unauthenticated dashboard controls and UDP ingestion.")
    # Start snapshot/status worker
    t1 = threading.Thread(target=background_worker, daemon=True)
    t1.start()

    # Start WiFi UDP telemetry receiver worker
    t2 = threading.Thread(target=udp_receiver_worker, args=(8082, host), daemon=True)
    t2.start()

    server = ThreadingHTTPServer((host, port), MedicalMonitorHandler)
    GLOBAL_STATE.log_message(f"🚀 Medical Monitor Web Server started at http://localhost:{port}")
    print(f"\n========================================================")
    print(f"🏥 VLC MEDICAL PATIENT MONITOR DASHBOARD")
    print(f"📡 AmebaPro2 NPU Optical Wireless Receiver Station")
    print(f"🌐 Web UI: http://localhost:{port} (or http://127.0.0.1:{port})")
    print(f"📶 WiFi UDP Port: 8082 (direct wireless telemetry)")
    print(f"========================================================\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[INFO] Stopping server...")
    finally:
        server.server_close()


if __name__ == "__main__":
    port_to_use = 8080
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        port_to_use = int(sys.argv[1])
    run_server(port=port_to_use)
