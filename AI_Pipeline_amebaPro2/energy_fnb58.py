#!/usr/bin/env python3
"""Whole-board FNB58 USB-input energy capture and offline pipeline analysis.

Only check/run access USB. Analyze uses the standard library and never opens
USB or serial. Acquisition requests 30 FPS; replay/resampling is 20 Hz; the
optical link rate is 5 bit/s. These are distinct rates.
"""
import argparse
import bisect
import csv
import hashlib
import json
import math
from pathlib import Path
import platform
import re
import statistics
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
FIELDS = ['host_rx_time_ns', 'packet_index', 'sample_in_packet', 'voltage_V',
          'current_A', 'dp_V', 'dn_V', 'temp_C', 'crc_ok']
IDLE = 'idle (benchmark firmware waiting for command)'
UDEV_RULE = 'SUBSYSTEM=="usb", ATTR{idVendor}=="2e3c", ATTR{idProduct}=="5558", MODE="0660", TAG+="uaccess"'


def crc8(data):
    value = 0x42
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = ((value << 1) ^ (0x39 if value & 0x80 else 0)) & 0xff
    return value


# Protocol reimplemented from baryluk/fnirsi-usb-power-data-logger,
# fnirsi_logger.py (MIT), supplied reference; no runtime scratchpad dependency.
# Includes its init/keepalive sequence, little-endian scales, CRC, and draining.
def decode_packet(data, host_rx_time_ns, packet_index):
    data = bytes(data)
    if len(data) != 64 or data[0] != 0xaa or data[1] != 0x04:
        return []
    ok = crc8(data[1:-1]) == data[-1]
    rows = []
    for i in range(4):
        o = 2 + i * 15
        def uint(offset, size):
            return int.from_bytes(data[o + offset:o + offset + size], 'little')
        rows.append(dict(zip(FIELDS, [host_rx_time_ns, packet_index, i,
            uint(0, 4) / 1e5, uint(4, 4) / 1e5, uint(8, 2) / 1e3,
            uint(10, 2) / 1e3, uint(13, 2) / 10, int(ok)])))
    return rows


def command(code, checksum):
    return bytes([0xaa, code]) + bytes(61) + bytes([checksum])


class Meter:
    """Claim the FNB58 HID interface; restore all detached kernel drivers."""
    def __enter__(self):
        import usb.core
        import usb.util
        self.usb = usb
        self.dev = usb.core.find(idVendor=0x2e3c, idProduct=0x5558)
        self.interface = None
        self.detached = []
        self.claimed = False
        self.drain_complete = False
        if self.dev is None:
            raise RuntimeError('FNB58 not found (USB VID 2e3c PID 5558); connect its USB-C PC port.')
        try:
            invalid = False
            try:
                self.dev.reset()
            except usb.core.USBError as exc:
                if exc.errno != 19 and exc.backend_error_code != -4:
                    raise
                invalid = True
            time.sleep(0.5)
            if not invalid:
                try:
                    cfg = self.dev.get_active_configuration()
                except usb.core.USBError as exc:
                    if exc.errno != 19 and exc.backend_error_code != -4:
                        raise
                    invalid = True
            if invalid:
                self.dev = usb.core.find(idVendor=0x2e3c, idProduct=0x5558)
                if self.dev is None:
                    raise RuntimeError('FNB58 not found after USB reset')
                cfg = self.dev.get_active_configuration()
            for number in sorted({i.bInterfaceNumber for i in cfg}):
                if self.dev.is_kernel_driver_active(number):
                    self.dev.detach_kernel_driver(number)
                    self.detached.append(number)
            self.dev.set_configuration()
            cfg = self.dev.get_active_configuration()
            intf = next((i for i in cfg if i.bInterfaceClass == 3), None)
            if intf is None:
                raise RuntimeError('FNB58 HID interface not found')
            self.interface = intf.bInterfaceNumber
            self.ep_in = usb.util.find_descriptor(intf, custom_match=lambda e:
                usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN)
            self.ep_out = usb.util.find_descriptor(intf, custom_match=lambda e:
                usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT)
            if self.ep_in is None or self.ep_out is None:
                raise RuntimeError('FNB58 HID IN/OUT endpoints not found')
            usb.util.claim_interface(self.dev, self.interface)
            self.claimed = True
            for code, checksum in [(0x81, 0x8e), (0x82, 0x96), (0x82, 0x96)]:
                self.ep_out.write(command(code, checksum), timeout=1000)
            self.next_keepalive = time.monotonic() + 1
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def read(self, keepalive=True, timeout=200):
        if keepalive and time.monotonic() >= self.next_keepalive:
            self.ep_out.write(command(0x83, 0x9e), timeout=1000)
            self.next_keepalive = time.monotonic() + 1
        try:
            data = self.ep_in.read(64, timeout=timeout)
            return bytes(data), time.time_ns()
        except self.usb.core.USBTimeoutError:
            return None

    def drain(self, consume):
        # fnirsi_stop-style: stop keepalives, keep reading until a 1-s timeout.
        # Bound shutdown if faulty firmware never stops; record that explicitly.
        end = time.monotonic() + 10
        while time.monotonic() < end:
            packet = self.read(keepalive=False, timeout=1000)
            if packet is None:
                self.drain_complete = True
                return
            consume(*packet)
        raise RuntimeError('FNB58 drain did not reach a timeout within 10 s')

    def __exit__(self, *_):
        try:
            if self.claimed:
                self.usb.util.release_interface(self.dev, self.interface)
        finally:
            try:
                self.usb.util.dispose_resources(self.dev)
            finally:
                for number in self.detached:
                    try:
                        self.dev.attach_kernel_driver(number)
                    except Exception:
                        pass


