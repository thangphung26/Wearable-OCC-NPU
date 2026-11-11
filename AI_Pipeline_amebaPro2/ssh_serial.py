"""Expose a remote serial port as a local readline/write object over SSH.

The remote host needs Python 3 and pyserial. Only serial bytes cross the SSH
channel; stderr is drained separately so it cannot block the bridge.
VLC_TX_SSH_PYTHON selects the remote shell launcher (default: py -3).
"""

from __future__ import annotations

import base64
import os
import queue
import select
import subprocess
import threading
import time


_REMOTE_BRIDGE = r'''import serial, sys, threading
port_name, baud = sys.argv[1], int(sys.argv[2])
ser = serial.Serial()
ser.port, ser.baudrate = port_name, baud
ser.timeout, ser.write_timeout = 0.2, 2
ser.open()
stopping = threading.Event()
def commands():
    try:
        for line in sys.stdin.buffer:
            if not line:
                break
            ser.write(line); ser.flush()
    except (OSError, serial.SerialException):
        pass
    stopping.set()
t = threading.Thread(target=commands, daemon=True); t.start()
try:
    while not stopping.is_set():
        line = ser.readline()
        if line:
            sys.stdout.buffer.write(line); sys.stdout.buffer.flush()
finally:
    ser.close()
'''


class SSHSerial:
    """Minimal serial-like wrapper suitable for benchmark_capture.reader()."""

    def __init__(self, target: str, port: str, baud: int, timeout: float = 0.2,
                 write_timeout: float = 2.0):
        del timeout, write_timeout  # Applied on the remote serial endpoint.
        encoded = base64.b64encode(_REMOTE_BRIDGE.encode("utf-8")).decode("ascii")
        launcher = os.environ.get('VLC_TX_SSH_PYTHON', 'py -3')
        remote_command = f'{launcher} -u -c "import base64;exec(base64.b64decode(\'{encoded}\'))" {port} {baud}'
        self.process = subprocess.Popen(
            ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", target,
             remote_command],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=0,
        )
        self._errors: queue.Queue[str] = queue.Queue()
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()
        # Let SSH start and surface immediate command/COM errors before capture setup.
        time.sleep(0.35)
        if self.process.poll() is not None:
            raise OSError(self._stderr_text() or f"SSH serial bridge exited ({self.process.returncode})")

    def _drain_stderr(self) -> None:
        assert self.process.stderr is not None
        for line in iter(self.process.stderr.readline, b""):
            self._errors.put(line.decode(errors="replace").rstrip())

    def _stderr_text(self) -> str:
        messages = []
        while True:
            try:
                messages.append(self._errors.get_nowait())
            except queue.Empty:
                break
        return "\n".join(messages)

    def readline(self) -> bytes:
        assert self.process.stdout is not None
        ready, _, _ = select.select([self.process.stdout], [], [], 0.2)
        if not ready:
            if self.process.poll() is not None:
                detail = self._stderr_text()
                raise OSError(detail or f"SSH serial bridge exited ({self.process.returncode})")
            return b""
        line = self.process.stdout.readline()
        if not line and self.process.poll() is not None:
            detail = self._stderr_text()
            raise OSError(detail or f"SSH serial bridge exited ({self.process.returncode})")
        return line

    def write(self, data: bytes) -> int:
        assert self.process.stdin is not None
        self.process.stdin.write(data)
        return len(data)

    def flush(self) -> None:
        assert self.process.stdin is not None
        self.process.stdin.flush()

    def close(self) -> None:
        if self.process.stdin is not None:
            try:
                self.process.stdin.close()
            except OSError:
                pass
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()
