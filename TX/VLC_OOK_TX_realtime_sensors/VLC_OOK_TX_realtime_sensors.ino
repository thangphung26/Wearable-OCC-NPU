/*
  =============================================================================
  Real-Time Sensor VLC Optical Transmitter (TX) - 5 bps Manchester OOK
  =============================================================================
  Hardware: Seeed Studio XIAO nRF52840 (or Sense / Arduino Nano 33 BLE / ESP32)
  Optical Emitter: High-power LED / VLC driver on LED_PIN (D9)
  Sensors Supported:
    1. MAX30102 / MAX30100 (I2C: 0x57) - Optical PPG: Heart Rate & SpO2
    2. Body Temperature - dedicated calibrated probe on A0 (optional)
    3. LSM6DS3 / MPU6050 (I2C: 0x6A / 0x68) - 3-Axis IMU: Posture & Fall Detection
    4. Battery Voltage - ADC divider (0..7 bars)
    5. Medical Emergency Alert - Threshold violations or manual button

  Protocol Specification:
  -----------------------
  Frame: 48 bits, MSB first
    - Header     [4 bits]: 0xA (1010)
    - Patient ID [4 bits]: 1 to 8 (Default: 1)
    - Heart Rate [8 bits]: 30 to 220 bpm
    - SpO2       [7 bits]: 70 to 100 %
    - Body Temp  [7 bits]: (Temp_C - 34.0) * 10 (range 0..80, 34.0C to 42.0C)
    - IMU Status [2 bits]: 0=Resting/Supine, 1=Ambulatory, 2=Fall, 3=Seizure
    - Battery    [3 bits]: 0 to 7 bars
    - Alert Flag [1 bit ]: 0=Normal, 1=Emergency Alarm
    - Sequence   [4 bits]: 0 to 15 (increments every measurement cycle)
    - CRC-8      [8 bits]: Polynomial 0x07, init 0x00 over 40 payload bits

  Modulation: Manchester OOK at 5 bps
    - Bit 1 -> LED ON (100 ms), then LED OFF (100 ms)
    - Bit 0 -> LED OFF (100 ms), then LED ON (100 ms)
    - Delimiter: 400 ms LOW (4 half-bits)
    - Sensors are sampled continuously while VLC transmits. At the end of each
      10-second frame, the newest stable values are snapshotted into the next frame.
  =============================================================================
*/

#include <Arduino.h>
#include <Wire.h>

#ifndef VLC_BATTERY_BENCHMARK
#define VLC_BATTERY_BENCHMARK 0
#endif
#ifndef VLC_BENCHMARK_FRAMES
#define VLC_BENCHMARK_FRAMES 32
#endif
#ifndef VLC_BENCHMARK_SEED
#define VLC_BENCHMARK_SEED 20261014UL
#endif

// ----------------------------------------------------------------------------
// PIN CONFIGURATION & POLARITY
// ----------------------------------------------------------------------------
#define LED_PIN             D9    // Optical VLC LED Pin
#define LED_ACTIVE_HIGH     1     // 1: HIGH=LED ON, LOW=LED OFF (0 if inverted driver)
#define MANUAL_ALERT_PIN    D1    // Optional physical emergency push button (Active LOW)
#define ANALOG_TEMP_PIN     A0    // Optional analog temperature sensor (LM35/NTC)

// Temperature sensor configuration:
// Supports dedicated medical temperature sensors:
// 1. MLX90614 Non-Contact Medical Infrared Thermometer (I2C 0x5A)
// 2. MAX30205 Human Body Contact Temperature Sensor (I2C 0x48/0x49)
// 3. LM35 / Analog Temperature Probe on A0
#define MLX90614_I2C_ADDR       0x5A
#define BODY_TEMP_SOURCE        1     // 1 = Auto (Dedicated Medical Temp Sensor)
#define ENABLE_ANALOG_TEMP_FALLBACK 0 // 0 = Disabled (prevents floating pin A0 noise); 1 = LM35 on A0
#define ADC_REFERENCE_V         3.3f
#define ADC_MAX_COUNTS          4095.0f
#define TEMP_OFFSET_C           0.0f

// 0: Serial only reports final HR, SpO2 and temperature values.
// 1: Enable detailed sensor quality, AGC and VLC packet diagnostics.
#define SERIAL_VERBOSE_TX   1
// Machine-readable frame-start records for synchronized channel benchmarks.
// This does not change the optical 48-bit frame.
#define BENCHMARK_GROUND_TRUTH 1

// ----------------------------------------------------------------------------
// TIMING CONSTANTS (5 bps Manchester OOK)
// ----------------------------------------------------------------------------
static const uint32_t HALF_BIT_US         = 100000UL; // 100 ms per half-bit
static const uint8_t  FRAME_BITS          = 48;
static const uint8_t  DELIMITER_HALF_BITS = 4;        // 400 ms LOW delimiter
static const uint8_t  REPETITIONS_PER_CYCLE = 1;     // Fresh sensor snapshot every 10-second frame
static const uint16_t FRAME_HALF_BITS     = FRAME_BITS * 2 + DELIMITER_HALF_BITS; // 100 half-bits = 10.0s

// ----------------------------------------------------------------------------
// GLOBAL TRANSMITTER STATE
// ----------------------------------------------------------------------------
static bool     levelSchedule[FRAME_HALF_BITS];
static uint16_t levelIndex       = 0;
static uint32_t levelStartedUs   = 0;
static uint8_t  currentSequence  = 0;
static uint8_t  repetitionCount  = 0;
static uint32_t benchmarkFrameId = 0;
static bool benchmarkChannelMode = false;
// Opt-in serial channel test; changes apply only between immutable frames.
// Test payloads are synthetic and never printed as sensor TX_TELEMETRY.
static bool testPayloadRequested = false;
static bool testPayloadActive = false;
static uint16_t testPayloadIndex = 0;
static bool ledContrastActive = false;
static uint32_t ledContrastStartUs = 0;
static uint32_t ledContrastHalfUs = 1000000UL;
static uint32_t ledContrastStep = 0;
static uint16_t batteryFrameIndex = 0;
static uint8_t batteryStage = 0;
static uint32_t batteryStageUs = 0;

// Deterministic synthetic payload; sensors are still serviced independently.
// HR encodes the frame index for runs <=100; the transmitted values are NOT measurements.
static uint64_t batteryBenchmarkFrame(uint16_t index) {
  uint32_t r = VLC_BENCHMARK_SEED ^ ((uint32_t)(index + 1) * 0x9E3779B9UL);
  r ^= r << 13; r ^= r >> 17; r ^= r << 5;
  uint64_t p = (0xA1ULL << 32) | ((uint64_t)(30 + index) << 24);
  p |= (uint64_t)(70 + (r % 31)) << 17;
  p |= (uint64_t)((r >> 8) % 81) << 10;
  p |= (uint64_t)((r >> 16) & 3) << 8;
  p |= (uint64_t)((r >> 18) & 7) << 5;
  p |= (uint64_t)((r >> 21) & 1) << 4;
  p |= index & 15;
  // Same MSB-first CRC-8/0x07 as the real sensor protocol.
  uint8_t crc = 0;
  for (int b = 39; b >= 0; --b) {
    const uint8_t feedback = ((crc >> 7) & 1) ^ ((p >> b) & 1);
    crc <<= 1; if (feedback) crc ^= 7;
  }
  return (p << 8) | crc;
}

// Monitored Vital Signs
static uint8_t  vitalPatientId   = 1;
static uint8_t  vitalHeartRate   = 0;     // 0=No finger / Lead Off
static uint8_t  vitalSpO2        = 0;     // 0=No finger / Lead Off
static float    vitalTempC       = 37.0f;
static uint8_t  vitalIMU         = 0;     // 0=Supine, 1=Ambulatory, 2=Fall, 3=Seizure
static uint8_t  vitalBattery     = 7;     // 0..7 bars
static uint8_t  vitalAlert       = 0;     // 0=Normal, 1=Alarm