def explain_error(exc):
    print(f'ERROR: {exc}', file=sys.stderr)
    if isinstance(exc, PermissionError) or getattr(exc, 'errno', None) in (13, -3) or getattr(exc, 'backend_error_code', None) == -3:
        print('Install /etc/udev/rules.d/99-fnb58.rules containing exactly:\n' + UDEV_RULE,
              file=sys.stderr)
        print('sudo udevadm control --reload-rules\nsudo udevadm trigger --subsystem-match=usb\n'
              'Unplug/replug the FNB58 PC cable if access is still denied.', file=sys.stderr)


def check(seconds, meter_factory=Meter):
    rows = []
    index = 0
    with meter_factory() as meter:
        start = time.monotonic()
        def consume(data, stamp):
            nonlocal index
            rows.extend(decode_packet(data, stamp, index))
            index += 1
        try:
            while time.monotonic() - start < seconds:
                packet = meter.read()
                if packet is not None:
                    consume(*packet)
        finally:
            elapsed = time.monotonic() - start
            capture_rows = list(rows)
            meter.drain(consume)
    valid = [r for r in capture_rows if r['crc_ok']]
    print(f'Samples: {len(capture_rows)}; CRC failures: '
          f'{sum(not r["crc_ok"] for r in capture_rows)} samples / '
          f'{len({r["packet_index"] for r in capture_rows if not r["crc_ok"]})} packets; '
          f'effective sample rate: {len(capture_rows) / elapsed:.3f} samples/s '
          f'({len(valid) / elapsed:.3f} CRC-valid samples/s)')
    for label, values in [('V', [r['voltage_V'] for r in valid]),
                          ('I (A)', [r['current_A'] for r in valid]),
                          ('P (W)', [r['voltage_V'] * r['current_A'] for r in valid])]:
        print(f'{label} mean/min/max: {statistics.mean(values):.6f} / '
              f'{min(values):.6f} / {max(values):.6f}' if values else f'{label}: no CRC-valid samples')
    if not valid:
        raise RuntimeError('FNB58 delivered no CRC-valid measurement samples')


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def read_json(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def session_name(value):
    if not value or value in ('.', '..') or not re.fullmatch(r'[A-Za-z0-9_.-]+', value):
        raise argparse.ArgumentTypeError('Session must use letters, digits, underscore, dot or hyphen')
    return value


def paths(root, name):
    session_name(name)
    return root / 'dataset/benchmark' / name, root / 'dataset/energy' / name


def preflight(root, name):
    benchmark, energy = paths(root, name)
    for path in (benchmark, energy):
        if path.exists() or path.is_symlink():
            raise FileExistsError(f'Session exists; refusing overwrite: {path}')
    return benchmark, energy


def run(args, root=ROOT, meter_factory=Meter):
    _, energy = preflight(root, args.session)  # Always before opening any device.
    energy.mkdir(parents=True, exist_ok=False)
    meta = dict(meter_model='FNIRSI FNB58', nominal_samples_per_s=100,
        connection='PC USB -> FNB58 inline (data passthrough) -> AMB82-MINI RX '
                   '(CH340 /dev/ttyUSB0); FNB58 USB-C PC port -> host HID 2e3c:5558',
        lighting=args.lighting, lux_rx=args.lux_rx, host=platform.node(),
        variant=getattr(args, 'variant', 'original'), roi_px=getattr(args, 'roi_px', None),
        platform=platform.platform(), script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        start_time_ns=time.time_ns(), end_time_ns=None, duration=args.duration,
        repeats=args.repeats, idle_segments=[], collector_returncode=None,
        complete=False, logger_error=None, error=None, drain_complete=False)
    write_json(energy / 'meta.json', meta)
    stop, ready = threading.Event(), threading.Event()
    def log():
        try:
            with (energy / 'fnb58_samples.csv').open('w', newline='', buffering=1) as f:
                writer = csv.DictWriter(f, fieldnames=FIELDS)
                writer.writeheader()
                index = 0
                def consume(data, stamp):
                    nonlocal index
                    writer.writerows(decode_packet(data, stamp, index))
                    index += 1
                with meter_factory() as meter:
                    ready.set()
                    try:
                        while not stop.is_set():
                            packet = meter.read()
                            if packet is not None:
                                consume(*packet)
                    finally:
                        meter.drain(consume)
                        meta['drain_complete'] = meter.drain_complete
        except BaseException as exc:
            meta['logger_error'] = f'{type(exc).__name__}: {exc}'
            explain_error(exc)
        finally:
            ready.set()
    thread = threading.Thread(target=log, name='fnb58-logger')
    child = None
    def idle(position):
        segment = dict(label=IDLE, position=position, start_time_ns=time.time_ns(), end_time_ns=None)
        meta['idle_segments'].append(segment)
        try:
            end = time.monotonic() + args.idle_s
            while time.monotonic() < end:
                if not thread.is_alive():
                    raise RuntimeError('Meter logger stopped during idle')
                time.sleep(min(.1, max(0, end - time.monotonic())))
        finally:
            segment['end_time_ns'] = time.time_ns()
    thread.start()
    try:
        ready.wait()
        if meta['logger_error']:
            raise RuntimeError(meta['logger_error'])
        idle('before')
        cmd = [str(REPO / '.venv/bin/python'), str(root / 'AI_Pipeline_amebaPro2/benchmark_cpu_npu_pipeline.py'),
               '--session', args.session, '--duration', str(args.duration),
               '--repeats', str(args.repeats), '--lighting', args.lighting]
        cmd += ['--variant', meta['variant']]
        if meta['roi_px'] is not None:
            cmd += ['--roi-px', meta['roi_px']]
        meta['collector_command'] = cmd
        # Inherit stdout/stderr so the collector streams directly to the terminal.
        child = subprocess.Popen(cmd)
        while child.poll() is None:
            if not thread.is_alive():
                raise RuntimeError('Meter logger stopped during collector')
            time.sleep(.1)
        meta['collector_returncode'] = child.returncode
        idle('after')
    except (Exception, KeyboardInterrupt) as exc:
        meta['error'] = f'{type(exc).__name__}: {exc}'
        explain_error(exc)
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
            meta['collector_returncode'] = child.returncode
        stop.set()
        thread.join()
        meta['end_time_ns'] = time.time_ns()
        meta['complete'] = (meta['collector_returncode'] == 0 and not meta['error']
                            and not meta['logger_error'] and meta['drain_complete'])
        write_json(energy / 'meta.json', meta)
    result = analyze(args.session, root)
    return 0 if result['complete'] else 1


def describe(values):
    return dict(mean=statistics.mean(values) if values else None,
                sd=statistics.stdev(values) if len(values) > 1 else None, n=len(values))


def load_samples(path):
    samples = []
    if path.exists():
        with path.open(newline='') as f:
            for r in csv.DictReader(f):
                # Raw CSV preserves the packet receive timestamp for all four rows.
                # Reference convention: samples precede reception by 40,30,20,10 ms.
                # This is a nominal within-packet reconstruction, not device timestamps.
                stamp = int(r['host_rx_time_ns']) - (4 - int(r['sample_in_packet'])) * 10_000_000
                samples.append(dict(t=stamp, packet=int(r['packet_index']),
                    ok=r['crc_ok'].lower() in ('1', 'true'),
                    v=float(r['voltage_V']), i=float(r['current_A'])))
    samples.sort(key=lambda r: r['t'])
    return samples


def interval_stats(samples, start, end):
    selected = [r for r in samples if start <= r['t'] < end]
    good = [r for r in selected if r['ok']]
    invalid = [r for r in selected if not r['ok']]
    power = [r['v'] * r['i'] for r in good]
    times = [r['t'] for r in good]
    gaps = [(b - a) / 1e9 for a, b in zip(times, times[1:])]
    bins = {}
    for r, p in zip(good, power):
        bins.setdefault((r['t'] - start) // 1_000_000_000, []).append(p)
    bin_power = [statistics.mean(v) for v in bins.values()]
    # Integrate clipped host-time trapezoids, including interpolated boundaries
    # only when bracketed by valid observations. Never extrapolate missing data.
    valid = [r for r in samples if r['ok']]
    vt = [r['t'] for r in valid]
    lo = max(0, bisect.bisect_left(vt, start) - 1)
    hi = min(len(valid), bisect.bisect_right(vt, end) + 1)
    energy = covered = 0.0
    integration_gaps = 0
    for a, b in zip(valid[lo:hi], valid[lo + 1:hi]):
        left, right = max(start, a['t']), min(end, b['t'])
        if right <= left or b['t'] <= a['t']:
            continue
        pa, pb = a['v'] * a['i'], b['v'] * b['i']
        pl = pa + (pb - pa) * (left - a['t']) / (b['t'] - a['t'])
        pr = pa + (pb - pa) * (right - a['t']) / (b['t'] - a['t'])
        dt = (right - left) / 1e9
        energy += (pl + pr) / 2 * dt
        covered += dt
        integration_gaps += int(b['t'] - a['t'] > 50_000_000)
    count_energy = sum(power) * .01
    return dict(start_time_ns=start, end_time_ns=end, duration_s=(end - start) / 1e9,
        samples=len(good), total_samples=len(selected), crc_failures=len(invalid),
        crc_failed_packets=len({r['packet'] for r in invalid}), gaps_over_50ms=sum(g > .05 for g in gaps),
        integration_gaps_over_50ms=integration_gaps, integration_covered_s=covered,
        mean_voltage_V=statistics.mean(r['v'] for r in good) if good else None,
        mean_current_A=statistics.mean(r['i'] for r in good) if good else None,
        mean_power_W=statistics.mean(power) if power else None,
        sd_power_W=statistics.stdev(power) if len(power) > 1 else None,
        bin_1s_min_power_W=min(bin_power) if bin_power else None,
        bin_1s_max_power_W=max(bin_power) if bin_power else None,
        energy_J=energy if covered else None, sample_count_energy_J=count_energy,
        trapezoid_to_count_ratio=energy / count_energy if count_energy else None,
        coverage_fraction=covered / ((end - start) / 1e9) if end > start else 0)


def analyze(name, root=ROOT):
    benchmark, energy = paths(root, name)
    if not energy.is_dir():
        raise FileNotFoundError(f'Energy session not found: {energy}')
    meta = read_json(energy / 'meta.json', {})
    bm = read_json(benchmark / 'metadata.json', {})
    variant = bm.get('variant', meta.get('variant', 'original'))
    status = read_json(benchmark / 'summary.json', {})
    saved_reports = read_json(benchmark / 'reports.json', [])
    samples = load_samples(energy / 'fnb58_samples.csv')
    legs, warnings = [], []
    pending = None
    report_index = 0
    event_path = benchmark / 'events.jsonl'
    if event_path.exists():
        for line_number, line in enumerate(event_path.read_text().splitlines(), 1):
            try:
                event = json.loads(line)
                text = event['line']
                if text.startswith('[GOAL2_PIPE_BEGIN]'):
                    pending = dict(re.findall(r'(\w+)=([^ ]+)', text))
                if not text.startswith('[GOAL2_PIPE_REPORT] '):
                    continue
                r = json.loads(text.split('] ', 1)[1])
                idx = report_index
                report_index += 1
                repeat = idx // 3 + 1
                if idx < len(saved_reports):
                    saved = saved_reports[idx]
                    if any(saved.get(k) != r.get(k) for k in ('backend', 'elapsed_us', 'jobs', 'expected_jobs')):
                        warnings.append(f'Report/event mismatch at leg {idx + 1}')
                    else:
                        repeat = saved.get('repeat', repeat)
                mode = r['backend']
                if mode not in ('camera', 'cpu', 'npu'):
                    raise ValueError('Unknown backend')
                duration = int(pending['seconds']) if pending else int(bm.get('duration', meta.get('duration', 0)))
                confirmed = duration > 0 and r['jobs'] == r['expected_jobs'] == duration * 20
                if not confirmed:
                    warnings.append(f'Job count mismatch at leg {idx + 1}')
                if pending and pending.get('backend') != mode:
                    warnings.append(f'BEGIN/REPORT mode mismatch at leg {idx + 1}')
                elapsed_ns = int(r['elapsed_us']) * 1000
                end = int(event['host_time_ns']) - 1_000_000_000
                start = int(event['host_time_ns']) - elapsed_ns + 1_000_000_000
                if end <= start:
                    raise ValueError('Measured interval too short for guards')
                stats = interval_stats(samples, start, end)
                jobs_equivalent = stats['duration_s'] * 20
                legs.append(dict(repeat=repeat, mode=mode, variant=variant, **stats,
                    report_host_time_ns=int(event['host_time_ns']), report_elapsed_us=r['elapsed_us'],
                    report_duration_s=duration, report_jobs=r['jobs'], expected_jobs=r['expected_jobs'],
                    jobs_confirmed=confirmed, guarded_jobs_20hz_equivalent=jobs_equivalent,
                    energy_per_job_mJ=stats['energy_J'] * 1000 / jobs_equivalent if stats['energy_J'] is not None else None,
                    decoded_jpeg_fps=r['decoded_jpeg_fps'],
                    inference_mean_ms=r['inference_full']['mean_us'] / 1000))
                pending = None
            except (ValueError, KeyError, TypeError) as exc:
                warnings.append(f'events.jsonl line {line_number}: {exc}')
    if report_index != len(saved_reports):
        warnings.append('Number of event REPORT lines differs from reports.json; using timestamped event reports')
    for leg in legs:
        if leg['coverage_fraction'] < .999 or leg['integration_gaps_over_50ms']:
            warnings.append(f'Incomplete/gapped sample coverage: repeat {leg["repeat"]} {leg["mode"]}; trapezoids bridge gaps')
    modes = {}
    for mode in ('camera', 'cpu', 'npu'):
        per_repeat = []
        for leg in legs:
            if leg['mode'] != mode or leg['mean_power_W'] is None:
                continue
            camera = next((c for c in legs if c['repeat'] == leg['repeat'] and c['mode'] == 'camera'), None)
            delta = leg['mean_power_W'] - camera['mean_power_W'] if camera and camera['mean_power_W'] is not None else None
            per_repeat.append(dict(repeat=leg['repeat'], mean_power_W=leg['mean_power_W'],
                incremental_power_W=delta, incremental_energy_per_job_mJ=delta * 1000 / 20 if delta is not None else None))
        modes[mode] = dict(per_repeat=per_repeat,
            power_W=describe([v['mean_power_W'] for v in per_repeat]),
            incremental_power_W=describe([v['incremental_power_W'] for v in per_repeat if v['incremental_power_W'] is not None]),
            incremental_energy_per_job_mJ=describe([v['incremental_energy_per_job_mJ'] for v in per_repeat if v['incremental_energy_per_job_mJ'] is not None]))
    idle = [dict(label=s.get('label', IDLE), position=s['position'],
                 **interval_stats(samples, s['start_time_ns'], s['end_time_ns']))
            for s in meta.get('idle_segments', []) if s.get('end_time_ns') is not None]
    expected = int(meta.get('repeats', bm.get('repeats', 0))) * 3
    expected_pairs = {(rep, mode) for rep in range(1, expected // 3 + 1) for mode in modes}
    complete = bool(meta.get('complete') and status.get('complete') and expected > 0
                    and len(legs) == expected and {(l['repeat'], l['mode']) for l in legs} == expected_pairs
                    and all(l['jobs_confirmed'] and l['samples'] for l in legs) and not warnings)
    result = dict(session=name, variant=variant, complete=complete, expected_legs=expected, completed_legs=len(legs),
                  warnings=warnings, capture_meta=meta, collector_summary=status, legs=legs, modes=modes, idle_segments=idle)
    out = root / 'results/energy' / name
    out.mkdir(parents=True, exist_ok=True)
    with (out / 'legs.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(legs[0]) if legs else ['repeat', 'mode', 'variant'])
        writer.writeheader()
        writer.writerows(legs)
    write_json(out / 'summary.json', result)
    (out / 'REPORT.md').write_text(render_report(result))
    print(f'Analysis: {out} (variant={variant}, complete={str(complete).lower()})')
    return result


def render_report(result):
    def fmt(value):
        return 'NA' if value is None else f'{value:.6g}'
    lines = [f'# FNB58 energy: {result["session"]}', '', f'complete={str(result["complete"]).lower()}', '',
        f'Variant: {result["variant"]}', '',
        'This is whole-board power at the USB input, including the CH340 USB-UART and the camera. '
        'The meter cannot resolve individual ~3 ms inferences. The CNN input is a fixed bank replay, '
        'not the live ROI tensor. CPU mode drops to ~14 FPS camera rate (see the per-leg reports below), '
        'so its per-job energy mixes effects.', '',
        'Timing: pipelineStage(10, false) is a separate warm-up. The reported elapsed_us starts '
        'in pipelineStage(seconds, true), whose counters and bank sequence restart. The measured '
        'start is REPORT host_time_ns minus elapsed_us, with a 1 s guard at both edges. '
        'Serial formatting/transport latency shifts this estimate; there is no shared device clock. '
        'Raw CSV timestamps are packet receive times. Analysis reconstructs four sample times at '
        'receive time minus 40, 30, 20, 10 ms, following the reference logger at nominal 100 samples/s. '
        'USB jitter and host clock adjustments can affect alignment and integration.', '',
        'Report jobs and expected_jobs must both equal commanded duration × 20. They describe the '
        'full post-warm-up stage. Energy per job for the guarded interval divides its trapezoid energy '
        'by guarded seconds × 20 Hz (a job-equivalent estimate, since individual job times are unavailable). '
        'The 30 FPS requested camera acquisition, 20 Hz resampling/replay, and 5 bit/s link are distinct.', '',
        'CRC-failed samples remain in the raw CSV and are excluded from all power/energy statistics. '
        'Energy uses clipped trapezoids on reconstructed host times, interpolating bracketed interval '
        'edges and bridging gaps; coverage and gap counts flag missing data. Count energy is Σ(V·I) × '
        '10 ms. Their ratio exposes sampling/timing discrepancies. One-second bins are aligned to '
        'the guarded start; the final partial bin is included.', '',
        'Statistics: mean ± sample SD across repeat means describes repeat variability; within-leg '
        'power SD describes sample variation. Repeats receive equal weight. Incremental power is '
        'paired with camera-only within each repeat; incremental mJ/job = ΔP / 20 Hz × 1000. '
        'All per-repeat values are shown, including for three repeats. No confidence intervals '
        'are inferred from this small sample; these measurements do not estimate a binomial proportion.', '',
        '| Repeat | Mode | P mean ± SD (W) | V | I (A) | 1-s P min/max (W) | Valid samples | CRC failed samples/packets | Gaps >50ms | E (J) | Count E (J) | Ratio | Report jobs | Guarded job equivalents | mJ/job | FPS | Inference ms |',
        '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for l in result['legs']:
        values = [l['repeat'], l['mode'], f'{fmt(l["mean_power_W"])} ± {fmt(l["sd_power_W"])}',
            fmt(l['mean_voltage_V']), fmt(l['mean_current_A']), f'{fmt(l["bin_1s_min_power_W"])}/{fmt(l["bin_1s_max_power_W"])}',
            l['samples'], f'{l["crc_failures"]}/{l["crc_failed_packets"]}', l['gaps_over_50ms'],
            fmt(l['energy_J']), fmt(l['sample_count_energy_J']), fmt(l['trapezoid_to_count_ratio']),
            f'{l["report_jobs"]} (expected {l["expected_jobs"]}, confirmed {l["jobs_confirmed"]})',
            fmt(l['guarded_jobs_20hz_equivalent']), fmt(l['energy_per_job_mJ']), fmt(l['decoded_jpeg_fps']), fmt(l['inference_mean_ms'])]
        lines.append('| ' + ' | '.join(map(str, values)) + ' |')
    lines += ['', '| Mode | Repeat | P (W) | Paired ΔP (W) | Δ mJ/job |', '|---|---|---|---|---|']
    for mode, data in result['modes'].items():
        for v in data['per_repeat']:
            lines.append(f'| {mode} | {v["repeat"]} | {fmt(v["mean_power_W"])} | {fmt(v["incremental_power_W"])} | {fmt(v["incremental_energy_per_job_mJ"])} |')
        def mean_sd(key):
            return f'{fmt(data[key]["mean"])} ± {fmt(data[key]["sd"])} (n={data[key]["n"]})'
        lines.append(f'| {mode} | mean ± SD | {mean_sd("power_W")} | {mean_sd("incremental_power_W")} | {mean_sd("incremental_energy_per_job_mJ")} |')
    lines += ['', 'Idle segments:']
    for s in result['idle_segments']:
        lines.append(f'- {s["position"]}: {s["label"]}; {fmt(s["duration_s"])} s, '
            f'{s["samples"]} valid samples, P={fmt(s["mean_power_W"])} ± {fmt(s["sd_power_W"])} W, '
            f'V={fmt(s["mean_voltage_V"])} V, I={fmt(s["mean_current_A"])} A, '
            f'E={fmt(s["energy_J"])} J, count E={fmt(s["sample_count_energy_J"])} J, '
            f'ratio={fmt(s["trapezoid_to_count_ratio"])}, CRC failures={s["crc_failures"]} samples/'
            f'{s["crc_failed_packets"]} packets, gaps >50ms={s["gaps_over_50ms"]}.')
    if not result['idle_segments']:
        lines.append('- No recorded idle intervals.')
    lines += ['', 'Warnings:'] + [f'- {w}' for w in result['warnings']]
    if not result['warnings']:
        lines.append('- None.')
    for key in ('error', 'logger_error'):
        if result['capture_meta'].get(key):
            lines.append(f'- Capture {key}: {result["capture_meta"][key]}')
    if result['collector_summary'].get('error'):
        lines.append(f'- Collector: {result["collector_summary"]["error"]}')
    if result['capture_meta'].get('collector_returncode') not in (None, 0):
        lines.append(f'- Collector exit code: {result["capture_meta"]["collector_returncode"]}')
    return '\n'.join(lines) + '\n'


def positive_float(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError('Must be finite and positive')
    return value


def lux_value(value):
    if value.lower() == 'na':
        return 'na'
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError('Lux must be nonnegative or na')
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('check', help='Read only the FNB58; never open serial')
    p.add_argument('--seconds', type=positive_float, default=5)
    p = sub.add_parser('run', help='Capture idle/workload/idle, then analyze')
    p.add_argument('--session', type=session_name, required=True)
    p.add_argument('--variant', choices=['original', 'aug-v2'], default='original')
    p.add_argument('--roi-px', help='Forward ROI x,y,width,height; omitted uses collector default')
    p.add_argument('--duration', type=int, choices=range(10, 121), default=120, metavar='10..120')
    p.add_argument('--repeats', type=int, choices=range(1, 6), default=3)
    p.add_argument('--lighting', choices=['lights_on', 'lights_off'], required=True)
    p.add_argument('--lux-rx', type=lux_value, required=True)
    p.add_argument('--idle-s', type=positive_float, default=30)
    p = sub.add_parser('analyze', help='Read dataset only; write results/energy/NAME')
    p.add_argument('session', type=session_name)
    args = parser.parse_args(argv)
    try:
        if args.command == 'check':
            check(args.seconds)
            return 0
        if args.command == 'run':
            return run(args)
        analyze(args.session)
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        explain_error(exc)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
