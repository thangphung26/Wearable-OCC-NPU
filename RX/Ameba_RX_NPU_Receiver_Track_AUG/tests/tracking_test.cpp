#include "../led_tracking.h"
#include <assert.h>
#include <stdio.h>

int main() {
  TrackBright b = {};
  assert(b.x() == 0 && b.y() == 0);
  for (int y = 90; y < 94; ++y)
    for (int x = 170; x < 174; ++x) b.add(x, y);
  assert(b.count == 16 && b.x() == 172 && b.y() == 92);
  int x, y;
  trackPosition(b.x(), b.y(), 24, 24, 320, 192, 160, 80, true, x, y);
  assert(x == 160 && y == 80);  // already centred
  trackPosition(200, 92, 24, 24, 320, 192, 160, 80, true, x, y);
  assert(x == 184 && y == 80);  // horizontal clamp
  for (int cx = 0; cx < 320; ++cx) {
    for (int cy = 0; cy < 192; ++cy) {
      trackPosition(cx, cy, 24, 24, 320, 192, 160, 80, true, x, y);
      int dx = x - 160, dy = y - 80;
      assert(dx * dx + dy * dy <= 24 * 24);
      assert(x >= 0 && x <= 296 && y >= 0 && y <= 168);
    }
  }
  trackPosition(319, 191, 24, 24, 320, 192, 0, 0, false, x, y);
  assert(x == 296 && y == 168);  // full-frame reacquisition
  trackPosition(0, 0, 23, 25, 320, 192, 5, 5, true, x, y);
  assert(x == 0 && y == 0);
  assert(!trackExpired(2499, 1000, TRACK_LOST_MS));
  assert(trackExpired(2500, 1000, TRACK_LOST_MS));
  assert(!trackExpired(1400, 1000, TRACK_LOST_MS)); // delimiter safe
  assert(trackExpired(0x000005DCU, 0xFFFFFFFEU, TRACK_LOST_MS));
  TrackHistory h = {};
  h.observe(12, 1000);
  assert(!h.modulating(1000));  // bright alone is a static lamp
  h.observe(12, 1400);
  assert(!h.modulating(1400));
  h.observe(0, 1450);
  assert(h.modulating(1450));
  assert(h.modulating(1900));  // 500 ms inclusive
  assert(!h.modulating(1901)); // stale bright evidence
  h = TrackHistory{};
  h.observe(0, 0xFFFFFF00U);
  h.observe(12, 0x00000010U);
  assert(h.modulating(0x00000020U));
  h = TrackHistory{};
  h.observe(0, 0);
  assert(!h.modulating(1)); // darkness alone cannot reacquire
  h.observe(2, 1);
  assert(!h.modulating(2)); // ambiguous small count isn't ON evidence
  TrackBright cells[12][20] = {};
  TrackHistory history[12][20] = {};
  TrackBright best;
  // A brighter static lamp must lose to a recently modulating LED.
  for (int i = 0; i < 32; ++i) cells[1][1].add(20, 20);
  for (int i = 0; i < 12; ++i) cells[6][10].add(170, 100);
  history[1][1].observe(32, 1000);
  history[6][10].observe(0, 900);
  history[6][10].observe(12, 1000);
  assert(trackFindBlob(cells, history, 16, 16, 1000, best));
  assert(best.count == 12 && best.x() == 170 && best.y() == 100);
  assert(!trackFindBlob(cells, history, 16, 16, 1501, best));
  // Adjacent-cell LED straddling a grid edge: aggregate centroid.
  cells[6][10] = TrackBright{};
  for (int i = 0; i < 6; ++i) {
    cells[6][10].add(175, 100); cells[6][11].add(176, 100);
  }
  history[6][11].observe(0, 900);
  history[6][11].observe(6, 1000);
  assert(trackFindBlob(cells, history, 16, 16, 1000, best));
  assert(best.count == 12 && best.x() == 176);
  // Static bright neighbour makes the entire connected component ineligible.
  cells[6][12].add(192, 100); cells[6][12].add(193, 100);
  assert(!trackFindBlob(cells, history, 16, 16, 1000, best));
  cells[6][12] = TrackBright{};
  // Four adjacent grid cells exceed the 48-px compactness limit.
  for (int cx = 10; cx < 14; ++cx) {
    for (int i = 0; i < 12; ++i) cells[6][cx].add(cx * 16, 100);
    history[6][cx].observe(0, 900); history[6][cx].observe(12, 1000);
  }
  assert(!trackFindBlob(cells, history, 16, 16, 1000, best));
  puts("tracking math: PASS (centroid, 61,440 step targets, bounds, timers, modulation)");
  puts("blob search: PASS (static lamp, split LED, stale history, static neighbour, size limit)");
}
