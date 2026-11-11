/*
  =============================================================================
  Real-Time Sensor VLC Optical Transmitter (TX) - 5 bps Manchester OOK
  =============================================================================
  Hardware: Seeed Studio XIAO nRF52840 Sense (Seeeduino nrf52 1.1.13)
  Optical Emitter: High-power LED / VLC driver on LED_PIN (D9)
  Sensors Supported:
    1. MAX30102 (Wire I2C: 0x57) - Optical PPG: Heart Rate & SpO2
    2. Body Temperature - MLX90614 / MAX30205, MAX30102 die fallback; A0 optional
    3. LSM6DS3TR-C (Wire1 I2C: 0x6A) - accelerometer activity: still/moving
    4. Battery - fixed placeholder (7 bars; no voltage measurement)
    5. Medical Emergency Alert - sustained vital thresholds or manual serial command

  Activity: population SD of |a| per 1-s window; moving if SD > 0.015 g (tuned on forehead wear).
  Snapshot is moving if >=4 of the last 10 windows moved. No fall/seizure detection.

  Protocol Specification:
  -----------------------
  Frame: 48 bits, MSB first
    - Header     [4 bits]: 0xA (1010)
    - Patient ID [4 bits]: 1 to 8 (Default: 1)
    - Heart Rate [8 bits]: 30 to 220 bpm
    - SpO2       [7 bits]: 70 to 100 %
    - Body Temp  [7 bits]: (Temp_C - 34.0) * 10 (range 0..80, 34.0C to 42.0C)
    - IMU Status [2 bits]: 0=Still/invalid, 1=Moving; 2 and 3 never emitted
    - Battery    [3 bits]: 0 to 7 bars
    - Alert Flag [1 bit ]: 0=Normal, 1=Emergency Alarm
    - Sequence   [4 bits]: 0 to 15 (increments every measurement cycle)
    - CRC-8      [8 bits]: Polynomial 0x07, init 0x00 over 40 payload bits

  Modulation: Manchester OOK at 5 bps
    - Bit 1 -> LED ON (100 ms), then LED OFF (100 ms)
    - Bit 0 -> LED OFF (100 ms), then LED ON (100 ms)
    - Delimiter: 400 ms LOW (4 half-bits)
    - RT copy: TIMER4 drives the LED independently of loop sensor processing.
      loop snapshots sensors in the last second into a second complete frame.
      TIMER4 swaps buffers at the 100-half-bit boundary, or repeats if not ready.
  =============================================================================
*/

#include <Arduino.h>
#include <Wire.h>
#include "ImuActivity.h"
#include "../tools/imu_probe/BoundedImuBus.h"

#if !defined(NRF52840_XXAA)
#error "This RT copy reserves TIMER4 on the nRF52840 Seeeduino core."
#endif

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
// loop owns the free buffer until publication; TIMER4 owns the on-air buffer.
struct FrameSnapshot {
  uint64_t frame;
  float tempC;
  uint16_t payloadIndex;
  uint8_t patient, hr, spo2, imu, battery, alert, seq;
  bool ppgValid, hrValid, spo2Valid, tempValid, test, imuValid;
};
struct AirFrame {
  FrameSnapshot snapshot;
  bool levels[FRAME_HALF_BITS];
};
struct TxEvent {
  FrameSnapshot snapshot;
  uint32_t us, ms, frameId, contrastHalfUs;
  uint8_t kind; // 0=frame, 1=level, 2=contrast end, 3=battery end, 4=complete, 5=contrast begin
  bool repeated, on; // on also marks a regular frame boundary for NEW PACKET logs
};
static AirFrame airFrames[2];
static volatile uint8_t activeBuffer = 0;
static volatile bool nextReady = false, forceFrame = false, contrastRequested = false;
static volatile bool schedulerStarted = false;
static volatile uint16_t levelIndex = 0;
static uint8_t currentSequence = 0; // loop-only construction state
static volatile uint32_t benchmarkFrameId = 0;
static bool benchmarkChannelMode = false;
static bool testPayloadRequested = false, testPayloadActive = false;
static uint16_t testPayloadIndex = 0;
static volatile bool ledContrastActive = false;
static volatile uint32_t ledContrastHalfUs = 1000000UL;
static volatile uint16_t contrastTicks = 0; // ISR-only
static volatile uint8_t batteryStage = 0;
static uint16_t stageTicks = 0; // ISR-only
static uint16_t batteryFrameIndex = 0; // loop-only construction state
static bool timingEnabled = false;
static volatile uint32_t maxEdgeLatenessUs = 0;
static uint32_t maxLoopBlockingUs = 0, previousLoopUs = 0;
static uint64_t timerEpochUs = 0; // ISR-only extended clock after startup
static uint32_t previousTimerUs = 0;
static const uint8_t EVENT_CAPACITY = 16;
static TxEvent events[EVENT_CAPACITY];
static volatile uint8_t eventHead = 0, eventTail = 0;
static volatile uint32_t droppedEvents = 0;

