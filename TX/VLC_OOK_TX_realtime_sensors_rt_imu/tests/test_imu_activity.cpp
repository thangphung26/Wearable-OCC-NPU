#include "../ImuActivity.h"
#include <assert.h>
#include <stdio.h>

static void window(ImuActivity &a, uint32_t end, float amplitude) {
  for (int i = 0; i < 50; ++i) a.add(0, 0, 1.0f + (i % 2 ? amplitude : -amplitude));
  a.advance(end - 1);
  assert(a.samples == 50); // No early window closure.
  a.advance(end);
  assert(a.samples == 0);
  assert(fabsf(a.sdG - amplitude) < 0.000001f);
}

int main() {
  ImuActivity a;
  a.reset(0);
  assert(a.activity() == 0);
  for (uint32_t t = 1000; t <= 30000; t += 1000) window(a, t, 0.001f);
  assert(a.windowsMoving == 0);
  window(a, 31000, IMU_MOVE_SD_G - 0.0001f);
  assert(!a.windowMoving);
  window(a, 32000, IMU_MOVE_SD_G + 0.0001f);
  assert(a.windowMoving && a.windowsMoving == 1 && a.activity() == 0);
  window(a, 33000, 0.05f);
  assert(a.windowsMoving == 2 && a.activity() == 1);
  for (uint32_t t = 34000; t <= 41000; t += 1000) window(a, t, 0);
  assert(a.windowsMoving == 2 && a.activity() == 1);
  window(a, 42000, 0);
  assert(a.windowsMoving == 1 && a.activity() == 0);
  window(a, 43000, 0);
  assert(a.windowsMoving == 0);

  // Exact threshold: samples 0 and 2*T have population SD exactly T.
  a.reset(0);
  a.add(0, 0, 0); a.add(0, 0, 2 * IMU_MOVE_SD_G);
  a.advance(1000);
  assert(a.sdG == IMU_MOVE_SD_G && !a.windowMoving);

  a.reset(0);
  a.advance(1000); // Empty window.
  assert(!a.windowMoving && isnan(a.sdG));
  a.add(0, 0, 1);
  a.advance(2000); // One sample.
  assert(!a.windowMoving && isnan(a.sdG));
  window(a, 3000, 0.1f);
  window(a, 4000, 0.1f);
  a.advance(15000); // Long gap expires motion; reset also models read failure.
  assert(a.activity() == 0 && a.windowsMoving == 0);
  window(a, 16000, 0.1f);
  window(a, 17000, 0.1f);
  a.reset(17000);
  assert(a.activity() == 0 && a.windowsMoving == 0);

  // Unsigned elapsed times survive millis() wrap.
  const uint32_t start = UINT32_MAX - 499;
  a.reset(start);
  window(a, start + 1000U, 0.1f);
  window(a, start + 2000U, 0.1f);
  assert(a.activity() == 1);

  // Magnitude, rather than axis changes: pure gravity rotation remains still.
  a.reset(0);
  for (int i = 0; i < 50; ++i) a.add(i % 2 ? 1 : 0, 0, i % 2 ? 0 : 1);
  a.advance(1000);
  assert(a.sdG == 0 && !a.windowMoving);
  puts("IMU activity tests passed");
}
