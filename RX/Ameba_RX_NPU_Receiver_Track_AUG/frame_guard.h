#pragma once
#include <stdint.h>
#include <string.h>
#include <math.h>

// Physical evidence only: 0xA Manchester preamble and a preceding LOW
// delimiter longer than Manchester's maximum two equal half-bits.
// Values are on the timestamp-resampled 20-Hz signal, in candidate order.
struct FrameEvidence {
  bool valid;
  float contrast;  // Signed, allowing inverted optical polarity.
  uint8_t transitionBits;
};

static inline FrameEvidence checkFrameEvidence(const float *samples,
                                               const float *delimiter) {
  FrameEvidence e = {false, 0.0f, 0};
  // Manchester(1010) = 10 01 10 01 in LED half-levels.
  static const uint8_t preambleHalf[8] = {1, 0, 0, 1, 1, 0, 0, 1};
  float halves[8], on = 0, off = 0;
  for (uint8_t i = 0; i < 8; ++i) {
    halves[i] = (samples[i * 2] + samples[i * 2 + 1]) * 0.5f;
    if (!isfinite(halves[i])) return e;
    if (preambleHalf[i]) on += halves[i]; else off += halves[i];
  }
  on *= 0.25f; off *= 0.25f;
  e.contrast = on - off;
  if (fabsf(e.contrast) < 1.0f) return e;
  for (uint8_t i = 0; i < 8; ++i) {
    const float normalized = (halves[i] - off) / e.contrast;
    if ((preambleHalf[i] && normalized < 0.5f) ||
        (!preambleHalf[i] && normalized >= 0.5f)) return e;
  }
  // Six centers span 250 ms: a sustained 300-ms delimiter neighborhood.
  // Do not inspect the far edge, which can touch the previous last bit.
  for (uint8_t i = 0; i < 6; ++i) {
    if (!isfinite(delimiter[i]) ||
        (delimiter[i] - off) / e.contrast > 0.45f) return e;
  }
  for (uint8_t bit = 0; bit < 48; ++bit) {
    const float *v = samples + bit * 4;
    for (uint8_t j = 0; j < 4; ++j) if (!isfinite(v[j])) return e;
    const float difference = (v[0] + v[1] - v[2] - v[3]) * 0.5f;
    if (fabsf(difference) >= fabsf(e.contrast) * 0.25f) ++e.transitionBits;
  }
  // Structural evidence, independent of the predicted data bits. Allows
  // weak transitions without forcing CNN decisions to equal classic bits.
  e.valid = e.transitionBits >= 40;
  return e;
}

struct RecentFrames {
  struct Entry { uint64_t frame; uint32_t atMs; bool used; } entries[16][4];
  uint8_t next[16];
  void reset() { memset(this, 0, sizeof(*this)); }
  bool contains(uint64_t frame, uint32_t nowMs) const {
    const uint8_t patient = (frame >> 40) & 15;
    for (uint8_t i = 0; i < 4; ++i) {
      const Entry &e = entries[patient][i];
      if (e.used && e.frame == frame && (uint32_t)(nowMs - e.atMs) < 15000UL) return true;
    }
    return false;
  }
  void remember(uint64_t frame, uint32_t nowMs) {
    const uint8_t patient = (frame >> 40) & 15;
    Entry &e = entries[patient][next[patient]];
    e.frame = frame; e.atMs = nowMs; e.used = true;
    next[patient] = (next[patient] + 1) % 4;
  }
};
