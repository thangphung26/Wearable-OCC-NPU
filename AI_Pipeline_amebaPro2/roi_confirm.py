#!/usr/bin/env python3
"""Confirm or replace the AmebaPro2 VLC ROI using one Serial JPEG snapshot."""

from __future__ import annotations

import argparse
import sys
import time

import cv2
import numpy as np
import serial
from serial.tools import list_ports


WIDTH, HEIGHT = 320, 192
GRID_COLS, GRID_ROWS = 10, 6
CELL_W, CELL_H = WIDTH // GRID_COLS, HEIGHT // GRID_ROWS


def read_snapshot(port: serial.Serial, timeout: float = 30.0):
    port.reset_input_buffer()
    port.write(b"SNAP\n")
    port.flush()
    deadline = time.monotonic() + timeout
    next_retry = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if time.monotonic() >= next_retry:
            print("Retrying SNAP request...")
            port.write(b"SNAP\n")
            port.flush()
            next_retry = time.monotonic() + 3.0
        line = port.readline()
        if not line.startswith(b"[SNAPSHOT_BEGIN]"):
            if line:
                print(line.decode(errors="replace").rstrip())
                if line.startswith(b"[SNAPSHOT] Request accepted"):
                    next_retry = deadline
            continue
        fields = line.decode().strip().split()
        if len(fields) != 6:
            raise RuntimeError(f"Invalid snapshot header: {line!r}")
        size, roi_x, roi_y, roi_w, roi_h = map(int, fields[1:])
        data = port.read(size)
        if len(data) != size:
            raise RuntimeError(f"Short JPEG: expected {size}, received {len(data)}")
        port.readline()  # newline following binary data
        port.readline()  # [SNAPSHOT_END]
        image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError("OpenCV could not decode the JPEG snapshot")
        return image, (roi_x, roi_y, roi_w, roi_h)
    raise TimeoutError("No snapshot received. Close Arduino Serial Monitor and check the port.")


