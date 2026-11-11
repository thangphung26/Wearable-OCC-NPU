#pragma once
#include <stdint.h>
#include <math.h>

static const int TRACK_MARGIN_PX = 40;
static const uint8_t TRACK_BRIGHT_MIN = 200;
static const uint16_t TRACK_MIN_PIXELS = 12;
static const int TRACK_MAX_STEP_PX = 24;
static const uint32_t TRACK_LOST_MS = 1500;
static const uint32_t TRACK_RECENT_MS = 500;
static const int TRACK_BLOB_MAX_PX = 48;

struct TrackBright {
  uint32_t count, sx, sy;
  void add(int x, int y) { ++count; sx += x; sy += y; }
  int x() const { return count ? (sx + count / 2) / count : 0; }
  int y() const { return count ? (sy + count / 2) / count : 0; }
};

struct TrackHistory {
  uint32_t brightMs, darkMs;
  bool brightSeen, darkSeen;
  void observe(uint32_t count, uint32_t now) {
    if (count >= 3) { brightMs = now; brightSeen = true; }
    if (count <= 1) { darkMs = now; darkSeen = true; }
  }
  bool modulating(uint32_t now) const {
    return brightSeen && darkSeen && now - brightMs <= TRACK_RECENT_MS &&
           now - darkMs <= TRACK_RECENT_MS;
  }
};

static bool trackExpired(uint32_t now, uint32_t since, uint32_t period) {
  return now - since >= period;  // millis() rollover safe
}

static int trackBound(int v, int lo, int hi) {
  return v < lo ? lo : (v > hi ? hi : v);
}

// Clamp Euclidean displacement, including diagonals; truncation rounds inward.
static void trackPosition(int cx, int cy, int w, int h, int frameW, int frameH,
                          int oldX, int oldY, bool limit, int &x, int &y) {
  x = trackBound(cx - w / 2, 0, frameW - w);
  y = trackBound(cy - h / 2, 0, frameH - h);
  int dx = x - oldX, dy = y - oldY;
  const int d2 = dx * dx + dy * dy;
  if (limit && d2 > TRACK_MAX_STEP_PX * TRACK_MAX_STEP_PX) {
    const float scale = TRACK_MAX_STEP_PX / sqrtf((float)d2);
    x = oldX + (int)(dx * scale);
    y = oldY + (int)(dy * scale);
  }
}

// Connected bright cells must ALL have recent dark evidence. This rejects a
// static lamp even if it touches a smaller modulating region. Grid resolution
// makes this deliberately conservative; each component is at most 48x48 px.
template<int ROWS, int COLS>
static bool trackFindBlob(const TrackBright (&cells)[ROWS][COLS],
                          const TrackHistory (&history)[ROWS][COLS],
                          int cellW, int cellH, uint32_t now, TrackBright &best) {
  bool visited[ROWS][COLS] = {};
  uint16_t queue[ROWS * COLS];
  best = TrackBright{};
  for (int y = 0; y < ROWS; ++y) {
    for (int x = 0; x < COLS; ++x) {
      if (visited[y][x] || !cells[y][x].count) continue;
      int read = 0, write = 0;
      queue[write++] = y * COLS + x;
      visited[y][x] = true;
      int left = x, right = x, top = y, bottom = y;
      bool modulated = true;
      TrackBright blob = {};
      uint32_t supportedBright = 0;
      while (read < write) {
        const int cell = queue[read++], cx = cell % COLS, cy = cell / COLS;
        const TrackBright &b = cells[cy][cx];
        blob.count += b.count; blob.sx += b.sx; blob.sy += b.sy;
        // Ignore isolated one-pixel speckles as evidence, but not as a bridge
        // to a static bright object.
        if (b.count >= 2 && !history[cy][cx].modulating(now)) modulated = false;
        if (history[cy][cx].modulating(now)) supportedBright += b.count;
        left = left < cx ? left : cx; right = right > cx ? right : cx;
        top = top < cy ? top : cy; bottom = bottom > cy ? bottom : cy;
        for (int dy = -1; dy <= 1; ++dy) {
          for (int dx = -1; dx <= 1; ++dx) {
            const int nx = cx + dx, ny = cy + dy;
            if (nx < 0 || nx >= COLS || ny < 0 || ny >= ROWS ||
                visited[ny][nx] || !cells[ny][nx].count) continue;
            visited[ny][nx] = true;
            queue[write++] = ny * COLS + nx;
          }
        }
      }
      if (modulated && supportedBright >= TRACK_MIN_PIXELS &&
          (right - left + 1) * cellW <= TRACK_BLOB_MAX_PX &&
          (bottom - top + 1) * cellH <= TRACK_BLOB_MAX_PX &&
          blob.count > best.count) best = blob;
    }
  }
  return best.count >= TRACK_MIN_PIXELS;
}