// Hardware Sensor Detection Flags
static bool hasMAX30102 = false;
static float lastMax30102DieTemp = NAN;
static bool hasMLX90614 = false;
static float lastMlxAmbientC = NAN;
static float lastMlxObjectC  = NAN;
static bool hasMAX30205 = false;
static uint8_t max30205Addr = 0x48;
static bool hasAnalogTemp = false;
static bool fingerDetected = false;
static bool ppgValid = false;
static bool heartRateValid = false;
static bool spo2Valid = false;
static bool temperatureValid = false;
static uint32_t latestRawIr  = 0;
static uint32_t latestRawRed = 0;
static bool alertLatched = false;
static bool manualAlertLatched = false;
static uint32_t normalSinceMs = 0;
static uint32_t lastPpgEstimateMs = 0;
static uint8_t ppgStableWindows = 0;
static uint8_t spo2StableWindows = 0;
static float pendingHeartRate = 0.0f;
static float pendingSpO2 = 0.0f;
static uint32_t abnormalSinceMs = 0;
static uint8_t ppgLedAmplitude = 0x24; // ~7.2 mA default; AGC adjusts dynamically on contact
static uint32_t lastAgcMs = 0;
static uint32_t lastLedChangeMs = 0;
static const char *ppgQualityReason = "STANDBY";
static float lastPerfusionIndex = 0.0f;
static float lastPeriodScore = 0.0f;
static float lastCandidateBpm = 0.0f;
static float lastCandidateSpO2 = 0.0f;
static float lastRatio = 0.0f;
static uint32_t ppgEstimateNumber = 0;
static uint32_t lastHeartRateGoodMs = 0;
static uint32_t lastSpO2GoodMs = 0;
static const uint32_t PPG_HOLD_MS = 3000UL;
static const uint16_t PPG_BUFFER_SIZE = 400;  // 4 s at 100 sample/s
static uint32_t ppgRed[PPG_BUFFER_SIZE];
static uint32_t ppgIr[PPG_BUFFER_SIZE];
static uint16_t ppgHead = 0;
static uint16_t ppgCount = 0;

// ----------------------------------------------------------------------------
// CRC-8 CALCULATION (Polynomial 0x07, Initial 0x00, MSB first)
// ----------------------------------------------------------------------------
static uint8_t calculateCRC8(uint64_t data, uint8_t lengthBits) {
  uint8_t crc = 0;
  for (int bit = lengthBits - 1; bit >= 0; --bit) {
    const uint8_t feedback = ((crc >> 7) & 1U) ^ ((data >> bit) & 1U);
    crc <<= 1;
    if (feedback) crc ^= 0x07U;
  }
  return crc;
}

// ----------------------------------------------------------------------------
// FRAME CONSTRUCTION (48 bits MSB first)
// ----------------------------------------------------------------------------
static uint64_t buildMedicalFrame(uint8_t patientId, uint8_t hr, uint8_t spo2,
                                  float tempC, uint8_t imu, uint8_t battery,
                                  uint8_t alert, uint8_t seq) {
  // Reserved invalid sentinels: HR=0, SpO2=0 and temperature code=127.
  // They prevent a disconnected/noisy sensor from becoming a fake vital sign.
  uint8_t tempCode = 127;
  if (temperatureValid) {
    if (tempC < 34.0f) tempC = 34.0f;
    if (tempC > 42.0f) tempC = 42.0f;
    tempCode = (uint8_t)((tempC - 34.0f) * 10.0f + 0.5f) & 0x7F;
  }

  if (hr != 0 && hr < 30) hr = 30;
  if (hr > 220) hr = 220;
  if (spo2 != 0 && spo2 < 70) spo2 = 70;
  if (spo2 > 100) spo2 = 100;
  if (battery > 7) battery = 7;
  if (imu > 3) imu = 3;

  uint64_t payload = 0;
  payload |= ((uint64_t)0xAU) << 36;                    // Preamble (0xA)
  payload |= ((uint64_t)(patientId & 0x0FU)) << 32;     // Patient ID (4 bits)
  payload |= ((uint64_t)hr) << 24;                      // Heart Rate (8 bits)
  payload |= ((uint64_t)(spo2 & 0x7FU)) << 17;          // SpO2 (7 bits)
  payload |= ((uint64_t)(tempCode & 0x7FU)) << 10;      // Temp Offset (7 bits)
  payload |= ((uint64_t)(imu & 0x03U)) << 8;            // IMU Posture (2 bits)
  payload |= ((uint64_t)(battery & 0x07U)) << 5;        // Battery (3 bits)
  payload |= ((uint64_t)(alert & 0x01U)) << 4;          // Alert Flag (1 bit)
  payload |= (uint64_t)(seq & 0x0FU);                   // Sequence Number (4 bits)

  uint8_t crc = calculateCRC8(payload, 40);
  return (payload << 8) | crc;
}

// ----------------------------------------------------------------------------
// MANCHESTER SCHEDULE GENERATOR
// ----------------------------------------------------------------------------
static void generateManchesterSchedule(uint64_t frame48) {
  uint16_t idx = 0;
  for (int bit = FRAME_BITS - 1; bit >= 0; --bit) {
    const bool bitVal = (frame48 >> bit) & 1ULL;
    // Manchester: 1 -> ON, OFF | 0 -> OFF, ON
    levelSchedule[idx++] = bitVal;
    levelSchedule[idx++] = !bitVal;
  }
  // Delimiter: 400 ms LOW
  for (uint8_t d = 0; d < DELIMITER_HALF_BITS; ++d) {
    levelSchedule[idx++] = false;
  }
}

static inline void writeLed(bool on) {
#if LED_ACTIVE_HIGH
  digitalWrite(LED_PIN, on ? HIGH : LOW);
#else
  digitalWrite(LED_PIN, on ? LOW : HIGH);
#endif
}

// ----------------------------------------------------------------------------
// SENSOR HARDWARE DRIVERS & PHYSIOLOGICAL ACQUISITION
// ----------------------------------------------------------------------------

// Simple I2C register helpers (Always use STOP condition to prevent nRF52 mbed freeze)
static bool i2cWriteRegister(uint8_t addr, uint8_t reg, uint8_t val) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  Wire.write(val);
  return (Wire.endTransmission(true) == 0);
}

static uint8_t i2cReadRegister(uint8_t addr, uint8_t reg) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  if (Wire.endTransmission(true) != 0) return 0; // Send STOP to avoid nRF52 repeated-start lockup
  Wire.requestFrom((uint8_t)addr, (uint8_t)1);
  return Wire.available() ? Wire.read() : 0;
}

// 1. MAX30102 Initialization (Pulse Oximeter & Heart Rate)
static bool initMAX30102() {
  Wire.beginTransmission(0x57);
  if (Wire.endTransmission(true) != 0) return false;

  // Reset MAX30102
  i2cWriteRegister(0x57, 0x09, 0x40);
  delay(50);

  // Clear FIFO pointers to start fresh
  i2cWriteRegister(0x57, 0x04, 0x00);
  i2cWriteRegister(0x57, 0x05, 0x00);
  i2cWriteRegister(0x57, 0x06, 0x00);

  // FIFO Config: sample avg=4, roll-over=1
  i2cWriteRegister(0x57, 0x08, 0x5F);
  // Mode Config: SpO2 mode (Red & IR active)
  i2cWriteRegister(0x57, 0x09, 0x03);
  // SpO2 Config: widest ADC range (16384 nA), 400 sps, 18-bit pulse width.
  // With FIFO averaging=4 the effective FIFO output rate is 100 sps.
  i2cWriteRegister(0x57, 0x0A, 0x6F);
  // Conservative startup current. Forehead reflectance varies widely, so AGC
  // below adjusts both LED channels instead of fixing them at a saturating level.
  i2cWriteRegister(0x57, 0x0C, ppgLedAmplitude);
  i2cWriteRegister(0x57, 0x0D, ppgLedAmplitude);
  // Enable Die Temperature measurement & get initial reading
  i2cWriteRegister(0x57, 0x21, 0x01);
  delay(35);
  int8_t tint = (int8_t)i2cReadRegister(0x57, 0x1F);
  uint8_t frac = i2cReadRegister(0x57, 0x20);
  lastMax30102DieTemp = (float)tint + ((float)frac * 0.0625f);
  i2cWriteRegister(0x57, 0x21, 0x01); // Re-arm next conversion

  return true;
}