static uint32_t lockTx() {
  const uint32_t key = __get_PRIMASK();
  __disable_irq();
  __DMB();
  return key;
}
static void unlockTx(uint32_t key) {
  __DMB();
  __set_PRIMASK(key);
}
static uint32_t txClockUs() {
  const uint32_t key = lockTx();
  NRF_TIMER4->TASKS_CAPTURE[2] = 1;
  const uint32_t now = NRF_TIMER4->CC[2];
  unlockTx(key);
  return now;
}

// Deterministic synthetic payload; sensors are still serviced independently.
// HR encodes the frame index for runs <=100; the transmitted values are NOT measurements.
static uint64_t batteryBenchmarkFrame(uint16_t index) {
  uint32_t r = VLC_BENCHMARK_SEED ^ ((uint32_t)(index + 1) * 0x9E3779B9UL);
  r ^= r << 13; r ^= r >> 17; r ^= r << 5;
  uint64_t p = (0xA1ULL << 32) | ((uint64_t)(30 + index) << 24);
  p |= (uint64_t)(70 + (r % 31)) << 17;
  p |= (uint64_t)((r >> 8) % 81) << 10;
  p |= (uint64_t)((r >> 16) & 1) << 8;
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
static uint8_t  vitalIMU         = 0;     // 0=still/invalid, 1=moving; no fall/seizure states
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

// On-board IMU is independent of external Wire sensors and the TIMER4 ISR.
static bool imuValid = false, imuDiagnostics = false;
static ImuActivity imuActivity;
static uint32_t lastImuPollMs = 0, lastImuDiagnosticMs = 0;
static float imuAx = NAN, imuAy = NAN, imuAz = NAN;

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
  imu = (imu == 1) ? 1 : 0; // Only validated activity states are allowed.

  uint64_t payload = 0;
  payload |= ((uint64_t)0xAU) << 36;                    // Preamble (0xA)
  payload |= ((uint64_t)(patientId & 0x0FU)) << 32;     // Patient ID (4 bits)
  payload |= ((uint64_t)hr) << 24;                      // Heart Rate (8 bits)
  payload |= ((uint64_t)(spo2 & 0x7FU)) << 17;          // SpO2 (7 bits)
  payload |= ((uint64_t)(tempCode & 0x7FU)) << 10;      // Temp Offset (7 bits)
  payload |= ((uint64_t)(imu & 0x03U)) << 8;            // IMU Activity (2 bits)
  payload |= ((uint64_t)(battery & 0x07U)) << 5;        // Battery (3 bits)
  payload |= ((uint64_t)(alert & 0x01U)) << 4;          // Alert Flag (1 bit)
  payload |= (uint64_t)(seq & 0x0FU);                   // Sequence Number (4 bits)

  uint8_t crc = calculateCRC8(payload, 40);
  return (payload << 8) | crc;
}

