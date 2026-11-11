#pragma once
#include <Arduino.h>
#include <Wire.h>

#if !defined(NRF52840_XXAA) || WIRE_INTERFACES_COUNT != 2
#error "Requires XIAO nRF52840 Sense Wire1 on TWIM1 (Seeeduino 1.1.13)."
#endif

// Wire1.begin()/setClock() configure the pins/peripheral without waiting. The
// core's endTransmission()/requestFrom() have unbounded event waits, so use
// that same TWIM1 EasyDMA peripheral directly, with a STOP after every phase.
// Exclusive loop/setup ownership: never mix Wire1 transfers or SPI on instance 1.
namespace ImuBus {
static const uint8_t ADDRESS = 0x6A;
static const uint32_t TRANSFER_US = 5000, STOP_US = 1000;
static const char *error = "none";
static uint32_t errorSource = 0;

static void powerOn() {
  // Board map: Arduino pin 15 -> 40 -> P1.08. Normal OUTPUT uses S0S1,
  // which left the IMU supply inadequate and SDA/SCL low on tested hardware.
  const uint32_t mappedPin = g_ADigitalPinMap[PIN_LSM6DS3TR_C_POWER];
  NRF_GPIO_Type *port = (mappedPin >> 5) ? NRF_P1 : NRF_P0;
  port->PIN_CNF[mappedPin & 31UL] =
      (GPIO_PIN_CNF_DIR_Output << GPIO_PIN_CNF_DIR_Pos) |
      (GPIO_PIN_CNF_INPUT_Disconnect << GPIO_PIN_CNF_INPUT_Pos) |
      (GPIO_PIN_CNF_PULL_Disabled << GPIO_PIN_CNF_PULL_Pos) |
      (GPIO_PIN_CNF_DRIVE_H0H1 << GPIO_PIN_CNF_DRIVE_Pos) |
      (GPIO_PIN_CNF_SENSE_Disabled << GPIO_PIN_CNF_SENSE_Pos);
  // Do not call pinMode(OUTPUT) afterward: it would restore standard drive.
  digitalWrite(PIN_LSM6DS3TR_C_POWER, HIGH);
}

static bool idle() {
  return digitalRead(PIN_WIRE1_SDA) == HIGH &&
         digitalRead(PIN_WIRE1_SCL) == HIGH;
}

static void gpioInputs() {
  pinMode(PIN_WIRE1_SDA, INPUT_PULLUP);
  pinMode(PIN_WIRE1_SCL, INPUT_PULLUP);
}

static void disable() {
  NRF_TWIM1->ENABLE = 0;
  __DSB(); // End EasyDMA use before stack buffers leave scope, even on timeout.
  NRF_TWIM1->SHORTS = 0;
}

static bool transfer(uint8_t *data, uint8_t count, bool receive) {
  errorSource = 0;
  if (!idle()) { error = "bus-low"; disable(); return false; }
  if (NRF_TWIM1->ENABLE != TWIM_ENABLE_ENABLE_Enabled) {
    error = "disabled"; return false;
  }
  NRF_TWIM1->INTENCLR = 0xFFFFFFFFUL; // Polling only; TIMER4/USB stay enabled.
  NRF_TWIM1->EVENTS_STOPPED = 0;
  NRF_TWIM1->EVENTS_ERROR = 0;
  NRF_TWIM1->EVENTS_LASTTX = 0;
  NRF_TWIM1->EVENTS_LASTRX = 0;
  NRF_TWIM1->EVENTS_TXSTARTED = 0;
  NRF_TWIM1->EVENTS_RXSTARTED = 0;
  NRF_TWIM1->ERRORSRC = NRF_TWIM1->ERRORSRC;
  NRF_TWIM1->ADDRESS = ADDRESS;
  NRF_TWIM1->SHORTS = receive ? TWIM_SHORTS_LASTRX_STOP_Msk : TWIM_SHORTS_LASTTX_STOP_Msk;
  if (receive) {
    NRF_TWIM1->RXD.PTR = (uint32_t)data;
    NRF_TWIM1->RXD.MAXCNT = count;
  } else {
    NRF_TWIM1->TXD.PTR = (uint32_t)data;
    NRF_TWIM1->TXD.MAXCNT = count;
  }
  __DMB();
  const uint32_t started = micros();
  if (receive) NRF_TWIM1->TASKS_STARTRX = 1;
  else NRF_TWIM1->TASKS_STARTTX = 1;
  while (!NRF_TWIM1->EVENTS_STOPPED && !NRF_TWIM1->EVENTS_ERROR &&
         (uint32_t)(micros() - started) < TRANSFER_US) {}

  const bool timedOut = !NRF_TWIM1->EVENTS_STOPPED && !NRF_TWIM1->EVENTS_ERROR;
  const bool busError = NRF_TWIM1->EVENTS_ERROR;
  errorSource = NRF_TWIM1->ERRORSRC;
  if (timedOut || busError) {
    NRF_TWIM1->TASKS_STOP = 1;
    const uint32_t stopping = micros();
    while (!NRF_TWIM1->EVENTS_STOPPED &&
           (uint32_t)(micros() - stopping) < STOP_US) {}
    error = !NRF_TWIM1->EVENTS_STOPPED ? "stop-timeout" :
            timedOut ? "transfer-timeout" : "nack-or-bus-error";
    disable();
    return false;
  }
  __DMB();
  const uint32_t amount = receive ? NRF_TWIM1->RXD.AMOUNT : NRF_TWIM1->TXD.AMOUNT;
  if (amount != count) { error = "short-transfer"; disable(); return false; }
  error = "none";
  return true;
}

static bool write(uint8_t reg, uint8_t value) {
  uint8_t bytes[2] = {reg, value}; // EasyDMA requires RAM.
  return transfer(bytes, sizeof(bytes), false);
}

static bool read(uint8_t reg, uint8_t *data, uint8_t count) {
  return transfer(&reg, 1, false) && transfer(data, count, true);
}
} // namespace ImuBus