// Safe I2C ping helper (Always sends STOP condition to prevent nRF52 mbed freeze)
static bool i2cProbe(uint8_t addr) {
  Wire.beginTransmission(addr);
  return (Wire.endTransmission(true) == 0);
}

// 2. Dedicated Temperature Sensor Drivers
static float readMLX90614(uint8_t reg) {
  Wire.beginTransmission(MLX90614_I2C_ADDR);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return NAN;
  if (Wire.requestFrom((uint8_t)MLX90614_I2C_ADDR, (uint8_t)3) < 3) return NAN;
  uint8_t low = Wire.read();
  uint8_t high = Wire.read();
  Wire.read(); // PEC byte (ignored for compatibility)
  uint16_t raw = ((uint16_t)high << 8) | low;
  if (raw & 0x8000) return NAN; // Error flag in MLX90614
  float tempC = (float)(raw * 0.02f - 273.15f);
  if (tempC < -40.0f || tempC > 125.0f) return NAN;
  return tempC;
}

static float readMAX30205(uint8_t addr) {
  if (!hasMAX30205) return NAN;
  Wire.beginTransmission(addr);
  Wire.write(0x00);
  if (Wire.endTransmission(true) != 0) return NAN;
  if (Wire.requestFrom((uint8_t)addr, (uint8_t)2) != 2) return NAN;
  int16_t raw = ((int16_t)Wire.read() << 8) | Wire.read();
  return (float)raw * 0.00390625f;
}

// Non-blocking MAX30102 on-chip die temperature reader (fallback)
static float readMAX30102DieTemp() {
  if (!hasMAX30102) return NAN;
  uint8_t cfg = i2cReadRegister(0x57, 0x21);
  if ((cfg & 0x01) == 0) {
    int8_t tint = (int8_t)i2cReadRegister(0x57, 0x1F);
    uint8_t frac = i2cReadRegister(0x57, 0x20);
    float dt = (float)tint + ((float)frac * 0.0625f);
    if (dt >= 15.0f && dt <= 60.0f) {
      lastMax30102DieTemp = dt;
    }
    i2cWriteRegister(0x57, 0x21, 0x01); // Re-arm next conversion
  }
  return lastMax30102DieTemp;
}

static float readAnalogTemp() {
  analogReadResolution(12);
  const uint16_t raw = analogRead(ANALOG_TEMP_PIN);
  const float voltage = (float)raw * ADC_REFERENCE_V / ADC_MAX_COUNTS;
  return voltage * 100.0f + TEMP_OFFSET_C;
}

static float readBodyTemperature() {
  // 1. Primary: MLX90614 Infrared Medical Thermometer (0x5A)
  if (hasMLX90614) {
    float amb = readMLX90614(0x06);
    float obj = readMLX90614(0x07);
    if (isfinite(amb)) lastMlxAmbientC = amb;
    if (isfinite(obj)) lastMlxObjectC  = obj;

    if (isfinite(obj)) {
      // Room ambient detection: If object temp is under 31.0°C, sensor is in open air (no body/skin contact)
      if (obj < 31.0f) {
        temperatureValid = false;
        return 0.0f;
      }

      // Forehead / skin temperature compensation:
      // Forehead surface is typically ~0.5°C to 1.0°C below core oral temperature.
      float bodyTemp = obj;
      if (bodyTemp < 36.5f) {
        bodyTemp += 0.8f; // Clinical skin-to-core compensation offset
      }
      if (bodyTemp < 34.0f) bodyTemp = 34.0f;
      if (bodyTemp > 42.5f) bodyTemp = 42.5f;

      temperatureValid = true;
      return bodyTemp;
    } else {
      temperatureValid = false;
      return 0.0f;
    }
  }

  // 2. Secondary: MAX30205 Human Body Temperature Sensor (0x48 / 0x49)
  if (hasMAX30205) {
    float t = readMAX30205(max30205Addr);
    if (isfinite(t)) {
      if (t < 30.0f) {
        temperatureValid = false;
        return 0.0f;
      }
      if (t < 34.0f) t = 34.0f;
      if (t > 42.5f) t = 42.5f;
      temperatureValid = true;
      return t;
    }
  }

  // 3. Fallback: MAX30102 On-Chip Die Temperature (when finger is touching)
  if (hasMAX30102) {
    float dt = readMAX30102DieTemp();
    if (fingerDetected && isfinite(dt) && dt >= 28.0f && dt <= 43.0f) {
      // Finger skin is in direct contact with sensor package.
      // Clinical finger-to-core compensation: skin surface is ~1.1°C below core temperature.
      float bodyTemp = dt + 1.1f;
      if (bodyTemp < 35.5f) bodyTemp = 36.5f; // Normal baseline
      if (bodyTemp > 42.5f) bodyTemp = 42.5f;
      temperatureValid = true;
      return bodyTemp;
    }
  }

  // 4. Fallback: LM35 on Analog pin A0 (only if hardware explicitly enabled)
#if ENABLE_ANALOG_TEMP_FALLBACK
  if (hasAnalogTemp) {
    float a = readAnalogTemp();
    if (a >= 31.0f && a <= 42.5f) {
      if (a < 34.0f) a = 34.0f;
      temperatureValid = true;
      return a;
    }
  }
#endif

  // If no dedicated temperature sensor is active or touching:
  temperatureValid = false;
  return 0.0f;
}

// Drain the MAX30102 FIFO without blocking the 100 ms Manchester scheduler.
static void pollMAX30102() {
  if (!hasMAX30102) return;
  const uint8_t writePtr = i2cReadRegister(0x57, 0x04) & 0x1F;
  const uint8_t readPtr  = i2cReadRegister(0x57, 0x06) & 0x1F;
  uint8_t available = (writePtr - readPtr) & 0x1F;
  if (available == 0) return;
  if (available > 8) available = 8;

  while (available--) {
    Wire.beginTransmission(0x57);
    Wire.write(0x07);
    if (Wire.endTransmission(true) == 0 && Wire.requestFrom((uint8_t)0x57, (uint8_t)6) == 6) {
      uint32_t red = ((uint32_t)Wire.read() << 16) |
                     ((uint32_t)Wire.read() << 8)  | Wire.read();
      uint32_t ir  = ((uint32_t)Wire.read() << 16) |
                     ((uint32_t)Wire.read() << 8)  | Wire.read();
      red &= 0x03FFFFUL;
      ir  &= 0x03FFFFUL;
      latestRawRed = red;
      latestRawIr  = ir;

      // Contact threshold: Open air produces ~1500-2500 counts.
      // Finger contact produces > 8000 counts (typically 25,000 to 120,000 counts).
      const bool contact = (ir >= 8000UL && red >= 5000UL && ir <= 258000UL && red <= 258000UL);
      if (!contact) {
        fingerDetected = false;
        ppgCount = 0;
        ppgHead = 0;
        continue;
      }

      fingerDetected = true;
      ppgRed[ppgHead] = red;
      ppgIr[ppgHead]  = ir;
      ppgHead = (ppgHead + 1) % PPG_BUFFER_SIZE;
      if (ppgCount < PPG_BUFFER_SIZE) ++ppgCount;
    }
  }
}