// ----------------------------------------------------------------------------
// MANCHESTER SCHEDULE GENERATOR
// ----------------------------------------------------------------------------
static void generateManchesterSchedule(uint64_t frame48, bool *levelSchedule) {
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

// LSM6DS3TR-C direct registers (ST datasheet): 104 Hz, +/-2 g, 0.061 mg/LSB.
// Core variant.h: power=15, Wire1 SDA=17/SCL=16. No IMU interrupt needed.
static bool imuWrite(uint8_t reg, uint8_t value) {
  return ImuBus::write(reg, value);
}
static bool imuRead(uint8_t reg, uint8_t *data, uint8_t count) {
  return ImuBus::read(reg, data, count);
}
static bool imuInitFail(const char *step) {
  ImuBus::disable();
  imuValid = false;
  vitalIMU = 0;
  Serial.print(F("[IMU] init FAIL step=")); Serial.print(step);
  Serial.print(F(" reason=")); Serial.print(ImuBus::error);
  Serial.print(F(" errorsrc=0x")); Serial.println(ImuBus::errorSource, HEX);
  return false;
}
static bool initIMU() {
  // Called once from loop, with Serial and the independent TIMER4 already live.
  ImuBus::powerOn(); // H0H1 high drive before HIGH; derived from the board pin map.
  delay(100); // Power-on settling; interrupts and optical scheduling remain live.
  ImuBus::gpioInputs();
  if (!ImuBus::idle()) {
    ImuBus::error = "bus-low";
    return imuInitFail("gpio-idle");
  }
  Wire1.begin();
  Wire1.setClock(100000);
  uint8_t id = 0;
  if (!imuRead(0x0F, &id, 1)) return imuInitFail("whoami-read");
  if (id != 0x6A && id != 0x69) return imuInitFail("whoami-value");
  // BDU + auto-increment; gyro off; accelerometer 104 Hz, +/-2 g.
  if (!imuWrite(0x12, 0x44)) return imuInitFail("ctrl3-c-write");
  if (!imuWrite(0x11, 0x00)) return imuInitFail("ctrl2-g-write");
  if (!imuWrite(0x10, 0x40)) return imuInitFail("ctrl1-xl-write");
  uint8_t config[3];
  if (!imuRead(0x10, config, 3)) return imuInitFail("config-read");
  if (config[0] != 0x40 || config[1] != 0x00 || config[2] != 0x44)
    return imuInitFail("config-value");
  Serial.print(F("[IMU] init ok whoami=0x")); Serial.println(id, HEX);
  return true;
}
static void serviceIMU(uint32_t nowMs) {
  imuActivity.advance(nowMs); // Close elapsed wall-clock windows before adding a sample.
  if (imuValid && nowMs - lastImuPollMs >= 20UL) {
    lastImuPollMs = nowMs; // No catch-up bursts or sample-ready busy wait.
    uint8_t raw[6];
    // Each TWIM1 phase is bounded to 5 ms + 1 ms STOP; IRQs stay enabled.
    if (!imuRead(0x28, raw, 6)) {
      imuValid = false; // Fail closed until reset; never retain stale activity.
      imuActivity.reset(nowMs);
      vitalIMU = 0;
      imuAx = imuAy = imuAz = NAN;
    } else {
      imuAx = (int16_t)((uint16_t)raw[0] | ((uint16_t)raw[1] << 8)) * 0.000061f;
      imuAy = (int16_t)((uint16_t)raw[2] | ((uint16_t)raw[3] << 8)) * 0.000061f;
      imuAz = (int16_t)((uint16_t)raw[4] | ((uint16_t)raw[5] << 8)) * 0.000061f;
      imuActivity.add(imuAx, imuAy, imuAz);
    }
  }
  if (imuDiagnostics && nowMs - lastImuDiagnosticMs >= 1000UL) {
    lastImuDiagnosticMs = nowMs;
    Serial.print(F("[IMU] sd_g=")); Serial.print(imuActivity.sdG, 5);
    Serial.print(F(" moving=")); Serial.print(imuActivity.windowMoving ? 1 : 0);
    Serial.print(F(" windows_moving=")); Serial.print(imuActivity.windowsMoving);
    Serial.print(F("/10 ax=")); Serial.print(imuAx, 4);
    Serial.print(F(" ay=")); Serial.print(imuAy, 4);
    Serial.print(F(" az=")); Serial.print(imuAz, 4);
    Serial.print(F(" imu_valid=")); Serial.println(imuValid ? 1 : 0);
  }
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
  vitalIMU = imuValid ? imuActivity.activity() : 0;

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

static void printTxTelemetryJson(const FrameSnapshot &snapshot) {
  Serial.print(F("[TX_TELEMETRY] {\"patient\":"));
  Serial.print(snapshot.patient);
  Serial.print(F(",\"hr\":"));
  Serial.print(snapshot.hr);
  Serial.print(F(",\"spo2\":"));
  Serial.print(snapshot.spo2);
  Serial.print(F(",\"temp_c\":"));
  if (snapshot.tempValid) Serial.print(snapshot.tempC, 1); else Serial.print(F("0.0"));
  Serial.print(F(",\"ppg_valid\":"));
  Serial.print(snapshot.ppgValid ? F("true") : F("false"));
  Serial.print(F(",\"hr_valid\":"));
  Serial.print(snapshot.hrValid ? F("true") : F("false"));
  Serial.print(F(",\"spo2_valid\":"));
  Serial.print(snapshot.spo2Valid ? F("true") : F("false"));
  Serial.print(F(",\"temp_valid\":"));
  Serial.print(snapshot.tempValid ? F("true") : F("false"));
  Serial.print(F(",\"imu\":"));
  Serial.print(snapshot.imu);
  Serial.print(F(",\"battery\":"));
  Serial.print(snapshot.battery);
  Serial.print(F(",\"alert\":"));
  Serial.print(snapshot.alert);
  Serial.print(F(",\"seq\":"));
  Serial.print(snapshot.seq);
  Serial.print(F(",\"frame_hex\":\"0x"));
  for (int b = 44; b >= 0; b -= 4) {
    uint8_t nibble = (snapshot.frame >> b) & 0x0F;
    Serial.print(nibble, HEX);
  }
  Serial.print(F("\",\"imu_valid\":")); Serial.print(snapshot.imuValid ? 1 : 0);
  Serial.println(F("}"));
}

static void printBenchmarkGroundTruth(const TxEvent &event) {
  const FrameSnapshot &snapshot = event.snapshot;
#if BENCHMARK_GROUND_TRUTH
  Serial.print(F("[TX_GT] frame_id=")); Serial.print(event.frameId);
  Serial.print(F(" tx_ms=")); Serial.print(event.ms);
  Serial.print(F(" seq=")); Serial.print(snapshot.seq);
  Serial.print(F(" ppg_valid=")); Serial.print(snapshot.ppgValid ? 1 : 0);
  Serial.print(F(" temp_valid=")); Serial.print(snapshot.tempValid ? 1 : 0);
  Serial.print(F(" frame_hex=0x"));
  for (int b = 44; b >= 0; b -= 4) {
    Serial.print((uint8_t)((snapshot.frame >> b) & 0x0F), HEX);
  }
  Serial.print(F(" tx_us=")); Serial.print(event.us);
  Serial.print(F(" payload_mode=")); Serial.print(snapshot.test ? F("test") : F("sensor"));
  if (snapshot.test) {
    Serial.print(F(" payload_index=")); Serial.print(snapshot.payloadIndex);
    Serial.print(F(" seed=")); Serial.print(VLC_BENCHMARK_SEED);
  }
  Serial.print(F(" imu_valid=")); Serial.print(snapshot.imuValid ? 1 : 0);
  Serial.println();
#else
  (void)event;
#endif
}

// Only publish after the free buffer and all metadata are complete. While
// nextReady is false the ISR can repeat, but cannot switch away from activeBuffer.
static void prepareNextFrame(bool immediate) {
  uint32_t key = lockTx();
  if (immediate) nextReady = false;
  if (nextReady) { unlockTx(key); return; }
  const uint8_t target = activeBuffer ^ 1U;
  const FrameSnapshot previous = airFrames[activeBuffer].snapshot;
  unlockTx(key);

  testPayloadActive = !VLC_BATTERY_BENCHMARK && testPayloadRequested;
  testPayloadIndex = testPayloadActive && previous.test ?
      (previous.payloadIndex + 1) % 100 : 0;
  batteryFrameIndex = schedulerStarted && !immediate ? previous.payloadIndex + 1 : 0;
  currentSequence = testPayloadActive ? (testPayloadIndex & 15) :
      VLC_BATTERY_BENCHMARK ? (batteryFrameIndex & 15) :
      (!schedulerStarted ? currentSequence : ((previous.seq + (immediate ? 0 : 1)) & 15));
  AirFrame &next = airFrames[target];
  FrameSnapshot &v = next.snapshot;
  v.patient = vitalPatientId; v.hr = vitalHeartRate; v.spo2 = vitalSpO2;
  vitalIMU = imuValid ? imuActivity.activity() : 0; // Also covers immediate frames.
  v.imuValid = imuValid;
  v.tempC = vitalTempC; v.imu = vitalIMU; v.battery = vitalBattery;
  v.alert = vitalAlert; v.seq = currentSequence;
  v.ppgValid = ppgValid; v.hrValid = heartRateValid; v.spo2Valid = spo2Valid;
  v.tempValid = temperatureValid; v.test = testPayloadActive;
  v.payloadIndex = VLC_BATTERY_BENCHMARK ? batteryFrameIndex : testPayloadIndex;
  v.frame = (VLC_BATTERY_BENCHMARK || v.test) ? batteryBenchmarkFrame(v.payloadIndex) :
      buildMedicalFrame(v.patient, v.hr, v.spo2, v.tempC, v.imu, v.battery, v.alert, v.seq);
  generateManchesterSchedule(v.frame, next.levels);
  key = lockTx();
  nextReady = true;
  if (immediate) {
    forceFrame = true;
    if (schedulerStarted) NVIC_SetPendingIRQ(TIMER4_IRQn);
  }
  unlockTx(key);
}
static void buildAndStartCurrentFrame(bool resetRepetition) {
  (void)resetRepetition;
  prepareNextFrame(true);
}

// Single ISR producer, loop consumer, fixed storage, no Serial/I2C/RTOS/heap.
static void recordTxEvent(uint8_t kind, uint32_t rawUs, bool repeated, bool on) {
  timerEpochUs += (uint32_t)(rawUs - previousTimerUs);
  previousTimerUs = rawUs;
  const uint8_t head = eventHead;
  const uint8_t next = (head + 1) % EVENT_CAPACITY;
  const uint32_t id = benchmarkFrameId;
  if (kind == 0) ++benchmarkFrameId;
  if (next == eventTail) { ++droppedEvents; return; }
  TxEvent &e = events[head];
  e.snapshot = airFrames[activeBuffer].snapshot;
  e.contrastHalfUs = ledContrastHalfUs;
  e.us = (uint32_t)timerEpochUs;
  e.ms = (uint32_t)(timerEpochUs / 1000ULL);
  e.frameId = id; e.kind = kind; e.repeated = repeated; e.on = on;
  __DMB();
  eventHead = next;
}
static uint32_t captureEdgeUs() {
  NRF_TIMER4->TASKS_CAPTURE[1] = 1;
  return NRF_TIMER4->CC[1];
}
static uint32_t startAirFrame(bool repeated, bool regularBoundary) {
  levelIndex = 0;
  writeLed(airFrames[activeBuffer].levels[0]);
  const uint32_t edge = captureEdgeUs();
  recordTxEvent(0, edge, repeated, regularBoundary);
  return edge;
}
extern "C" void TIMER4_IRQHandler(void) {
  const bool tick = NRF_TIMER4->EVENTS_COMPARE[0] != 0;
  const uint32_t due = NRF_TIMER4->CC[0];
  if (tick) {
    NRF_TIMER4->EVENTS_COMPARE[0] = 0;
    NRF_TIMER4->CC[0] = due + HALF_BIT_US; // anchored, independent of ISR arrival
  }
  if (contrastRequested) {
    contrastRequested = false;
    ledContrastActive = true;
    contrastTicks = 0;
    writeLed(true);
    const uint32_t edge = captureEdgeUs();
    NRF_TIMER4->CC[0] = edge + HALF_BIT_US;
    recordTxEvent(5, edge, false, true);
    return;
  }
  if (forceFrame && !ledContrastActive && (!VLC_BATTERY_BENCHMARK || batteryStage == 3)) {
    forceFrame = false;
    if (nextReady) { activeBuffer ^= 1U; nextReady = false; }
    const uint32_t edge = startAirFrame(false, false);
    NRF_TIMER4->CC[0] = edge + HALF_BIT_US;
    return;
  }
  if (!tick) return;
  if (ledContrastActive) {
    ++contrastTicks;
    if (contrastTicks >= 200) {
      ledContrastActive = false;
      forceFrame = false;
      recordTxEvent(2, captureEdgeUs(), false, false);
      const bool repeated = !nextReady;
      if (nextReady) { activeBuffer ^= 1U; nextReady = false; }
      startAirFrame(repeated, false);
    } else if ((contrastTicks % (ledContrastHalfUs / HALF_BIT_US)) == 0) {
      const bool on = ((contrastTicks / (ledContrastHalfUs / HALF_BIT_US)) & 1U) == 0;
      writeLed(on);
      recordTxEvent(1, captureEdgeUs(), false, on);
    }
  } else if (VLC_BATTERY_BENCHMARK && batteryStage != 3) {
    ++stageTicks;
    if (batteryStage == 1 && stageTicks >= 100) {
      batteryStage = 2; stageTicks = 0; writeLed(true);
    } else if (batteryStage == 2 && stageTicks >= 30) {
      batteryStage = 6; stageTicks = 0; writeLed(false);
    } else if (batteryStage == 6 && stageTicks >= 20) {
      batteryStage = 3; forceFrame = false;
      if (nextReady) { activeBuffer ^= 1U; nextReady = false; }
      startAirFrame(false, false);
    } else if (batteryStage == 4 && stageTicks >= 30) {
      batteryStage = 5; stageTicks = 0; writeLed(false);
      recordTxEvent(4, captureEdgeUs(), false, false);
    }
  } else {
    ++levelIndex;
    if (levelIndex == FRAME_HALF_BITS) {
      if (VLC_BATTERY_BENCHMARK &&
          airFrames[activeBuffer].snapshot.payloadIndex + 1 >= VLC_BENCHMARK_FRAMES) {
        batteryStage = 4; stageTicks = 0; writeLed(true);
        recordTxEvent(3, captureEdgeUs(), false, false);
      } else {
        const bool repeated = !nextReady;
        if (nextReady) { activeBuffer ^= 1U; nextReady = false; }
        startAirFrame(repeated, true);
      }
    } else {
      writeLed(airFrames[activeBuffer].levels[levelIndex]);
    }
  }
  // Includes ISR work through the GPIO write; measures every slot even if the
  // level is unchanged. Unsigned subtraction handles TIMER4's 32-bit wrap.
  const uint32_t late = captureEdgeUs() - due;
  if (late > maxEdgeLatenessUs) maxEdgeLatenessUs = late;
}
static void startTxTimer() {
  NVIC_DisableIRQ(TIMER4_IRQn);
  NRF_TIMER4->TASKS_STOP = 1;
  NRF_TIMER4->MODE = TIMER_MODE_MODE_Timer;
  NRF_TIMER4->BITMODE = TIMER_BITMODE_BITMODE_32Bit;
  NRF_TIMER4->PRESCALER = 4; // 16 MHz / 16 = 1 MHz
  NRF_TIMER4->SHORTS = 0;
  NRF_TIMER4->INTENCLR = 0xFFFFFFFFUL;
  NRF_TIMER4->TASKS_CLEAR = 1;
  NRF_TIMER4->EVENTS_COMPARE[0] = 0;
  NRF_TIMER4->CC[0] = HALF_BIT_US;
  timerEpochUs = (uint64_t)millis() * 1000ULL;
  previousTimerUs = 0;
  NRF_TIMER4->INTENSET = TIMER_INTENSET_COMPARE0_Msk;
  NVIC_ClearPendingIRQ(TIMER4_IRQn);
  NVIC_SetPriority(TIMER4_IRQn, 2); // highest SoftDevice-compatible app priority
  schedulerStarted = true;
  NRF_TIMER4->TASKS_START = 1;
  NVIC_EnableIRQ(TIMER4_IRQn);
  if (forceFrame && !VLC_BATTERY_BENCHMARK) NVIC_SetPendingIRQ(TIMER4_IRQn);
}

static uint32_t lastSensorPollMs = 0;
static uint32_t lastTempPollMs = 0;
static void serviceSensorsAndPriorityAlert() {
  const uint32_t nowMs = millis();
  serviceIMU(nowMs);
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

// Deferred logs use the immutable snapshot, never the live sensor globals.
static void serviceManchesterTx() {
  for (;;) {
    TxEvent e;
    uint32_t key = lockTx();
    if (eventTail == eventHead) { unlockTx(key); break; }
    e = events[eventTail];
    eventTail = (eventTail + 1) % EVENT_CAPACITY;
    unlockTx(key);
    if (e.kind == 0) {
      if (e.repeated) Serial.println(F("[TX_REPEAT] next_buffer_not_ready; retransmitted immutable frame"));
      if (!VLC_BATTERY_BENCHMARK) {
        printBenchmarkGroundTruth(e);
        if (e.snapshot.test) {
          Serial.print(F("[TX_TEST] SYNTHETIC index=")); Serial.println(e.snapshot.payloadIndex);
        } else {
          printTxTelemetryJson(e.snapshot);
#if SERIAL_VERBOSE_TX
          if (e.on) {
          const FrameSnapshot &v = e.snapshot;
          Serial.print(F("\\n[TX NEW PACKET] Seq=")); Serial.print(v.seq);
          Serial.print(F(" | HR=")); Serial.print(v.hr);
          Serial.print(F(" bpm | SpO2=")); Serial.print(v.spo2);
          Serial.print(F("% | Temp="));
          if (v.tempValid) Serial.print(v.tempC, 1); else Serial.print(F("INVALID"));
          Serial.print(F("°C | Alert=")); Serial.println(v.alert);
          }
#endif
        }
      }
      key = lockTx();
      const uint32_t late = maxEdgeLatenessUs;
      maxEdgeLatenessUs = 0;
      unlockTx(key);
      if (timingEnabled) {
        Serial.print(F("[TIMING] max_loop_block_us=")); Serial.print(maxLoopBlockingUs);
        Serial.print(F(" max_edge_late_us=")); Serial.println(late);
      }
      maxLoopBlockingUs = 0;
    } else if (e.kind == 1 || e.kind == 5) {
      if (e.kind == 5) {
        Serial.print(F("[LED_CONTRAST] BEGIN half_us=")); Serial.print(e.contrastHalfUs);
        Serial.print(F(" tx_us=")); Serial.println(e.us);
      }
      Serial.print(F("[LED_LEVEL] tx_us=")); Serial.print(e.us);
      Serial.print(F(" on=")); Serial.println(e.on ? 1 : 0);
    } else if (e.kind == 2) Serial.println(F("[LED_CONTRAST] END; resume frames"));
    else if (e.kind == 3) Serial.println(F("[BATTERY_BENCHMARK] END_MARKER"));
    else if (e.kind == 4) Serial.println(F("[BATTERY_BENCHMARK] COMPLETE - reset to run again"));
  }
  const uint32_t key = lockTx();
  const uint32_t dropped = droppedEvents;
  droppedEvents = 0;
  unlockTx(key);
  if (dropped) {
    Serial.print(F("[TX_LOG_OVERFLOW] dropped_events=")); Serial.println(dropped);
  }
}
static void serviceNextFrame() {
  // Snapshot in the last second, allowing ~1 s for sensor work and construction.
  const uint32_t key = lockTx();
  const bool needed = !nextReady && ((ledContrastActive && contrastTicks >= 190) || (!ledContrastActive && levelIndex >= 90)) &&
      (!VLC_BATTERY_BENCHMARK || batteryStage == 3);
  unlockTx(key);
  if (!needed) return;
  sampleSensors();
  const bool priority = updateAlertLatch() && !benchmarkChannelMode && !ledContrastActive;
  prepareNextFrame(priority);
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

  // The first frame is explicitly IMU-invalid; initialize only after TIMER4 starts.
  imuActivity.reset(millis());

  // Initial sensor sample & frame build
  sampleSensors();
  updateAlertLatch();
  if (VLC_BATTERY_BENCHMARK) {
    static_assert(VLC_BENCHMARK_FRAMES > 0 && VLC_BENCHMARK_FRAMES <= 100,
                  "Battery run must contain 1..100 frames");
    benchmarkChannelMode = true;
    batteryStage = 1;
    writeLed(false);
    Serial.print(F("[BATTERY_BENCHMARK] SYNTHETIC seed=")); Serial.print(VLC_BENCHMARK_SEED);
    Serial.print(F(" frames=")); Serial.println(VLC_BENCHMARK_FRAMES);
    prepareNextFrame(true);
    startTxTimer();
    return;
  }
  buildAndStartCurrentFrame(true);
  startTxTimer();
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
// MAIN LOOP: SENSOR ACQUISITION, FRAME PREPARATION AND DEFERRED LOGGING
// ----------------------------------------------------------------------------
void loop() {
  static bool imuInitAttempted = false;
  if (schedulerStarted && !imuInitAttempted) {
    imuInitAttempted = true;
    serviceManchesterTx(); // Publish the initial frame log before the IMU probe.
    imuValid = initIMU();
    imuActivity.reset(millis());
    lastImuPollMs = millis();
  }
  const uint32_t now = txClockUs();
  if (previousLoopUs) {
    const uint32_t blocked = now - previousLoopUs;
    if (blocked > maxLoopBlockingUs) maxLoopBlockingUs = blocked;
  }
  previousLoopUs = now;
  serviceManchesterTx();
  if (!VLC_BATTERY_BENCHMARK || batteryStage != 5) serviceSensorsAndPriorityAlert();
  serviceNextFrame();
  serviceManchesterTx();
  if (VLC_BATTERY_BENCHMARK) return;

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
      const uint32_t halfUs = cmd.endsWith("1000") ? 1000000UL : 100000UL;
      const uint32_t key = lockTx();
      nextReady = false;
      ledContrastHalfUs = halfUs;
      contrastRequested = true;
      NVIC_SetPendingIRQ(TIMER4_IRQn);
      unlockTx(key);
    } else if (cmd.equalsIgnoreCase("TIMING ON") || cmd.equalsIgnoreCase("TIMING OFF")) {
      timingEnabled = cmd.equalsIgnoreCase("TIMING ON");
      Serial.print(F("[TIMING] ")); Serial.println(timingEnabled ? F("ON") : F("OFF"));
    } else if (cmd.equalsIgnoreCase("IMU ON") || cmd.equalsIgnoreCase("IMU OFF")) {
      imuDiagnostics = cmd.equalsIgnoreCase("IMU ON");
      Serial.print(F("[IMU_DIAGNOSTICS] ")); Serial.println(imuDiagnostics ? F("ON") : F("OFF"));
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
      const uint32_t key = lockTx(); nextReady = false; unlockTx(key);
      Serial.println(F("[PAYLOAD_TEST] ON pending_frame_boundary; SYNTHETIC channel-test data"));
    } else if (cmd.equalsIgnoreCase("PAYLOAD TEST OFF")) {
      testPayloadRequested = false;
      const uint32_t key = lockTx(); nextReady = false; unlockTx(key);
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
