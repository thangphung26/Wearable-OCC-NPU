#include <Arduino.h>
#include <Wire.h>
#include "BoundedImuBus.h"

static void step(const char *message) {
  Serial.print(F("[PROBE] ")); Serial.println(message); Serial.flush();
}

static bool check(bool ok, const char *name) {
  Serial.print(F("[PROBE] ")); Serial.print(name);
  Serial.print(ok ? F(" ok") : F(" FAIL reason="));
  if (!ok) {
    Serial.print(ImuBus::error);
    Serial.print(F(" errorsrc=0x")); Serial.print(ImuBus::errorSource, HEX);
  }
  Serial.println(); Serial.flush();
  return ok;
}

static void probe() {
  step("power pin H0H1 HIGH begin");
  ImuBus::powerOn();
  step("power pin H0H1 HIGH ok");
  step("power delay 100 ms begin");
  delay(100);
  step("power delay 100 ms ok");

  ImuBus::gpioInputs();
  Serial.print(F("[PROBE] GPIO idle SDA=")); Serial.print(digitalRead(PIN_WIRE1_SDA));
  Serial.print(F(" SCL=")); Serial.println(digitalRead(PIN_WIRE1_SCL)); Serial.flush();
  if (!ImuBus::idle()) { step("FAIL step=gpio-idle (bus held low)"); return; }

  step("Wire1.begin begin");
  Wire1.begin();
  step("Wire1.begin ok");
  step("setClock(100000) begin");
  Wire1.setClock(100000);
  step("setClock(100000) ok");

  uint8_t id = 0;
  step("WHO_AM_I read begin");
  if (!check(ImuBus::read(0x0F, &id, 1), "WHO_AM_I read")) return;
  Serial.print(F("[PROBE] WHO_AM_I=0x")); Serial.println(id, HEX); Serial.flush();
  if (id != 0x6A && id != 0x69) { step("FAIL step=whoami-value"); return; }
  step("CTRL3_C=0x44 write begin (BDU + auto-increment)");
  if (!check(ImuBus::write(0x12, 0x44), "CTRL3_C write")) return;
  step("CTRL2_G=0x00 write begin (gyro off)");
  if (!check(ImuBus::write(0x11, 0x00), "CTRL2_G write")) return;
  step("CTRL1_XL=0x40 write begin (104 Hz, +/-2 g)");
  if (!check(ImuBus::write(0x10, 0x40), "CTRL1_XL write")) return;
  uint8_t config[3];
  step("configuration readback begin");
  if (!check(ImuBus::read(0x10, config, 3), "configuration readback")) return;
  if (config[0] != 0x40 || config[1] != 0 || config[2] != 0x44) {
    step("FAIL step=config-value"); return;
  }
  delay(100); // Allow accelerometer startup/filter settling before first sample.
  for (uint8_t i = 0; i < 20; ++i) {
    Serial.print(F("[PROBE] accel read begin n=")); Serial.println(i + 1); Serial.flush();
    uint8_t raw[6];
    if (!check(ImuBus::read(0x28, raw, 6), "accel read")) return;
    Serial.print(F("[PROBE] accel_g n=")); Serial.print(i + 1);
    for (uint8_t axis = 0; axis < 3; ++axis) {
      const int16_t value = (int16_t)((uint16_t)raw[2 * axis] | ((uint16_t)raw[2 * axis + 1] << 8));
      Serial.print(axis == 0 ? F(" x=") : axis == 1 ? F(" y=") : F(" z="));
      Serial.print(value * 0.000061f, 5);
    }
    Serial.println(); Serial.flush();
    delay(20);
  }
}

void setup() {
  // Optical D9 and all active-low on-board LEDs remain off throughout the probe.
  digitalWrite(D9, LOW); pinMode(D9, OUTPUT);
  digitalWrite(LED_RED, HIGH); pinMode(LED_RED, OUTPUT);
  digitalWrite(LED_GREEN, HIGH); pinMode(LED_GREEN, OUTPUT);
  digitalWrite(LED_BLUE, HIGH); pinMode(LED_BLUE, OUTPUT);
  Serial.begin(115200);
  const uint32_t started = millis();
  while (!Serial && (uint32_t)(millis() - started) < 3000UL) delay(1);
  step("USB wait complete; Wire1=TWIM1; transfer limit=5 ms + STOP 1 ms");
  probe();
  ImuBus::disable();
  step("DONE"); // Also printed after a failure; inspect preceding FAIL lines.
}

void loop() {}