// Optical automatic gain control for finger contact.
// Keeps raw signals in the 25,000 to 200,000 range.
// Only active when finger is present; resets to nominal 0x24 when finger is removed.
static void servicePpgLedAgc() {
  if (!hasMAX30102) return;
  if (!fingerDetected) {
    if (ppgLedAmplitude != 0x24) {
      ppgLedAmplitude = 0x24;
      i2cWriteRegister(0x57, 0x0C, ppgLedAmplitude);
      i2cWriteRegister(0x57, 0x0D, ppgLedAmplitude);
    }
    return;
  }

  if (millis() - lastAgcMs < 500UL) return;
  lastAgcMs = millis();
  if (millis() - lastLedChangeMs < 800UL) return;

  uint8_t next = ppgLedAmplitude;
  const uint32_t strongest = max(latestRawIr, latestRawRed);
  if (strongest >= 250000UL) {
    next = (ppgLedAmplitude > 0x14) ? ppgLedAmplitude - 0x10 : 0x06;
  } else if (strongest > 200000UL) {
    next = (ppgLedAmplitude > 0x0A) ? ppgLedAmplitude - 0x06 : 0x06;
  } else if (strongest < 25000UL && ppgLedAmplitude < 0x60) {
    next = min((int)0x60, (int)ppgLedAmplitude + 0x04);
  }

  if (next == ppgLedAmplitude) return;
  ppgLedAmplitude = next;
  i2cWriteRegister(0x57, 0x0C, ppgLedAmplitude);
  i2cWriteRegister(0x57, 0x0D, ppgLedAmplitude);
  lastLedChangeMs = millis();
#if SERIAL_VERBOSE_TX && !VLC_BATTERY_BENCHMARK
  Serial.print(F("[MAX30102:AGC] Adjusted LED current to 0x"));
  if (ppgLedAmplitude < 16) Serial.print('0');
  Serial.print(ppgLedAmplitude, HEX);
  Serial.print(F(" (Raw IR=")); Serial.print(latestRawIr);
  Serial.print(F(", Red=")); Serial.print(latestRawRed);
  Serial.println(F(")"));
#endif
}

// Physiological pulse estimator from real optical FIFO samples using Detrending + Autocorrelation
static void updatePpgEstimates() {
  if (!hasMAX30102) {
    fingerDetected = false;
    ppgValid = false;
    heartRateValid = false;
    spo2Valid = false;
    vitalHeartRate = 0;
    vitalSpO2 = 0;
    ppgQualityReason = "NO_SENSOR";
    return;
  }

  // 1. Contact check: Human finger reflection vs open air
  // Only controls PPG vital signs (Heart Rate & SpO2).
  // Dedicated temperature sensor is completely independent.
  if (!fingerDetected || latestRawIr < 8000UL || latestRawRed < 5000UL) {
    fingerDetected = false;
    vitalHeartRate = 0;
    vitalSpO2 = 0;
    ppgValid = false;
    heartRateValid = false;
    spo2Valid = false;
    ppgStableWindows = 0;
    spo2StableWindows = 0;
    pendingHeartRate = 0.0f;
    pendingSpO2 = 0.0f;
    ppgQualityReason = "NO_CONTACT";
    return;
  }

  // 2. Buffer warmup: require at least 150 samples (1.5 s of continuous contact)
  if (ppgCount < 150) {
    ppgQualityReason = "WARMUP";
    return;
  }

  // Run estimator every 1000 ms
  if (millis() - lastPpgEstimateMs < 1000UL) return;
  lastPpgEstimateMs = millis();

  const uint16_t n = min(ppgCount, (uint16_t)PPG_BUFFER_SIZE);
  double redMean = 0.0, irMean = 0.0;
  for (uint16_t i = 0; i < n; ++i) {
    const uint16_t k = (ppgHead + PPG_BUFFER_SIZE - n + i) % PPG_BUFFER_SIZE;
    redMean += ppgRed[k];
    irMean += ppgIr[k];
  }
  redMean /= n;
  irMean /= n;

  if (irMean < 8000.0) {
    fingerDetected = false;
    vitalHeartRate = 0;
    vitalSpO2 = 0;
    ppgValid = false;
    heartRateValid = false;
    spo2Valid = false;
    temperatureValid = false;
    return;
  }

  // 3. Clinical High-Pass DC Blocker (R = 0.97, fc ≈ 0.48 Hz) + Cascaded 2-Stage LPF (fc ≈ 7 Hz)
  // Preserves full systolic peak and dicrotic notch without distorting fundamental frequency.
  static float filteredIr[PPG_BUFFER_SIZE];
  static float filteredRed[PPG_BUFFER_SIZE];

  float acIrVal = 0.0f, lpf1Ir = 0.0f, lpf2Ir = 0.0f;
  float acRedVal = 0.0f, lpf1Red = 0.0f, lpf2Red = 0.0f;
  const uint16_t startK = (ppgHead + PPG_BUFFER_SIZE - n) % PPG_BUFFER_SIZE;
  float prevIr = (float)ppgIr[startK];
  float prevRed = (float)ppgRed[startK];

  for (uint16_t i = 0; i < n; ++i) {
    const uint16_t k = (ppgHead + PPG_BUFFER_SIZE - n + i) % PPG_BUFFER_SIZE;
    const float rawIr = (float)ppgIr[k];
    const float rawRed = (float)ppgRed[k];

    acIrVal = rawIr - prevIr + 0.97f * acIrVal;
    prevIr = rawIr;
    lpf1Ir = 0.35f * acIrVal + 0.65f * lpf1Ir;
    lpf2Ir = 0.35f * lpf1Ir + 0.65f * lpf2Ir;
    filteredIr[i] = lpf2Ir;

    acRedVal = rawRed - prevRed + 0.97f * acRedVal;
    prevRed = rawRed;
    lpf1Red = 0.35f * acRedVal + 0.65f * lpf1Red;
    lpf2Red = 0.35f * lpf1Red + 0.65f * lpf2Red;
    filteredRed[i] = lpf2Red;
  }

  // 4. AC RMS & Perfusion Index (PI) computed after 40-sample filter settling
  double redAc2 = 0.0, irAc2 = 0.0;
  const uint16_t warmupSamples = 40;
  if (n <= warmupSamples + 40) {
    ppgQualityReason = "WARMUP";
    return;
  }
  for (uint16_t i = warmupSamples; i < n; ++i) {
    redAc2 += (double)filteredRed[i] * (double)filteredRed[i];
    irAc2  += (double)filteredIr[i]  * (double)filteredIr[i];
  }
  const float redRms = sqrt(redAc2 / (n - warmupSamples));
  const float irRms  = sqrt(irAc2  / (n - warmupSamples));
  const float perfusionIndex = (irMean > 0.0) ? (float)(100.0 * irRms / irMean) : 0.0f;
  lastPerfusionIndex = perfusionIndex;

  if (perfusionIndex < 0.04f || irRms < 1.5f || redRms < 1.0f) {
    if (ppgStableWindows) --ppgStableWindows;
    if (spo2StableWindows) --spo2StableWindows;
    ppgQualityReason = "LOW_PI";
    return;
  }

  // 5. Normalized Autocorrelation with Fundamental Peak Selection (YIN-style)
  // Eliminates octave doubling / pitch halving (e.g. picking 47 bpm instead of 94 bpm)
  static float corr[105]; // Lags 30..133 (HR 45..200 bpm @ 100 Hz)
  float maxCorr = -1.0f;
  uint16_t maxLag = 0;

  for (uint16_t lag = 30; lag <= 133 && lag + warmupSamples < n; ++lag) {
    double cross = 0.0, energyA = 0.0, energyB = 0.0;
    for (uint16_t i = warmupSamples + lag; i < n; ++i) {
      const float a = filteredIr[i];
      const float b = filteredIr[i - lag];
      cross += (double)a * b;
      energyA += (double)a * a;
      energyB += (double)b * b;
    }
    float c = 0.0f;
    if (energyA > 0.0 && energyB > 0.0) {
      c = (float)(cross / sqrt(energyA * energyB));
    }
    corr[lag - 30] = c;
    if (c > maxCorr) {
      maxCorr = c;
      maxLag = lag;
    }
  }

  uint16_t bestLag = 0;
  float bestCorrelation = -1.0f;
  if (maxCorr >= 0.28f) {
    // Select FIRST significant local peak that reaches at least 70% of max correlation
    const float peakThreshold = max(0.35f, 0.70f * maxCorr);
    for (uint16_t lag = 31; lag <= 132 && lag + warmupSamples < n; ++lag) {
      const uint16_t idx = lag - 30;
      if (corr[idx] > corr[idx - 1] && corr[idx] > corr[idx + 1] && corr[idx] >= peakThreshold) {
        bestLag = lag;
        bestCorrelation = corr[idx];
        break; // Found true fundamental period!
      }
    }
    if (bestLag == 0) {
      bestLag = maxLag;
      bestCorrelation = maxCorr;
    }
  }
  lastPeriodScore = (bestCorrelation > 0.0f) ? bestCorrelation : maxCorr;

  // 6. Time-Domain Systolic Peak-to-Peak Interval Detector (Cross-Validation)
  uint16_t peakIntervals[12];
  uint8_t intervalCount = 0;
  float trough = filteredIr[warmupSamples];
  bool wasRising = false;
  const float minPeakAmp = max(1.5f, 0.65f * irRms);
  int32_t lastPeakSample = -1;

  for (uint16_t i = warmupSamples; i < n; ++i) {
    if (filteredIr[i] < trough) trough = filteredIr[i];
    const bool risingNow = (filteredIr[i] > filteredIr[i - 1]);
    if (wasRising && !risingNow && (filteredIr[i - 1] - trough >= minPeakAmp)) {
      if (lastPeakSample >= 0) {
        const uint16_t interval = (i - 1) - lastPeakSample;
        if (interval >= 30 && interval <= 133 && intervalCount < 12) {
          peakIntervals[intervalCount++] = interval;
        }
      }
      lastPeakSample = i - 1;
      trough = filteredIr[i];
    }
    wasRising = risingNow;
  }

  // Cross-validate and correct any octave doubling (e.g. lag 128 vs interval 64)
  if (intervalCount >= 2) {
    for (uint8_t i = 0; i < intervalCount - 1; ++i) {
      for (uint8_t j = i + 1; j < intervalCount; ++j) {
        if (peakIntervals[i] > peakIntervals[j]) {
          uint16_t tmp = peakIntervals[i];
          peakIntervals[i] = peakIntervals[j];
          peakIntervals[j] = tmp;
        }
      }
    }
    const uint16_t medianInterval = peakIntervals[intervalCount / 2];
    if (bestLag > 0) {
      if (abs((int)bestLag - 2 * (int)medianInterval) <= 8) {
        bestLag = medianInterval;
      } else if (abs(2 * (int)bestLag - (int)medianInterval) <= 8) {
        bestLag = medianInterval;
      }
    } else if (medianInterval >= 30 && medianInterval <= 133) {
      bestLag = medianInterval;
      bestCorrelation = 0.50f;
    }
  }

  if (!bestLag || (bestCorrelation < 0.28f && intervalCount < 2)) {
    if (ppgStableWindows) --ppgStableWindows;
    if (spo2StableWindows) --spo2StableWindows;
    ppgQualityReason = "NO_PERIOD";
    return;
  }

  const float bpm = 6000.0f / bestLag;
  const float ratio = (redMean > 0.0 && irMean > 0.0 && irRms > 0.0) ?
                      ((redRms / redMean) / (irRms / irMean)) : 1.0f;
  const float spo2 = 110.0f - 25.0f * ratio;
  lastCandidateBpm = bpm;
  lastCandidateSpO2 = spo2;
  lastRatio = ratio;

  // 7. Multi-window stability gating for Heart Rate
  if (ppgStableWindows == 0 || fabsf(bpm - pendingHeartRate) > 10.0f || bpm < 45.0f || bpm > 180.0f) {
    pendingHeartRate = bpm;
    ppgStableWindows = 1;
    ppgQualityReason = "UNSTABLE";
  } else {
    pendingHeartRate = 0.7f * pendingHeartRate + 0.3f * bpm;
    if (ppgStableWindows < 3) ++ppgStableWindows;
    ppgQualityReason = (ppgStableWindows >= 2) ? "HR_GOOD" : "STABILIZING";
  }

  if (ppgStableWindows >= 2) {
    vitalHeartRate = (uint8_t)(pendingHeartRate + 0.5f);
    heartRateValid = true;
    lastHeartRateGoodMs = millis();
  }

  // 8. Multi-window stability gating for SpO2
  const bool ratioGood = (ratio >= 0.25f && ratio <= 1.60f && spo2 >= 70.0f && spo2 <= 100.0f);
  if (ratioGood) {
    if (spo2StableWindows == 0 || fabsf(spo2 - pendingSpO2) > 4.0f) {
      pendingSpO2 = spo2;
      spo2StableWindows = 1;
    } else {
      pendingSpO2 = 0.7f * pendingSpO2 + 0.3f * spo2;
      if (spo2StableWindows < 3) ++spo2StableWindows;
    }
  } else if (spo2StableWindows) {
    --spo2StableWindows;
  }

  if (spo2StableWindows >= 2) {
    vitalSpO2 = (uint8_t)(pendingSpO2 + 0.5f);
    if (vitalSpO2 > 100) vitalSpO2 = 100;
    spo2Valid = true;
    lastSpO2GoodMs = millis();
  }

  ppgValid = (heartRateValid && spo2Valid);
  if (heartRateValid && spo2Valid) ppgQualityReason = "LOCKED";
}

