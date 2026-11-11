#pragma once
#include <stdint.h>
#include <string.h>

// Fixed-memory counters. Quantiles are upper bounds in 1-ms buckets;
// values >=127 ms use the measured maximum as a conservative upper bound.
struct EdgeTiming {
  uint32_t count, maximum, gaps100, bins[128];
  uint64_t total;
  void reset() { memset(this, 0, sizeof(*this)); }
  void add(uint32_t us) {
    ++count; total += us;
    if (us > maximum) maximum = us;
    if (us > 100000UL) ++gaps100;
    const uint32_t bucket = us / 1000UL;
    ++bins[bucket < 127 ? bucket : 127];
  }
  float mean() const { return count ? (float)total / count : 0.0f; }
  uint32_t percentileUpper(uint32_t percent) const {
    if (!count) return 0;
    const uint32_t target = (count * percent + 99) / 100;
    uint32_t seen = 0;
    for (uint32_t i = 0; i < 128; ++i) {
      seen += bins[i];
      if (seen >= target) return i == 127 ? maximum : (i + 1) * 1000UL;
    }
    return maximum;
  }
};
