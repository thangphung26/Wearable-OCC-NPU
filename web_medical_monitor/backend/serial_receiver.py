"""
Serial receiver module for AmebaPro2 VLC NPU output.
Reads and parses output from Ameba_RX_NPU_Receiver.ino:
- [TELEMETRY:NPU] {"patient":1,"hr":75,"spo2":98,"temp_c":37.5,"imu":0,"battery":7,"alert":0,"seq":0}
- [TELEMETRY:CLASSIC] {...}
- [STATUS] frames=100 fps=19.8 samples=192 valid=5 rejected=0 ...
- [ROI] Locked cell x=... y=...
"""

from thresholds import validate_telemetry

import glob
import json
import re
import sys
import threading
import time
from typing import Callable, Optional, Dict, Any, List

# Try importing serial
try:
    import serial
    SERIAL_AVAILABLE = True
except ImportError:
    serial = None
    SERIAL_AVAILABLE = False


def list_available_ports() -> List[Dict[str, str]]:
    """Scan and list possible serial ports on Linux, macOS, and Windows."""
    ports = []
    if sys.platform.startswith("linux"):
        patterns = ["/dev/ttyACM*", "/dev/ttyUSB*", "/dev/pts/*"]
        for pat in patterns:
            for p in sorted(glob.glob(pat)):
                desc = f"USB Serial Device ({p})"
                if "USB" in p:
                    desc = f"AmebaPro2 Camera RX ({p})"
                elif "ACM" in p:
                    desc = f"XIAO nRF52840 Sensor TX ({p})"
                ports.append({"device": p, "description": desc})
    elif sys.platform.startswith("win"):
        for i in range(1, 32):
            ports.append({"device": f"COM{i}", "description": f"Serial Port COM{i}"})
    elif sys.platform.startswith("darwin"):
        for p in sorted(glob.glob("/dev/tty.usb*") + glob.glob("/dev/cu.usb*")):
            ports.append({"device": p, "description": f"USB Serial ({p})"})

    if not ports:
        ports.append({"device": "/dev/ttyUSB0", "description": "AmebaPro2 Camera RX (/dev/ttyUSB0)"})
        ports.append({"device": "/dev/ttyACM0", "description": "XIAO nRF52840 Sensor TX (/dev/ttyACM0)"})
    return ports


