#include "../pipeline_schedule.h"
#include <assert.h>
#include <stdio.h>
int main() {
  const uint32_t starts[]={0U,0xFFFF0000U};
  for (uint32_t start: starts) {
    PipelineSchedule s;s.reset(start,1200);
    for(uint32_t frame=0;frame<1900;++frame) {
      uint32_t now=start+frame*33333UL;
      while(s.due(now))++s.completed;
    }
    assert(s.completed==1200 && !s.due(start+65000000UL));
    s.reset(start,3);assert(!s.due(start+49999));assert(s.due(start+50000));
    ++s.completed;assert(!s.due(start+50000));assert(s.due(start+200000));
    while(s.due(start+200000))++s.completed;
    assert(s.completed==3);
  }
  puts("20-Hz finite releases + wrap + backlog PASS");
}