def auto_detect_led_roi(image: np.ndarray, target_w: int = 32, target_h: int = 26) -> tuple[int, int, int, int]:
    """
    Automatically detect the VLC LED emitter spot using computer vision.
    Locates the brightest compact optical hotspot and centers a
    target_w x target_h bounding box on it.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(blurred)

    if max_val < 50:
        # Image is too dark or LED is off, fallback to center of image
        return (WIDTH // 2 - target_w // 2, HEIGHT // 2 - target_h // 2, target_w, target_h)

    # Threshold for the bright saturated LED emitter core
    thresh_val = max(110, int(max_val * 0.75))
    _, thresh = cv2.threshold(blurred, thresh_val, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    cx, cy = max_loc
    if contours:
        # Filter contours by plausible LED emitter area (< 3000 px to discard massive ceiling light panels)
        valid_contours = [c for c in contours if cv2.contourArea(c) <= 3000]
        if valid_contours:
            best_c = max(valid_contours, key=lambda c: cv2.contourArea(c))
            M = cv2.moments(best_c)
            if M["m00"] > 0:
                cx = int(M["m10"] / M["m00"])
                cy = int(M["m01"] / M["m00"])

    x = max(0, min(WIDTH - target_w, cx - target_w // 2))
    y = max(0, min(HEIGHT - target_h, cy - target_h // 2))
    return (int(x), int(y), target_w, target_h)


def choose_roi(image: np.ndarray, automatic_roi, auto_detected_roi):
    selected = list(auto_detected_roi if auto_detected_roi else automatic_roi)
    window = "VLC ROI confirmation"

    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window, 960, 576)
    while True:
        display = image.copy()
        for x in range(1, GRID_COLS):
            cv2.line(display, (x * CELL_W, 0), (x * CELL_W, HEIGHT), (60, 60, 60), 1)
        for y in range(1, GRID_ROWS):
            cv2.line(display, (0, y * CELL_H), (WIDTH, y * CELL_H), (60, 60, 60), 1)

        # Draw camera candidate in thin cyan/amber if different
        if automatic_roi and tuple(selected) != tuple(automatic_roi):
            ax, ay, aw, ah = automatic_roi
            cv2.rectangle(display, (ax, ay), (ax + aw - 1, ay + ah - 1), (255, 180, 0), 1)
            cv2.putText(display, "Camera raw candidate", (ax, max(12, ay - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255, 180, 0), 1, cv2.LINE_AA)

        # Draw active selected box in bold green
        x, y, w, h = selected
        cv2.rectangle(display, (x, y), (x + w - 1, y + h - 1), (0, 255, 0), 2)
        cv2.putText(display, f"Active ROI px: ({x}, {y}, {w}, {h})", (5, 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1, cv2.LINE_AA)
        cv2.putText(display, "[ENTER] Confirm | [A] Auto-Detect LED | [R] Draw Manual | [ESC] Cancel",
                    (5, HEIGHT - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (0, 255, 255), 1, cv2.LINE_AA)

        cv2.imshow(window, display)
        key = cv2.waitKey(30) & 0xFF
        if key in (10, 13):  # ENTER
            cv2.destroyAllWindows()
            return tuple(selected)
        if key in (ord("a"), ord("A")):  # Re-run auto detect
            selected[:] = list(auto_detect_led_roi(image))
            print(f"Auto-detected LED ROI: {tuple(selected)}")
        if key in (ord("r"), ord("R")):  # Draw manually
            box = cv2.selectROI(window, image, showCrosshair=True,
                                fromCenter=False, printNotice=False)
            if box[2] > 0 and box[3] > 0:
                selected[:] = map(int, box)
        if key == 27:  # ESC
            cv2.destroyAllWindows()
            return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="auto",
                        help="Serial device, or 'auto' to detect it")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--auto", action="store_true",
                        help="Automatically detect the VLC LED emitter using OpenCV without GUI interaction")
    args = parser.parse_args()

    available = list(list_ports.comports())
    if args.port == "auto":
        preferred = [p for p in available if
                     "USB" in p.device or "ACM" in p.device]
        if len(preferred) == 1:
            port_name = preferred[0].device
        elif not preferred:
            print("No USB serial port found. Reconnect the AMB82-MINI UART USB port.")
            return 2
        else:
            print("Multiple USB serial ports found; select one with --port:")
            for item in preferred:
                print(f"  {item.device}: {item.description}")
            return 2
    else:
        port_name = args.port
        if not any(p.device == port_name for p in available):
            print(f"Serial port {port_name!r} does not exist.")
            if available:
                print("Available serial ports:")
                for item in available:
                    print(f"  {item.device}: {item.description}")
            else:
                print("No serial ports detected. Reconnect the board's UART USB cable.")
            return 2

    print(f"Opening {port_name} at {args.baud} baud")
    with serial.Serial(port_name, args.baud, timeout=1, write_timeout=2) as port:
        time.sleep(0.4)
        image, automatic_roi = read_snapshot(port)
        print(f"Camera candidate ROI: {automatic_roi}")

        auto_detected = auto_detect_led_roi(image)
        print(f"OpenCV Auto-detected LED ROI: {auto_detected}")

        if args.auto:
            choice = auto_detected
            print(f"Auto-mode enabled: directly applying detected ROI {choice}")
        else:
            choice = choose_roi(image, automatic_roi, auto_detected)

        if choice is None:
            print("Cancelled; RX ROI was not changed.")
            return 1

        x, y, w, h = choice
        if choice == automatic_roi:
            port.write(b"CONFIRM\n")
        else:
            port.write(f"ROI_PX {x} {y} {w} {h}\n".encode())
        port.flush()
        print(f"Confirmed ROI pixels ({x}, {y}, {w}, {h}). Keep TX/camera fixed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
