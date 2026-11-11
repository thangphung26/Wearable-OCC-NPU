"""Research-demonstration rules only; these are not clinical alarm limits."""
import math

HR_LOW = 50
HR_HIGH = 140
SPO2_LOW = 94
TEMP_HIGH_C = 39.0


def strict_int(value):
    if type(value) is not int:
        raise ValueError("Expected an integer (no strings, booleans or fractions)")
    return value


def strict_bool(value):
    if type(value) is not bool:
        raise ValueError("Expected a JSON boolean")
    return value


def dashboard_warnings(data):
    reasons = []
    if data.get("hr_valid", data["hr"] > 0) and data.get("ppg_valid", True):
        if 0 < data["hr"] < HR_LOW:
            reasons.append("HR below 50 bpm")
        if data["hr"] > HR_HIGH:
            reasons.append("HR above 140 bpm")
    if data.get("spo2_valid", data["spo2"] > 0) and data.get("ppg_valid", True) and 0 < data["spo2"] < SPO2_LOW:
        reasons.append("SpO2 below 94%")
    if data.get("temp_valid", data["temp_c"] > 0) and data["temp_c"] >= TEMP_HIGH_C:
        reasons.append("Temperature at least 39 C")
    if data["imu"] in (2, 3):
        reasons.append(f"IMU state {data['imu']}")
    return reasons


def validate_telemetry(payload):
    if not isinstance(payload, dict):
        return False, "Telemetry must be an object", {}
    required = ("patient", "hr", "spo2", "temp_c", "imu", "battery", "alert", "seq")
    try:
        clean = dict(payload)
        for key in required:
            if key == "temp_c":
                value = clean[key]
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError("Invalid temperature")
                clean[key] = round(value, 1)
            else:
                clean[key] = strict_int(clean[key])
        limits = {"patient": (0, 15), "hr": (0, 240), "spo2": (0, 100),
                  "temp_c": (0, 45), "imu": (0, 3), "battery": (0, 7),
                  "alert": (0, 1), "seq": (0, 15)}
        for key, (low, high) in limits.items():
            if not low <= clean[key] <= high:
                raise ValueError(f"Invalid {key}")
        for key, low in (("hr", 25), ("spo2", 50), ("temp_c", 30)):
            if 0 < clean[key] < low:
                raise ValueError(f"Invalid {key}; use zero for unavailable")
        for flag, default in (("ppg_valid", clean["hr"] > 0 and clean["spo2"] > 0),
                              ("hr_valid", clean["hr"] > 0), ("spo2_valid", clean["spo2"] > 0),
                              ("temp_valid", clean["temp_c"] > 0)):
            clean[flag] = strict_bool(clean.get(flag, default))
        if "crc_valid" in clean:
            strict_bool(clean["crc_valid"])
        clean["crc_status"] = ("valid" if clean["crc_valid"] else "rejected") if "crc_valid" in clean else "unverified"
        clean["tx_alert_bit"] = clean["alert"]
        clean["dashboard_warnings"] = dashboard_warnings(clean)
        clean["alert_reasons"] = clean["dashboard_warnings"] + (["Transmitted alert bit asserted"] if clean["alert"] else [])
        return True, "", clean
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        return False, str(exc), {}


def recovery_available(data):
    return (data["ppg_valid"] and data["hr_valid"] and data["spo2_valid"] and
            data["temp_valid"] and data["hr"] > 0 and data["spo2"] > 0 and data["temp_c"] > 0)
