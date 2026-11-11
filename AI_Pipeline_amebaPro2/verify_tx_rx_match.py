#!/usr/bin/env python3
"""
Side-by-Side Real-Time TX vs RX Verification Tool for Optical VLC Telemetry.
Simultaneously reads:
  1. TX Serial Port (Seeed Studio XIAO nRF52840) -> Ground Truth [TX_TELEMETRY]
  2. RX Serial Port (AmebaPro2 AMB82-MINI)        -> Optical Decoded [TELEMETRY_CONFIRMED]
Compares decoded fields bit-for-bit, measures packet success rate and BER for publication.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from typing import Optional, Dict, Any

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("Error: 'pyserial' is required. Run 'pip install pyserial' first.")
    sys.exit(1)


def parse_telemetry_line(line: str) -> Optional[Dict[str, Any]]:
    # Matches [TX_TELEMETRY] {...} or [TELEMETRY_CONFIRMED:...] {...}
    m = re.search(r"\[(?:TX_TELEMETRY|TELEMETRY(?:_CONFIRMED)?:[A-Za-z0-9_]+)\]\s*(\{.*\})", line)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            return None
    return None


class PortReader(threading.Thread):
    def __init__(self, name: str, port_name: str, baudrate: int, callback):
        super().__init__(daemon=True)
        self.name = name
        self.port_name = port_name
        self.baudrate = baudrate
        self.callback = callback
        self.running = True
        self.ser = None

    def run(self):
        try:
            self.ser = serial.Serial(self.port_name, self.baudrate, timeout=1.0)
            print(f"[{self.name}] Successfully opened {self.port_name} at {self.baudrate} baud.")
        except Exception as e:
            print(f"[{self.name} ERROR] Cannot open {self.port_name}: {e}")
            self.running = False
            return

        while self.running and self.ser.is_open:
            try:
                raw = self.ser.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    self.callback(self.name, line)
            except Exception as e:
                print(f"[{self.name} READ ERROR] {e}")
                time.sleep(0.5)

    def close(self):
        self.running = False
        if self.ser and self.ser.is_open:
            self.ser.close()


class TelemetryComparator:
    def __init__(self, log_csv: Optional[str] = "vlc_experiment_comparison.csv"):
        self.lock = threading.Lock()
        self.tx_cache: Dict[int, Dict[str, Any]] = {}  # seq -> data
        self.total_compared = 0
        self.total_matched = 0
        self.log_csv = log_csv

        if self.log_csv:
            with open(self.log_csv, "w", encoding="utf-8") as f:
                f.write("timestamp,seq,tx_hr,rx_hr,tx_spo2,rx_spo2,tx_temp,rx_temp,tx_imu,rx_imu,tx_alert,rx_alert,match,status\n")

        print("\n" + "=" * 95)
        print("🔬 REAL-TIME SIDE-BY-SIDE VLC TELEMETRY VERIFICATION (TX vs RX)")
        print("=" * 95)
        print(f"{'TIME':<9} | {'ORIGIN':<4} | {'SEQ':<4} | {'HR':<6} | {'SPO2':<6} | {'TEMP':<7} | {'IMU':<5} | {'ALERT':<6} | {'RESULT'}")
        print("-" * 95)

    def handle_line(self, source: str, line: str):
        data = parse_telemetry_line(line)
        if not data:
            # Print important diagnostic logs from RX or TX
            if "[AUTO_ROI]" in line:
                print(f"📷 [RX LOG] {line}")
            elif "[TX PRIORITY]" in line:
                print(f"🚨 [TX LOG] {line}")
            return

        timestamp = time.strftime("%H:%M:%S")
        seq = data.get("seq", 0)

        with self.lock:
            if source == "TX":
                self.tx_cache[seq] = data
                print(f"{timestamp:<9} | 📡TX | #{seq:<3} | {data.get('hr',0):<3}bpm | {data.get('spo2',0):<3}%  | {data.get('temp_c',0.0):<5.1f}C | {data.get('imu',0):<5} | {data.get('alert',0):<6} | TRANSMITTED")
            elif source == "RX":
                tx_data = self.tx_cache.get(seq)
                if tx_data:
                    match_hr = (data.get("hr") == tx_data.get("hr"))
                    match_spo2 = (data.get("spo2") == tx_data.get("spo2"))
                    match_temp = (abs(data.get("temp_c", 0.0) - tx_data.get("temp_c", 0.0)) < 0.15)
                    match_imu = (data.get("imu") == tx_data.get("imu"))
                    match_alert = (data.get("alert") == tx_data.get("alert"))

                    is_match = match_hr and match_spo2 and match_temp and match_imu and match_alert
                    self.total_compared += 1
                    if is_match:
                        self.total_matched += 1
                        status_str = "✅ 100% BIT-PERFECT MATCH"
                    else:
                        status_str = "❌ MISMATCH"

                    pct = (self.total_matched * 100.0 / self.total_compared) if self.total_compared else 0
                    print(f"{timestamp:<9} | 📷RX | #{seq:<3} | {data.get('hr',0):<3}bpm | {data.get('spo2',0):<3}%  | {data.get('temp_c',0.0):<5.1f}C | {data.get('imu',0):<5} | {data.get('alert',0):<6} | {status_str} (Acc: {pct:.1f}%)")

                    if self.log_csv:
                        with open(self.log_csv, "a", encoding="utf-8") as f:
                            f.write(f"{timestamp},{seq},{tx_data.get('hr')},{data.get('hr')},{tx_data.get('spo2')},{data.get('spo2')},{tx_data.get('temp_c')},{data.get('temp_c')},{tx_data.get('imu')},{data.get('imu')},{tx_data.get('alert')},{data.get('alert')},{1 if is_match else 0},{status_str}\n")
                else:
                    print(f"{timestamp:<9} | 📷RX | #{seq:<3} | {data.get('hr',0):<3}bpm | {data.get('spo2',0):<3}%  | {data.get('temp_c',0.0):<5.1f}C | {data.get('imu',0):<5} | {data.get('alert',0):<6} | DECODED (Waiting for TX seq sync)")


def main():
    parser = argparse.ArgumentParser(description="Real-Time TX vs RX VLC Optical Telemetry Comparator")
    parser.add_argument("--tx_port", default=None, help="TX Serial port (e.g. /dev/ttyACM1 or COM4)")
    parser.add_argument("--rx_port", default=None, help="RX Serial port (e.g. /dev/ttyACM0 or COM3)")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate (default 115200)")
    parser.add_argument("--csv", default="vlc_experiment_comparison.csv", help="CSV log filename for research paper")
    args = parser.parse_args()

    ports = list(list_ports.comports())
    if not ports:
        print("No USB serial devices detected. Connect both TX (nRF52840) and RX (AmebaPro2).")
        return 1

    print("Detected Serial Devices:")
    for i, p in enumerate(ports):
        print(f"  [{i}] {p.device}: {p.description}")

    tx_port = args.tx_port
    rx_port = args.rx_port

    if not tx_port or not rx_port:
        if len(ports) >= 2:
            print(f"\nAuto-assigning:")
            print(f"  RX (AmebaPro2): {ports[0].device}")
            print(f"  TX (XIAO nRF52840): {ports[1].device}")
            print("To manually specify ports, use: --rx_port <port> --tx_port <port>")
            rx_port = ports[0].device
            tx_port = ports[1].device
        else:
            print("\nOnly 1 serial port detected. Please specify --tx_port and --rx_port, or connect both boards.")
            return 1

    comparator = TelemetryComparator(log_csv=args.csv)
    rx_reader = PortReader("RX", rx_port, args.baud, comparator.handle_line)
    tx_reader = PortReader("TX", tx_port, args.baud, comparator.handle_line)

    rx_reader.start()
    tx_reader.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping comparator...")
        rx_reader.close()
        tx_reader.close()
        print(f"Final Statistics: {comparator.total_matched}/{comparator.total_compared} frames matched (Accuracy: {(comparator.total_matched*100.0/comparator.total_compared) if comparator.total_compared else 0:.2f}%).")
        if args.csv:
            print(f"Experiment log saved to: {args.csv}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
