#pragma once
#include <stdint.h>
#include <math.h>

#ifndef IMU_ACTIVITY_MIN_WINDOWS
#define IMU_ACTIVITY_MIN_WINDOWS 4  // of the last 10 one-second windows
#endif
#ifndef IMU_MOVE_SD_G
#define IMU_MOVE_SD_G 0.015f  // tuned 2026-11-08 on forehead (seated still: <=2 windows >0.015 g in any 10 s; head nodding: up to 10/10)
#endif

// Population SD of acceleration magnitude over non-overlapping 1-s windows.
// Strictly greater than the threshold = moving; >=IMU_ACTIVITY_MIN_WINDOWS of the last 10 = activity 1.
// Missing startup windows are still. Empty/one-sample windows cannot show motion.
struct ImuActivity {
  uint32_t windowStartMs = 0;
  uint16_t samples = 0;
  float mean = 0, m2 = 0, sdG = NAN;
  bool history[10] = {}, windowMoving = false;
  uint8_t head = 0, windowsMoving = 0;

  void reset(uint32_t nowMs) {
    windowStartMs = nowMs;
    samples = 0; mean = m2 = 0; sdG = NAN;
    head = windowsMoving = 0; windowMoving = false;
    for (uint8_t i = 0; i < 10; ++i) history[i] = false;
  }
  void add(float ax, float ay, float az) {
    const float magnitude = sqrtf(ax * ax + ay * ay + az * az);
    ++samples;
    const float delta = magnitude - mean;
    mean += delta / samples;
    m2 += delta * (magnitude - mean); // Welford: stable near constant 1 g.
  }
  void advance(uint32_t nowMs) {
    // A long scheduling gap expires all history without unbounded catch-up work.
    if ((uint32_t)(nowMs - windowStartMs) > 10000UL) {
      reset(nowMs);
      return;
    }
    while ((uint32_t)(nowMs - windowStartMs) >= 1000UL) {
      sdG = samples >= 2 ? sqrtf(fmaxf(0.0f, m2 / samples)) : NAN;
      windowMoving = samples >= 2 && sdG > IMU_MOVE_SD_G;
      windowsMoving -= history[head] ? 1 : 0;
      history[head] = windowMoving;
      windowsMoving += windowMoving ? 1 : 0;
      head = (head + 1) % 10;
      samples = 0; mean = m2 = 0;
      windowStartMs += 1000UL;
    }
  }
  uint8_t activity() const { return windowsMoving >= IMU_ACTIVITY_MIN_WINDOWS ? 1 : 0; }
};
