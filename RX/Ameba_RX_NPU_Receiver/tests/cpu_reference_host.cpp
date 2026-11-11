#include <cstdio>
#include "vlc_cpu_model.h"
int main() {
  int8_t input[192], output[48];
  while (fread(input,1,sizeof(input),stdin)==sizeof(input)) {
    vlcCpuInfer(input,output);
    if (fwrite(output,1,sizeof(output),stdout)!=sizeof(output)) return 2;
  }
  return 0;
}
