#pragma once
#include <stdint.h>

// Exact finite 20-Hz release sequence, safe across micros() rollover.
struct PipelineSchedule {
  uint32_t startUs, expected, completed;
  void reset(uint32_t start, uint32_t jobs) { startUs=start;expected=jobs;completed=0; }
  uint32_t releaseUs() const {return startUs+(completed+1)*50000UL;}
  bool due(uint32_t observedUs) const {
    return completed<expected && (int32_t)(observedUs-releaseUs())>=0;
  }
};
