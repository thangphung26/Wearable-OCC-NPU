"""Optional raw UART archive; serial readers never wait for disk I/O."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import queue
import sys
import threading
import time


class RawSessionLog:
    def __init__(self, root):
        started = datetime.now(timezone.utc)
        self.directory = Path(root) / started.strftime("%Y%m%d_%H%M%S")
        # A timestamp collision is a configuration error, never an overwrite.
        self.directory.mkdir(parents=True, exist_ok=False)
        self.enabled = True
        self.closed = False
        self.pending = queue.Queue(maxsize=4096)
        self.streams = {}
        self.counts = {"RX": 0, "TX": 0}
        backend = Path(__file__).resolve().parent
        sources = [backend.parent / "start_monitor.py", *sorted(backend.glob("*.py"))]
        self.meta = {
            "start_time": started.isoformat(), "start_time_ns": time.time_ns(),
            "ports": {"RX": None, "TX": None},
            "baud": {"RX": 115200, "TX": 115200}, "connections": [],
        }
        try:
            self.meta["source_sha256"] = {
                str(path.relative_to(backend.parent)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sources
            }
            for role in self.counts:
                self.streams[role] = (self.directory / (role.lower() + "_raw.jsonl")).open(
                    "x", encoding="utf-8", buffering=1)
            self._write_meta()
        except OSError as exc:
            self._disable(exc)
            self._close_streams()
        self.worker = threading.Thread(target=self._write_loop, daemon=True)
        self.worker.start()

    def _disable(self, error):
        if self.enabled:
            self.enabled = False
            print(f"WARNING: Raw serial logging disabled: {error}", file=sys.stderr, flush=True)

    def _enqueue(self, item):
        if self.enabled and not self.closed:
            try:
                self.pending.put_nowait(item)
            except queue.Full:
                self._disable("archive queue full; disk writer cannot keep up")

    def connection(self, role, port, baud):
        self._enqueue(("connection", role, dict(port=port, baud=baud, host_time_ns=time.time_ns())))

    def record(self, role, port, line, host_time_ns, kind):
        self._enqueue(("line", role, dict(host_time_ns=host_time_ns, port=port,
                                         line=line, kind=kind)))

    def _write_meta(self):
        with (self.directory / "session_meta.json").open("w", encoding="utf-8") as stream:
            json.dump(self.meta, stream, indent=2)
            stream.write("\n")

    def _write_loop(self):
        try:
            while True:
                item = self.pending.get()
                if item is None:
                    break
                if not self.enabled:
                    continue
                event, role, data = item
                try:
                    if event == "connection":
                        self.meta["ports"][role] = data["port"]
                        self.meta["baud"][role] = data["baud"]
                        self.meta["connections"].append(dict(role=role, **data))
                        self._write_meta()
                    else:
                        self.streams[role].write(json.dumps(data, ensure_ascii=False) + "\n")
                        self.counts[role] += 1
                except OSError as exc:
                    self._disable(exc)
            if self.enabled:
                self.meta.update(end_time=datetime.now(timezone.utc).isoformat(),
                                 end_time_ns=time.time_ns(), line_counts=self.counts)
                try:
                    self._write_meta()
                except OSError as exc:
                    self._disable(exc)
        finally:
            self._close_streams()

    def _close_streams(self):
        for stream in self.streams.values():
            try:
                stream.close()
            except OSError as exc:
                self._disable(exc)

    def close(self):
        """Call after stopping both readers; drain pending lines on clean shutdown."""
        if not self.closed:
            self.closed = True
            self.pending.put(None)
            self.worker.join()