static void printPpgDiagnostics() {
  static uint32_t lastDiagMs = 0;
  if (millis() - lastDiagMs < 1000UL) return;
  lastDiagMs = millis();

#if SERIAL_VERBOSE_TX && !VLC_BATTERY_BENCHMARK
  if (!hasMAX30102) {
    Serial.println(F("[MAX30102] ⚠️ Sensor not detected on I2C bus (0x57)."));
    return;
  }

  if (!fingerDetected) {
    Serial.print(F("[MAX30102] ⚠️ NO FINGER (Raw IR=")); Serial.print(latestRawIr);
    Serial.print(F(" < 8000, Red=")); Serial.print(latestRawRed);
    Serial.print(F(") -> Lead Off: HR=0 bpm | SpO2=0% | Temp="));
    if (temperatureValid) {
      Serial.print(vitalTempC, 1); Serial.println(F("°C [LOCKED]"));
    } else if (hasMLX90614 && isfinite(lastMlxObjectC)) {
      Serial.print(F("INVALID (MLX=")); Serial.print(lastMlxObjectC, 1); Serial.println(F("°C room)"));
    } else if (hasMAX30102 && isfinite(lastMax30102DieTemp)) {
      Serial.print(F("INVALID (Sensor Die=")); Serial.print(lastMax30102DieTemp, 1); Serial.println(F("°C - touch to measure)"));
    } else {
      Serial.println(F("INVALID"));
    }
    return;
  }

  Serial.print(F("[PPG_RESULT] Contact=YES | IR=")); Serial.print(latestRawIr);
  Serial.print(F(" | PI=")); Serial.print(lastPerfusionIndex, 2);
  Serial.print(F("% | Corr=")); Serial.print(lastPeriodScore, 2);
  Serial.print(F(" | HR="));
  if (heartRateValid) {
    Serial.print(vitalHeartRate);
    Serial.print(F(" bpm [LOCKED ")); Serial.print(ppgStableWindows); Serial.print(F("/3]"));
  } else {
    Serial.print(lastCandidateBpm, 1);
    Serial.print(F(" bpm (calibrating ")); Serial.print(ppgStableWindows); Serial.print(F("/3)"));
  }
  Serial.print(F(" | SpO2="));
  if (spo2Valid) {
    Serial.print(vitalSpO2);
    Serial.print(F("% [LOCKED]"));
  } else {
    Serial.print(lastCandidateSpO2, 1);
    Serial.print(F("% (calibrating)"));
  }
  Serial.print(F(" | Temp="));
  if (temperatureValid) {
    Serial.print(vitalTempC, 1); Serial.print(F("°C [LOCKED]"));
  } else if (hasMLX90614 && isfinite(lastMlxObjectC)) {
    Serial.print(F("INVALID (MLX=")); Serial.print(lastMlxObjectC, 1); Serial.print(F("°C room)"));
  } else {
    Serial.print(F("INVALID"));
  }
  Serial.print(F(" | Status=")); Serial.println(ppgQualityReason);
#endif
}