class SerialReceiver:
    def __init__(self, on_telemetry: Optional[Callable[[Dict[str, Any]], None]] = None,
                 on_status: Optional[Callable[[Dict[str, Any]], None]] = None,
                 on_log: Optional[Callable[[str], None]] = None,
                 on_connection: Optional[Callable[[bool, Optional[str]], None]] = None,
                 role: str = "RX"):
        self.connection_lock = threading.RLock()
        self.on_telemetry = on_telemetry
        self.on_status = on_status
        self.on_log = on_log
        self.on_connection = on_connection
        self.role = role.upper()  # "RX" or "TX"
        self.raw_logger = None

        self.port_name: Optional[str] = None
        self.baudrate: int = 115200
        self.ser = None
        self.is_running = False
        self.thread: Optional[threading.Thread] = None

        self.last_stats = {}

    def is_connected(self) -> bool:
        return bool(self.ser and self.is_running and getattr(self.ser, "is_open", False))

    def connect(self, port: str, baudrate: int = 115200) -> bool:
        with self.connection_lock:
            return self._connect(port, baudrate)

    def _connect(self, port: str, baudrate: int = 115200):
        if not SERIAL_AVAILABLE:
            if self.on_log:
                self.on_log("[WARNING] 'pyserial' package is not installed. Run 'pip install pyserial' to connect physical USB serial ports.")
            return False

        self.disconnect()
        if self.thread and self.thread.is_alive():
            return False
        self.last_stats = {}
        self.port_name = port
        self.baudrate = baudrate

        try:
            self.ser = serial.Serial(port, baudrate=baudrate, timeout=1.0)
            if self.raw_logger:
                self.raw_logger.connection(self.role, port, baudrate)
            self.is_running = True
            self.thread = threading.Thread(target=self._read_loop, daemon=True)
            self.thread.start()
            if self.on_log:
                self.on_log(f"[CONNECT] Connected to serial port {port} at {baudrate} bps.")
            if self.on_connection:
                self.on_connection(True, port)
            return True
        except Exception as e:
            if self.on_log:
                self.on_log(f"[ERROR] Failed to open serial port {port}: {e}")
            self.ser = None
            self.is_running = False
            if self.on_connection:
                self.on_connection(False, port)
            return False

    def disconnect(self):
        with self.connection_lock:
            return self._disconnect()

    def _disconnect(self):
        self.is_running = False
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
        if not self.thread or not self.thread.is_alive():
            self.thread = None
        if self.on_log:
            self.on_log("[DISCONNECT] Serial port closed.")
        if self.on_connection:
            self.on_connection(False, self.port_name)

    def _read_loop(self):
        consecutive_errors = 0
        while self.is_running and self.ser and self.ser.is_open:
            try:
                raw = self.ser.readline(16385)
                if not raw:
                    continue
                host_time_ns = time.time_ns()
                decoded = raw.decode("utf-8", errors="replace")
                line = decoded.strip()
                kind = "ignored"
                try:
                    if line:
                        consecutive_errors = 0
                        if self.on_log:
                            self.on_log(f"[RX UART] {line}")
                        kind = self.parse_line(line)
                finally:
                    if self.raw_logger:
                        self.raw_logger.record(self.role, self.port_name, decoded, host_time_ns, kind)
            except Exception as e:
                if self.is_running:
                    consecutive_errors += 1
                    if self.on_log:
                        self.on_log(f"[SERIAL READ ERROR] {e}")
                    if consecutive_errors >= 3:
                        if self.on_log:
                            self.on_log("[DISCONNECT] Serial device stopped responding.")
                        self.is_running = False
                        try:
                            self.ser.close()
                        except Exception:
                            pass
                        if self.on_connection:
                            self.on_connection(False, self.port_name)
                        break
                    time.sleep(0.5)

    def parse_line(self, line: str):
        # Malformed telemetry/status lines must never terminate the reader.
        try:
            if len(line) <= 16384:
                return self._parse_line(line)
        except Exception:
            return "rejected"
        return "ignored"

    def _parse_line(self, line):
        # 1. Telemetry line: [TELEMETRY_CONFIRMED:CLASSIC_PLL] {...} or [TX_TELEMETRY] {...}
        m_telemetry = re.search(r"\[(?:TELEMETRY(?:_CONFIRMED)?:([A-Za-z0-9_]+)|(TX_TELEMETRY))\]\s*(\{.*\})", line)
        if m_telemetry:
            is_tx = bool(m_telemetry.group(2)) or (self.role == "TX")
            decoder_suffix = m_telemetry.group(1) or "SENSORS"
            decoder = "TX_GROUND_TRUTH" if is_tx else f"Ameba_{decoder_suffix}"
            json_str = m_telemetry.group(3)
            try:
                data = json.loads(json_str)
                data["origin"] = "TX" if is_tx else "RX"
                data["decoder"] = decoder
                data["timestamp"] = time.strftime("%H:%M:%S")
                data["timestamp_ms"] = int(time.time() * 1000)

                if data.get("crc_valid") is False:
                    if self.on_telemetry:
                        self.on_telemetry(data)
                    return "rejected"
                valid, _, data = validate_telemetry(data)
                if not valid:
                    return "rejected"
                if self.on_telemetry:
                    self.on_telemetry(data)
                return "accepted"
            except json.JSONDecodeError:
                return "rejected"

        # 2. Status line: [STATUS] frames=100 fps=19.80 samples=192 valid=5 rejected=0 ...
        if line.startswith("[STATUS]"):
            m_frames = re.search(r"frames=(\d+)", line)
            m_fps = re.search(r"fps=([\d\.]+)", line)
            m_valid = re.search(r"valid=(\d+)", line)
            m_rej = re.search(r"rejected=(\d+)", line)
            m_sig = re.search(r"signal=([\d\.\-]+)", line)
            m_roi = re.search(r"roi_activity=([\d\.]+)", line)
            m_roi_box = re.search(r"roi=(\d+),(\d+),(\d+),(\d+)", line)
            m_valid_age = re.search(r"last_valid_age_s=(-?\d+)", line)
            m_candidates = re.search(r"candidates=(\d+)", line)
            m_phys = re.search(r"phys_reject=(\d+)", line)

            if m_frames:
                self.last_stats["frames"] = int(m_frames.group(1))
            if m_fps:
                self.last_stats["acquisition_fps"] = float(m_fps.group(1))
            if m_valid:
                self.last_stats["valid_packets"] = int(m_valid.group(1))
            if m_rej:
                self.last_stats["rejected_crc"] = int(m_rej.group(1))
            if m_sig:
                self.last_stats["signal"] = float(m_sig.group(1))
            if m_roi:
                self.last_stats["roi_activity"] = float(m_roi.group(1))
            if m_roi_box:
                self.last_stats["roi_box"] = [int(m_roi_box.group(i)) for i in range(1, 5)]
                self.last_stats["roi_locked"] = True
            elif "roi=searching" in line:
                self.last_stats["roi_locked"] = False
            if m_valid_age:
                self.last_stats["last_valid_age_sec"] = int(m_valid_age.group(1))
            if m_candidates:
                self.last_stats["candidate_packets"] = int(m_candidates.group(1))
            if m_phys:
                self.last_stats["physiology_rejects"] = int(m_phys.group(1))

            if self.on_status:
                self.on_status(self.last_stats)

        # 3. Auto-ROI dynamic cluster lock event:
        # [AUTO_ROI] Locked LED at cell=(...) centered px=(x,y,w,h) component_cells=... activity=...
        if "[AUTO_ROI]" in line:
            m_px = re.search(r"px=\((\d+),(\d+),(\d+),(\d+)\)", line)
            m_cells = re.search(r"component_cells=(\d+)", line)
            m_act = re.search(r"activity=([\d\.]+)", line)
            if m_px:
                self.last_stats["roi_box"] = [int(m_px.group(1)), int(m_px.group(2)), int(m_px.group(3)), int(m_px.group(4))]
            if m_cells:
                self.last_stats["roi_component_cells"] = int(m_cells.group(1))
            if m_act:
                self.last_stats["roi_activity"] = float(m_act.group(1))
            if self.on_status:
                self.on_status(self.last_stats)

        return "ignored"
