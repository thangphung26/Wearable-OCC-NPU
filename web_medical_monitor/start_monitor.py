#!/usr/bin/env python3
"""
Launcher script for VLC Medical Patient Monitor Dashboard.
Run: python3 start_monitor.py [--port 8080]
"""

import argparse
import math
import os
import socket
import subprocess
import sys
from typing import List

# Ensure backend directory is in sys.path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(CURRENT_DIR, "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from server import run_server


def get_all_local_ips() -> List[str]:
    """Find all local network IP addresses for remote access."""
    ips = set()

    # Method 1: hostname -I (Linux standard)
    try:
        out = subprocess.check_output(["hostname", "-I"], stderr=subprocess.DEVNULL).decode().strip()
        for item in out.split():
            if item and not item.startswith("127."):
                ips.add(item)
    except Exception:
        pass

    # Method 2: UDP probe to default gateway
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            ips.add(ip)
    except Exception:
        pass

    # Method 3: gethostbyname
    try:
        host_ip = socket.gethostbyname(socket.gethostname())
        if host_ip and not host_ip.startswith("127."):
            ips.add(host_ip)
    except Exception:
        pass

    result = sorted(list(ips))
    return result if result else ["127.0.0.1"]


def main():
    parser = argparse.ArgumentParser(description="VLC Medical Patient Monitor Web Server")
    parser.add_argument("--port", type=int, default=8080, help="Port to bind server (default: 8080)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host interface (default: 127.0.0.1)")
    parser.add_argument("--sim-period", type=float, default=10.0, help="Simulation snapshot period in seconds (default: 10)")
    parser.add_argument("--raw-log-dir", help="Archive raw RX/TX UART in a new timestamped session directory (default: off)")
    args = parser.parse_args()
    if not math.isfinite(args.sim_period) or args.sim_period <= 0:
        parser.error("--sim-period must be positive and finite")

    local_ips = get_all_local_ips() if args.host == "0.0.0.0" else ["127.0.0.1"]

    print("\n" + "=" * 68)
    print("🏥  VLC MEDICAL PATIENT MONITOR - AMEBAPRO2 NPU TELEMETRY")
    print("=" * 68)
    print(f"📡 Firmware RX: Ameba_RX_NPU_Receiver.ino (/dev/ttyUSB0)")
    print(f"💡 Firmware TX: VLC_OOK_TX_realtime_sensors.ino (/dev/ttyACM0)")
    print(f"--------------------------------------------------------------------")
    print(f"🖥️  Local Access (This PC):    http://localhost:{args.port}")
    if args.host == "0.0.0.0":
        for ip in local_ips:
            print(f"📱 LAN Access (trusted network only): http://{ip}:{args.port}")
    else:
        print(f"HTTP / UDP bound interface: {args.host}")
    print("📶 WiFi UDP Telemetry Port: 8082 (same bound interface)")
    print(f"Simulation snapshot period: {args.sim_period:g} s")
    print("=" * 68 + "\n")

    run_server(port=args.port, host=args.host, sim_period=args.sim_period, raw_log_dir=args.raw_log_dir)


if __name__ == "__main__":
    main()