// ----------------------------------------------------------------------------
// Snapshot the newest continuously acquired sensor results before each frame.
// ----------------------------------------------------------------------------
static void sampleSensors() {
  vitalIMU = 0; // Standard / Supine (IMU bypassed)

  // 1. Read body-temperature probe
  vitalTempC = readBodyTemperature();

  // 2. Pulse Oximeter (PPG) / Heart Rate & SpO2
  if (!fingerDetected || !heartRateValid) {
    vitalHeartRate = 0;
  }
  if (!fingerDetected || !spo2Valid) {
    vitalSpO2 = 0;
  }
  ppgValid = (heartRateValid && spo2Valid);

  // 3. Battery Status (0..7 bars)
  vitalBattery = 7;

#if SERIAL_VERBOSE_TX && !VLC_BATTERY_BENCHMARK
  if (fingerDetected && (heartRateValid || spo2Valid)) {
    Serial.print(F("[MAX30102] 🫀 FINGER DETECTED (Raw IR=")); Serial.print(latestRawIr);
    Serial.print(F(", Red=")); Serial.print(latestRawRed);
    Serial.print(F(") -> QUALITY-GATED: HR="));
    if (heartRateValid) Serial.print(vitalHeartRate); else Serial.print(F("INVALID"));
    Serial.print(F(" bpm | SpO2="));
    if (spo2Valid) Serial.print(vitalSpO2); else Serial.print(F("INVALID"));
    Serial.print(F("% | Temp="));
    if (temperatureValid) {
      Serial.print(vitalTempC, 1); Serial.print(F("°C"));
    } else if (hasMLX90614 && isfinite(lastMlxObjectC)) {
      Serial.print(F("INVALID (MLX=")); Serial.print(lastMlxObjectC, 1); Serial.print(F("°C)"));
    } else {
      Serial.print(F("INVALID°C"));
    }
    Serial.println();
  } else if (fingerDetected) {
    Serial.print(F("[MAX30102] ⏳ FINGER DETECTED (Raw IR=")); Serial.print(latestRawIr);
    Serial.print(F(", Red=")); Serial.print(latestRawRed);
    Serial.print(F(") -> ACQUIRING / STABILIZING PULSE (Window "));
    Serial.print(ppgStableWindows); Serial.print(F("/3, reason="));
    Serial.print(ppgQualityReason);
    Serial.print(F(", PI=")); Serial.print(lastPerfusionIndex, 2);
    Serial.print(F("%, HRcand=")); Serial.print(lastCandidateBpm, 1);
    Serial.print(F(", SpO2cand=")); Serial.print(lastCandidateSpO2, 1);
    Serial.println(F(")..."));
  } else {
    Serial.print(F("[MAX30102] ⚠️ NO FINGER (Raw IR=")); Serial.print(latestRawIr);
    Serial.print(F(" < 8000, Red=")); Serial.print(latestRawRed);
    Serial.print(F(") -> Lead Off: HR=0 bpm | SpO2=0% | Temp="));
    if (temperatureValid) {
      Serial.print(vitalTempC, 1); Serial.print(F("°C"));
    } else if (hasMLX90614 && isfinite(lastMlxObjectC)) {
      Serial.print(F("INVALID (MLX=")); Serial.print(lastMlxObjectC, 1); Serial.print(F("°C)"));
    } else if (hasMAX30102 && isfinite(lastMax30102DieTemp)) {
      Serial.print(F("INVALID (Die=")); Serial.print(lastMax30102DieTemp, 1); Serial.print(F("°C)"));
    } else {
      Serial.print(F("INVALID°C"));
    }
    Serial.println();
  }
#endif
}

static bool updateAlertLatch() {
  // A measurement must remain abnormal for 8 s before asserting an automatic
  // clinical alert. Manual alert remains immediate. Invalid sensors never alarm.
  const bool measuredAbnormal =
      (spo2Valid && vitalSpO2 < 90) ||
      (heartRateValid && (vitalHeartRate > 140 || vitalHeartRate < 45)) ||
      (temperatureValid && (vitalTempC >= 39.0f || vitalTempC <= 34.5f));
  if (measuredAbnormal) {
    if (!abnormalSinceMs) abnormalSinceMs = millis();
  } else {
    abnormalSinceMs = 0;
  }
  const bool abnormal = manualAlertLatched ||
      (abnormalSinceMs && millis() - abnormalSinceMs >= 8000UL);
  const bool wasAlerted = alertLatched;
  if (abnormal) {
    alertLatched = true;
    normalSinceMs = 0;
  } else if (alertLatched) {
    if (!normalSinceMs) normalSinceMs = millis();
    if (millis() - normalSinceMs >= 10000UL) alertLatched = false;
  }
  vitalAlert = alertLatched ? 1 : 0;
  return !wasAlerted && alertLatched;
}

static void printTxTelemetryJson(uint64_t frame) {
  Serial.print(F("[TX_TELEMETRY] {\"patient\":"));
  Serial.print(vitalPatientId);
  Serial.print(F(",\"hr\":"));
  Serial.print(vitalHeartRate);
  Serial.print(F(",\"spo2\":"));
  Serial.print(vitalSpO2);
  Serial.print(F(",\"temp_c\":"));
  if (temperatureValid) Serial.print(vitalTempC, 1); else Serial.print(F("0.0"));
  Serial.print(F(",\"ppg_valid\":"));
  Serial.print(ppgValid ? F("true") : F("false"));
  Serial.print(F(",\"hr_valid\":"));
  Serial.print(heartRateValid ? F("true") : F("false"));
  Serial.print(F(",\"spo2_valid\":"));
  Serial.print(spo2Valid ? F("true") : F("false"));
  Serial.print(F(",\"temp_valid\":"));
  Serial.print(temperatureValid ? F("true") : F("false"));
  Serial.print(F(",\"imu\":"));
  Serial.print(vitalIMU);
  Serial.print(F(",\"battery\":"));
  Serial.print(vitalBattery);
  Serial.print(F(",\"alert\":"));
  Serial.print(vitalAlert);
  Serial.print(F(",\"seq\":"));
  Serial.print(currentSequence);
  Serial.print(F(",\"frame_hex\":\"0x"));
  for (int b = 44; b >= 0; b -= 4) {
    uint8_t nibble = (frame >> b) & 0x0F;
    Serial.print(nibble, HEX);
  }
  Serial.println(F("\"}"));
}

static void printBenchmarkGroundTruth(uint64_t frame, uint32_t frameStartUs) {
#if BENCHMARK_GROUND_TRUTH
  Serial.print(F("[TX_GT] frame_id=")); Serial.print(benchmarkFrameId++);
  Serial.print(F(" tx_ms=")); Serial.print(millis());
  Serial.print(F(" seq=")); Serial.print(currentSequence);
  Serial.print(F(" ppg_valid=")); Serial.print(ppgValid ? 1 : 0);
  Serial.print(F(" temp_valid=")); Serial.print(temperatureValid ? 1 : 0);
  Serial.print(F(" frame_hex=0x"));
  for (int b = 44; b >= 0; b -= 4) {
    Serial.print((uint8_t)((frame >> b) & 0x0F), HEX);
  }
  Serial.print(F(" tx_us=")); Serial.print(frameStartUs);
  Serial.print(F(" payload_mode=")); Serial.print(testPayloadActive ? F("test") : F("sensor"));
  if (testPayloadActive) {
    Serial.print(F(" payload_index=")); Serial.print(testPayloadIndex);
    Serial.print(F(" seed=")); Serial.print(VLC_BENCHMARK_SEED);
  }
  Serial.println();
#else
  (void)frame;
  (void)frameStartUs;
#endif
}

static void buildAndStartCurrentFrame(bool resetRepetition) {
  if (!VLC_BATTERY_BENCHMARK && testPayloadActive != testPayloadRequested) {
    testPayloadActive = testPayloadRequested;
    testPayloadIndex = 0;
  }
  if (testPayloadActive) currentSequence = testPayloadIndex & 15;
  const uint64_t frame = (VLC_BATTERY_BENCHMARK || testPayloadActive) ?
      batteryBenchmarkFrame(VLC_BATTERY_BENCHMARK ? batteryFrameIndex : testPayloadIndex) : buildMedicalFrame(vitalPatientId, vitalHeartRate, vitalSpO2,
                                           vitalTempC, vitalIMU, vitalBattery,
                                           vitalAlert, currentSequence);
  generateManchesterSchedule(frame);
  levelIndex = 0;
  if (resetRepetition) repetitionCount = 0;
  levelStartedUs = micros();
  writeLed(levelSchedule[0]);
  // The battery benchmark has synthetic payloads and no serial logger attached.
  // Do not emit them under the normal medical telemetry labels.
  if (!VLC_BATTERY_BENCHMARK) {
    printBenchmarkGroundTruth(frame, levelStartedUs);
    if (testPayloadActive) {
      Serial.print(F("[TX_TEST] SYNTHETIC index=")); Serial.println(testPayloadIndex);
      testPayloadIndex = (testPayloadIndex + 1) % 100;
    } else printTxTelemetryJson(frame);
  }
}

