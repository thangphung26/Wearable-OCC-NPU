"""
Patient simulator module for VLC Medical Monitor.
Models eight synthetic profiles from the shared 48-bit VLC protocol, based on
PPG (Pulse Rate & SpO2), temperature, and 3-axis IMU values.
"""

import random
import time
from typing import Dict, Any
from thresholds import validate_telemetry

# Eight synthetic profiles matching vlc_protocol.py.
DATASET_PROFILES = [
    {"patient": 1, "hr": 75,  "spo2": 98, "temp_c": 37.5, "imu": 0, "battery": 7, "alert": 0, "desc": "Synthetic protocol profile baseline"},
    {"patient": 2, "hr": 115, "spo2": 96, "temp_c": 36.8, "imu": 1, "battery": 5, "alert": 0, "desc": "Synthetic protocol profile baseline"},
    {"patient": 3, "hr": 55,  "spo2": 92, "temp_c": 35.8, "imu": 0, "battery": 2, "alert": 1, "desc": "Synthetic protocol profile baseline"},
    {"patient": 4, "hr": 145, "spo2": 88, "temp_c": 39.5, "imu": 2, "battery": 4, "alert": 1, "desc": "Synthetic protocol profile baseline"},
    {"patient": 5, "hr": 90,  "spo2": 99, "temp_c": 36.5, "imu": 0, "battery": 6, "alert": 0, "desc": "Synthetic protocol profile baseline"},
    {"patient": 6, "hr": 180, "spo2": 94, "temp_c": 38.0, "imu": 3, "battery": 3, "alert": 1, "desc": "Synthetic protocol profile baseline"},
    {"patient": 7, "hr": 65,  "spo2": 95, "temp_c": 36.0, "imu": 1, "battery": 7, "alert": 0, "desc": "Synthetic protocol profile baseline"},
    {"patient": 8, "hr": 130, "spo2": 85, "temp_c": 40.0, "imu": 0, "battery": 1, "alert": 1, "desc": "Synthetic protocol profile baseline"},
]

IMU_DESCRIPTIONS = {
    0: "IMU state 0 — still",
    1: "IMU state 1 — moving",
    2: "IMU state 2 — fall flag",
    3: "IMU state 3 — protocol flag"
}

class PatientSimulator:
    def __init__(self):
        self.profile_index = 0
        self.auto_cycle = False
        self.cycle_interval_sec = 15.0
        self.last_cycle_time = time.time()
        self.sequence = 0
        self.forced_alert = False
        self.alert_override_reason = ""
        self.start_time = time.time()

    def set_patient(self, patient_id: int):
        for idx, prof in enumerate(DATASET_PROFILES):
            if prof["patient"] == patient_id:
                self.profile_index = idx
                self.forced_alert = False
                return True
        return False

    def trigger_alert(self, reason: str = "Manual Emergency Alert Triggered"):
        self.forced_alert = True
        self.alert_override_reason = reason

    def clear_alert(self):
        self.forced_alert = False
        self.alert_override_reason = ""

    def get_current_telemetry(self) -> Dict[str, Any]:
        now = time.time()
        if self.auto_cycle and (now - self.last_cycle_time >= self.cycle_interval_sec):
            self.profile_index = (self.profile_index + 1) % len(DATASET_PROFILES)
            self.last_cycle_time = now

        base = DATASET_PROFILES[self.profile_index]
        self.sequence = (self.sequence + 1) & 0x0F

        # Physiological micro-jitter to make PPG/Temp telemetry realistic
        hr_jitter = random.choice([-1, 0, 1]) if random.random() < 0.6 else 0
        spo2_jitter = random.choice([-1, 0, 1]) if random.random() < 0.3 else 0
        temp_jitter = round(random.uniform(-0.1, 0.1), 1) if random.random() < 0.4 else 0.0

        current_hr = max(30, min(220, base["hr"] + hr_jitter))
        current_spo2 = max(70, min(100, base["spo2"] + spo2_jitter))
        current_temp = round(max(34.0, min(42.0, base["temp_c"] + temp_jitter)), 1)
        current_imu = base["imu"]
        current_battery = base["battery"]
        alert_state = 1 if (base["alert"] == 1 or self.forced_alert) else 0

        telemetry = {
            "patient": base["patient"],
            "patient_name": f"Patient #{base['patient']}",
            "desc": base["desc"],
            "sensor_type": "PPG (Pulse/SpO2) + Temp + 3-Axis IMU",
            "hr": current_hr,
            "spo2": current_spo2,
            "temp_c": current_temp,
            "imu": current_imu,
            "imu_desc": IMU_DESCRIPTIONS[current_imu],
            "battery": current_battery,
            "alert": alert_state,
            "seq": self.sequence,
            "decoder": "simulated",
            "source": "simulation",
            "timestamp": time.strftime("%H:%M:%S"),
            "timestamp_ms": int(now * 1000)
        }
        return validate_telemetry(telemetry)[2]