static uint32_t lastSensorPollMs = 0;
static uint32_t lastTempPollMs = 0;
static void serviceSensorsAndPriorityAlert() {
  const uint32_t nowMs = millis();
  if (nowMs - lastSensorPollMs >= 20) {
    lastSensorPollMs = nowMs;
    pollMAX30102();
    servicePpgLedAgc();
    updatePpgEstimates();
  }

  // Continuously sample dedicated medical temperature probe every 500 ms
  if (nowMs - lastTempPollMs >= 500) {
    lastTempPollMs = nowMs;
    vitalTempC = readBodyTemperature();
    printPpgDiagnostics();
  }

  if (updateAlertLatch() && !benchmarkChannelMode && !ledContrastActive) {
    buildAndStartCurrentFrame(true);
#if SERIAL_VERBOSE_TX && !VLC_BATTERY_BENCHMARK
    Serial.println(F("[TX PRIORITY] Abnormal condition: telemetry preempted by alert frame."));
#endif
  }
}

// Keep optical timing independent from sensor processing. This function is
// intentionally called both before and after I2C work so a slow sensor read
// cannot leave the external LED stuck at one level.
static void serviceManchesterTx() {
  const uint32_t nowUs = micros();
  if (ledContrastActive) {
    const uint32_t elapsed = nowUs - ledContrastStartUs;
    if (elapsed >= 20000000UL) {
      ledContrastActive = false;
      Serial.println(F("[LED_CONTRAST] END; resume frames"));
      buildAndStartCurrentFrame(true);
      return;
    }
    const uint32_t step = elapsed / ledContrastHalfUs;
    if (step != ledContrastStep) {
      ledContrastStep = step;
      writeLed((step & 1U) == 0);
      Serial.print(F("[LED_LEVEL] tx_us=")); Serial.print(micros());
      Serial.print(F(" on=")); Serial.println((step & 1U) == 0 ? 1 : 0);
    }
    return;
  }
  const uint32_t elapsedUs = nowUs - levelStartedUs;
  if (elapsedUs < HALF_BIT_US) return;

  uint32_t steps = elapsedUs / HALF_BIT_US;
  // Advance at most one complete frame. If execution was stalled longer than
  // that, restart cleanly instead of indexing beyond levelSchedule[].
  if (steps > FRAME_HALF_BITS) steps = FRAME_HALF_BITS;
  levelStartedUs += steps * HALF_BIT_US;
  levelIndex += steps;

  if (levelIndex >= FRAME_HALF_BITS) {
    if (VLC_BATTERY_BENCHMARK) {
      ++batteryFrameIndex;
      if (batteryFrameIndex >= VLC_BENCHMARK_FRAMES) {
        batteryStage = 4;
        batteryStageUs = micros();
        writeLed(true);  // End marker: 3 s ON, then stays OFF.
        Serial.println(F("[BATTERY_BENCHMARK] END_MARKER"));
      } else {
        currentSequence = batteryFrameIndex & 15;
        buildAndStartCurrentFrame(true);
      }
      return;
    }
    levelIndex = 0;
    ++repetitionCount;

    if (repetitionCount >= REPETITIONS_PER_CYCLE) {
      repetitionCount = 0;
      currentSequence = (currentSequence + 1) & 0x0FU;

      // The current frame stays immutable while it is on air. Its successor
      // receives an atomic snapshot of the newest sensor estimates here.
      sampleSensors();
      updateAlertLatch();
      buildAndStartCurrentFrame(true);

#if SERIAL_VERBOSE_TX && !VLC_BATTERY_BENCHMARK
      if (!testPayloadActive) {
      Serial.print(F("\n[TX NEW PACKET] Seq=")); Serial.print(currentSequence);
      Serial.print(F(" | HR=")); Serial.print(vitalHeartRate);
      Serial.print(F(" bpm | SpO2=")); Serial.print(vitalSpO2);
      Serial.print(F("% | Temp="));
      if (temperatureValid) Serial.print(vitalTempC, 1); else Serial.print(F("INVALID"));
      Serial.print(F("°C | Alert=")); Serial.println(vitalAlert);
      }
#endif
      return;
    }
  }

  writeLed(levelSchedule[levelIndex]);
}

// ----------------------------------------------------------------------------
// SETUP
// ----------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  Serial.setTimeout(25); // A partial command must not stall 100 ms optical timing.
  delay(1000); // Give USB CDC time to enumerate so serial output isn't missed

  pinMode(LED_PIN, OUTPUT);
#ifdef LED_BUILTIN
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, HIGH); // Off initially (Active LOW)
#endif
  writeLed(false);

  // Enable internal pullups to prevent bus lock if no sensor is attached
  pinMode(D4, INPUT_PULLUP);
  pinMode(D5, INPUT_PULLUP);
  Wire.begin();
  Wire.setClock(100000); // 100 kHz standard I2C (maximally stable on nRF52)

#if SERIAL_VERBOSE_TX
  Serial.println(F("\n========================================================"));
  Serial.println(F("🏥 VLC MEDICAL TELEMETRY SENSOR TRANSMITTER (5 BPS OOK)"));
  Serial.println(F("========================================================"));
#endif

  // 1. Auto-detect MAX30102 PPG Sensor
  hasMAX30102 = initMAX30102();

  // 2. Auto-detect Dedicated Temperature Sensor
  // Note: MLX90614 is an SMBus device that requires a register opcode command (0x07 for Object Temp)
  // and NACKs 0-byte I2C quick pings. We probe by reading register 0x07 directly.
  float testMlx = readMLX90614(0x07);
  hasMLX90614 = isfinite(testMlx) && (testMlx >= -20.0f && testMlx <= 100.0f);
  if (hasMLX90614) {
    lastMlxObjectC  = testMlx;
    lastMlxAmbientC = readMLX90614(0x06);
  }
  
  hasMAX30205 = false;
  if (!hasMLX90614) {
    if (i2cProbe(0x48)) {
      hasMAX30205 = true;
      max30205Addr = 0x48;
    } else if (i2cProbe(0x49)) {
      hasMAX30205 = true;
      max30205Addr = 0x49;
    }
  }

#if ENABLE_ANALOG_TEMP_FALLBACK
  float aVal = readAnalogTemp();
  hasAnalogTemp = (!hasMLX90614 && !hasMAX30205 && aVal >= 15.0f && aVal <= 50.0f);
#else
  hasAnalogTemp = false;
  float aVal = 0.0f;
#endif

#if SERIAL_VERBOSE_TX
  Serial.print(F("• PPG Sensor (MAX30102):     "));
  Serial.println(hasMAX30102 ? F("ONLINE (I2C 0x57) [PASS]") : F("NOT DETECTED"));
  Serial.print(F("• Temperature Sensor:        "));
  if (hasMLX90614) {
    Serial.print(F("ONLINE (MLX90614 Infrared 0x5A) [PASS, Ambient="));
    Serial.print(lastMlxAmbientC, 1);
    Serial.print(F("°C, Object="));
    Serial.print(lastMlxObjectC, 1);
    Serial.println(F("°C]"));
  } else if (hasMAX30205) {
    float curT = readMAX30205(max30205Addr);
    Serial.print(F("ONLINE (MAX30205 Body Contact 0x"));
    Serial.print(max30205Addr, HEX);
    Serial.print(F(") [PASS, Cur=")); Serial.print(curT, 1); Serial.println(F("°C]"));
  } else if (hasMAX30102) {
    Serial.print(F("ONLINE (MAX30102 On-Chip Sensor) [PASS, Die="));
    Serial.print(lastMax30102DieTemp, 1);
    Serial.println(F("°C]"));
  } else if (hasAnalogTemp) {
    Serial.print(F("ONLINE (LM35 / Analog A0) [PASS, Cur="));
    Serial.print(aVal, 1); Serial.println(F("°C]"));
  } else {
    Serial.println(F("NOT DETECTED (Dedicated temp sensor offline)"));
  }
  Serial.println(F("========================================================\n"));
#endif

  // Power-up visual indicator: 3 short blinks
  for (uint8_t b = 0; b < 3; ++b) {
    writeLed(true); delay(150); writeLed(false); delay(150);
  }
  delay(1000);

  // Initial sensor sample & frame build
  sampleSensors();
  updateAlertLatch();
  if (VLC_BATTERY_BENCHMARK) {
    static_assert(VLC_BENCHMARK_FRAMES > 0 && VLC_BENCHMARK_FRAMES <= 100,
                  "Battery run must contain 1..100 frames");
    benchmarkChannelMode = true;
    batteryStage = 1;
    batteryStageUs = micros();
    writeLed(false);
    Serial.print(F("[BATTERY_BENCHMARK] SYNTHETIC seed=")); Serial.print(VLC_BENCHMARK_SEED);
    Serial.print(F(" frames=")); Serial.println(VLC_BENCHMARK_FRAMES);
    return;
  }
  buildAndStartCurrentFrame(true);
#if SERIAL_VERBOSE_TX
  Serial.println(F("🚀 Optical VLC transmission STARTED at 5 bps."));
  Serial.print(F("Initial Frame: Patient=")); Serial.print(vitalPatientId);
  Serial.print(F(" HR=")); Serial.print(vitalHeartRate);
  Serial.print(F(" SpO2=")); Serial.print(vitalSpO2);
  Serial.print(F(" Temp="));
  if (temperatureValid) Serial.print(vitalTempC, 1); else Serial.print(F("INVALID"));
  Serial.print(F(" IMU=")); Serial.print(vitalIMU);
  Serial.print(F(" Battery=")); Serial.print(vitalBattery);
  Serial.print(F(" Seq=")); Serial.println(currentSequence);
#endif
}

// ----------------------------------------------------------------------------
// MAIN LOOP: NON-BLOCKING MICROSECOND-ACCURATE MANCHESTER OOK SCHEDULER
// ----------------------------------------------------------------------------
void loop() {
  if (VLC_BATTERY_BENCHMARK) {
    if (batteryStage == 5) {
      writeLed(false);
      return;
    }
    if (batteryStage == 3) serviceManchesterTx();
    serviceSensorsAndPriorityAlert();
    const uint32_t elapsed = micros() - batteryStageUs;
    // 10 s OFF allows RX to be ready; start marker is 3 s ON + 2 s OFF.
    if (batteryStage == 1 && elapsed >= 10000000UL) {
      batteryStage = 2; batteryStageUs = micros(); writeLed(true);
    } else if (batteryStage == 2 && elapsed >= 3000000UL) {
      batteryStage = 6; batteryStageUs = micros(); writeLed(false);
    } else if (batteryStage == 6 && elapsed >= 2000000UL) {
      batteryStage = 3; batteryFrameIndex = 0; currentSequence = 0;
      buildAndStartCurrentFrame(true);
    } else if (batteryStage == 4 && elapsed >= 3000000UL) {
      batteryStage = 5; writeLed(false);
      Serial.println(F("[BATTERY_BENCHMARK] COMPLETE - reset to run again"));
    }
    return;
  }
  serviceManchesterTx();
  serviceSensorsAndPriorityAlert();
  serviceManchesterTx();

  // Handle Serial Commands (e.g. "ALERT", "NORMAL", "HR 90")
  static char commandBuffer[96];
  static uint8_t commandLength = 0;
  while (Serial.available()) {
    const char incoming = (char)Serial.read();
    if (incoming == '\r') continue;
    if (incoming != '\n') {
      if (commandLength < sizeof(commandBuffer) - 1) commandBuffer[commandLength++] = incoming;
      continue;
    }
    commandBuffer[commandLength] = '\0';
    String cmd(commandBuffer);
    commandLength = 0;
    cmd.trim();
    if (cmd.equalsIgnoreCase("LED CONTRAST 1000") || cmd.equalsIgnoreCase("LED CONTRAST 100")) {
      ledContrastHalfUs = cmd.endsWith("1000") ? 1000000UL : 100000UL;
      ledContrastStep = 0;
      ledContrastStartUs = micros();
      ledContrastActive = true;
      writeLed(true);
      Serial.print(F("[LED_CONTRAST] BEGIN half_us=")); Serial.print(ledContrastHalfUs);
      Serial.print(F(" tx_us=")); Serial.println(ledContrastStartUs);
      Serial.print(F("[LED_LEVEL] tx_us=")); Serial.print(ledContrastStartUs);
      Serial.println(F(" on=1"));
    } else if (cmd.equalsIgnoreCase("ALERT")) {
      manualAlertLatched = true;
      alertLatched = true;
      vitalAlert = 1;
      normalSinceMs = 0;
      buildAndStartCurrentFrame(true);
      Serial.println(F("🚨 MANUAL EMERGENCY ALERT ASSERTED!"));
    } else if (cmd.equalsIgnoreCase("NORMAL")) {
      manualAlertLatched = false;
      alertLatched = false;
      vitalAlert = 0;
      normalSinceMs = 0;
      buildAndStartCurrentFrame(true);
      Serial.println(F("✅ Medical alert cleared."));
    } else if (cmd.equalsIgnoreCase("PAYLOAD TEST ON")) {
      benchmarkChannelMode = true;
      testPayloadRequested = true;
      Serial.println(F("[PAYLOAD_TEST] ON pending_frame_boundary; SYNTHETIC channel-test data"));
    } else if (cmd.equalsIgnoreCase("PAYLOAD TEST OFF")) {
      testPayloadRequested = false;
      Serial.println(F("[PAYLOAD_TEST] OFF pending_frame_boundary"));
    } else if (cmd.equalsIgnoreCase("BENCHMARK ON")) {
      benchmarkChannelMode = true;
      Serial.println(F("[BENCHMARK_MODE] ON - priority preemption disabled until frame boundary"));
    } else if (cmd.equalsIgnoreCase("BENCHMARK OFF")) {
      benchmarkChannelMode = false;
      Serial.println(F("[BENCHMARK_MODE] OFF - priority preemption restored"));
    } else if (cmd.equalsIgnoreCase("SCAN") || cmd.equalsIgnoreCase("STATUS")) {
      Serial.println(F("\n--- I2C BUS SCAN ---"));
      for (uint8_t a = 8; a <= 119; ++a) {
        Wire.beginTransmission(a);
        if (Wire.endTransmission(true) == 0) {
          Serial.print(F("Found I2C device at 0x"));
          if (a < 16) Serial.print('0');
          Serial.print(a, HEX);
          if (a == 0x57) Serial.print(F(" (MAX30102)"));
          if (a == 0x5A) Serial.print(F(" (MLX90614)"));
          if (a == 0x6A) Serial.print(F(" (LSM6DS3 IMU)"));
          Serial.println();
        }
      }
      Wire.beginTransmission(0x5A);
      Wire.write(0x07);
      uint8_t endErr = Wire.endTransmission(false);
      uint8_t reqAvail = Wire.requestFrom((uint8_t)0x5A, (uint8_t)3);
      Serial.print(F("MLX debug: endTrans(false)=")); Serial.print(endErr);
      Serial.print(F(", reqFrom=")); Serial.println(reqAvail);
      float mlxO = readMLX90614(0x07);
      float mlxA = readMLX90614(0x06);
      Serial.print(F("MLX direct test: Amb=")); Serial.print(mlxA, 2);
      Serial.print(F("°C, Obj=")); Serial.print(mlxO, 2);
      Serial.print(F("°C | hasMLX=")); Serial.println(hasMLX90614 ? F("true") : F("false"));
      i2cWriteRegister(0x57, 0x21, 0x01);
      delay(35);
      int8_t tint = (int8_t)i2cReadRegister(0x57, 0x1F);
      uint8_t frac = i2cReadRegister(0x57, 0x20);
      float dieT = (float)tint + ((float)frac * 0.0625f);
      Serial.print(F("MAX30102 Die Temp: ")); Serial.print(dieT, 2); Serial.println(F("°C"));
      Serial.println(F("--- END SCAN ---\n"));
    }
  }
}
