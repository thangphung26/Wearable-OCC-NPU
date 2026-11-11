/* Goal 2 phase A: paired INT8 inference on the SAME AmebaPro2 board.
   Standalone microbenchmark; no JPEG/ROI decoding and no channel PDR claim.
   CPU = official TFLite reference kernels, not CMSIS-NN optimized kernels.
   UART printing and quality comparisons occur outside timed regions.
*/
#include <Arduino.h>
#include <string.h>
#include "VideoStream.h"
#include "SPI.h"
#include <JPEGDEC_Libraries/JPEGDEC.h>
// Arduino macros collide with standard C++ names used by TFLite kernels.
#undef min
#undef max
#undef abs
#undef round
#include <vlc_cpu_model.h>
#include <vlc_cpu_bank.h>
#include <vlc_cpu_nbg.h>
extern "C" {
#include "vip_lite.h"
#include "hal_cache.h"
void vipnn_hardware_init(void);
void *vipnn_allocate_videomemory(uint32_t size);
}

static VideoSetting videoConfig(320,192,30,VIDEO_JPEG,1);
static CameraSetting cameraControl;
static vip_network network=VIP_NULL;
static vip_buffer inputBuffer=VIP_NULL,outputBuffer=VIP_NULL;
static int8_t *outputData=nullptr;
static bool ready=false;
static int8_t cpuInput[192],cpuOutput[48],npuOutput[48];
static const unsigned PAIRS=512;
struct PairTiming {uint32_t cpuCore,cpuFull,npuCore,npuFull;};
static PairTiming timings[PAIRS];
static int8_t checkedNpu[VLC_CPU_BANK_N][48];
static volatile uint32_t checksum=0;

static bool ok(vip_status_e status,const char *step) {
  if(status==VIP_SUCCESS)return true;
  Serial.print("[GOAL2_ERROR] ");Serial.print(step);Serial.print(' ');Serial.println((int)status);
  return false;
}
static bool tensor(bool input,vip_buffer_create_params_t &p) {
  memset(&p,0,sizeof(p));
  vip_status_e (*q)(vip_network,vip_uint32_t,vip_enum,void*)=input?vip_query_input:vip_query_output;
  if(!ok(q(network,0,VIP_BUFFER_PROP_NUM_OF_DIMENSION,&p.num_of_dims),"rank") ||
     !ok(q(network,0,VIP_BUFFER_PROP_SIZES_OF_DIMENSION,p.sizes),"shape") ||
     !ok(q(network,0,VIP_BUFFER_PROP_DATA_FORMAT,&p.data_format),"dtype") ||
     !ok(q(network,0,VIP_BUFFER_PROP_QUANT_FORMAT,&p.quant_format),"quant"))return false;
  p.memory_type=VIP_BUFFER_MEMORY_TYPE_DEFAULT;
  if(p.quant_format!=VIP_BUFFER_QUANTIZE_TF_ASYMM || p.data_format!=VIP_BUFFER_FORMAT_INT8)return false;
  if(!ok(q(network,0,VIP_BUFFER_PROP_TF_SCALE,&p.quant_data.affine.scale),"scale") ||
     !ok(q(network,0,VIP_BUFFER_PROP_TF_ZERO_POINT,&p.quant_data.affine.zeroPoint),"zero"))return false;
  unsigned count=1;for(unsigned i=0;i<p.num_of_dims;++i)count*=p.sizes[i];
  const float scale=input?1.0f/255.0f:1.0f/256.0f;
  return count==(input?192U:48U) && p.quant_data.affine.zeroPoint==-128 &&
         fabsf(p.quant_data.affine.scale-scale)<1e-8f;
}
static bool initNpu() {
  vipnn_hardware_init();if(!ok(vip_init(),"init"))return false;
  uint8_t *model=static_cast<uint8_t*>(vipnn_allocate_videomemory(vlc_edge_cnn_nb_len));
  if(!model)return false;
  memcpy(model,vlc_edge_cnn_nb,vlc_edge_cnn_nb_len);
  dcache_clean_by_addr(reinterpret_cast<uint32_t*>(model),vlc_edge_cnn_nb_len);
  if(!ok(vip_create_network(model,vlc_edge_cnn_nb_len,VIP_CREATE_NETWORK_FROM_MEMORY,&network),"network"))return false;
  vip_buffer_create_params_t in,out;
  if(!tensor(true,in)||!tensor(false,out))return false;
  if(!ok(vip_create_buffer(&in,sizeof(in),&inputBuffer),"input") ||
     !ok(vip_create_buffer(&out,sizeof(out),&outputBuffer),"output") ||
     !ok(vip_prepare_network(network),"prepare") ||
     !ok(vip_set_output(network,0,outputBuffer),"set output"))return false;
  outputData=static_cast<int8_t*>(vip_map_buffer(outputBuffer));return outputData!=nullptr;
}
static void runCpu(const int8_t *x,PairTiming &t) {
  uint32_t full=micros();memcpy(cpuInput,x,192);
  uint32_t core=micros();vlcCpuInfer(cpuInput,cpuOutput);t.cpuCore=micros()-core;
  t.cpuFull=micros()-full;
}
static bool runNpu(const int8_t *x,PairTiming &t) {
  uint32_t full=micros();
  int8_t *input=static_cast<int8_t*>(vip_map_buffer(inputBuffer));if(!input)return false;
  memcpy(input,x,192);dcache_clean_by_addr(reinterpret_cast<uint32_t*>(input),192);
  if(!ok(vip_unmap_buffer(inputBuffer),"unmap") || !ok(vip_set_input(network,0,inputBuffer),"set input"))return false;
  uint32_t core=micros();vip_status_e s=vip_run_network(network);t.npuCore=micros()-core;
  if(!ok(s,"run") || !ok(vip_flush_buffer(outputBuffer,VIP_BUFFER_OPER_TYPE_INVALIDATE),"invalidate"))return false;
  memcpy(npuOutput,outputData,48);t.npuFull=micros()-full;return true;
}
static void hex(const int8_t *v) {
  const char *d="0123456789ABCDEF";
  for(unsigned i=0;i<48;++i){uint8_t q=(uint8_t)v[i];Serial.print(d[q>>4]);Serial.print(d[q&15]);}
}
static void info() {
  if(!ready){Serial.println("[GOAL2_ERROR] not ready");return;}
  Serial.print("[GOAL2_READY] build=paired-int8-pipeline-v2 accuracy=buffered-v1 snapshot=serial-jpeg-v1 preprocess=jpeg-gray8-v2 exposure_us=2000 gain=256 bank=");Serial.print(VLC_CPU_BANK_N);
  Serial.print(" pairs=");Serial.print(PAIRS);Serial.print(" cpu_workspace_bytes=");Serial.print(VLC_CPU_WORKSPACE_BYTES);
  Serial.print(" model_sha256=");Serial.println(VLC_CPU_MODEL_SHA256);
  Serial.print("[GOAL2_BUILD] f_cpu_macro=");Serial.print(F_CPU);
  Serial.print(" compiler=");Serial.println(__VERSION__);
}
static void benchmark() {
  if(!ready){Serial.println("[GOAL2_ERROR] not ready");return;}
  Serial.println("[GOAL2_BEGIN] CPU reference vs VIP NPU; video domain active; JPEG/ROI inactive");
  PairTiming t{};
  // Validate all bank outputs before accepting performance measurements.
  unsigned cpuMismatches=0;
  for(unsigned i=0;i<VLC_CPU_BANK_N;++i){
    runCpu(VLC_CPU_BANK_INPUT[i],t);
    for(unsigned j=0;j<48;++j)cpuMismatches+=cpuOutput[j]!=VLC_CPU_BANK_EXPECTED[i][j];
    if(!runNpu(VLC_CPU_BANK_INPUT[i],t))return;
    memcpy(checkedNpu[i],npuOutput,48);delay(1);
  }
  Serial.print("[GOAL2_CPU_PARITY] unequal_values=");Serial.println(cpuMismatches);
  if(cpuMismatches){Serial.println("[GOAL2_ERROR] CPU reference parity failed; timings invalid");return;}
  Serial.flush(); // No pending UART output interrupts during timed pairs.
  // All 64 inputs warmed both backends. Alternate AB/BA order per pair.
  for(unsigned i=0;i<PAIRS;++i){
    const int8_t *x=VLC_CPU_BANK_INPUT[i%VLC_CPU_BANK_N];
    if(i&1){if(!runNpu(x,timings[i]))return;runCpu(x,timings[i]);}
    else {runCpu(x,timings[i]);if(!runNpu(x,timings[i]))return;}
    checksum+=(uint8_t)cpuOutput[i%48]+(uint8_t)npuOutput[i%48];
    delay(1); // Outside timing, service RTOS/watchdog fairly for both backends.
  }
  // Every print below is after all timed invocations.
  for(unsigned i=0;i<VLC_CPU_BANK_N;++i){
    Serial.print("[GOAL2_OUTPUT] sample=");Serial.print(i);Serial.print(" expected=");
    hex(VLC_CPU_BANK_EXPECTED[i]);Serial.print(" npu=");hex(checkedNpu[i]);Serial.println();
  }
  for(unsigned i=0;i<PAIRS;++i){
    Serial.print("[GOAL2_PAIR] i=");Serial.print(i);Serial.print(" sample=");Serial.print(i%VLC_CPU_BANK_N);
    Serial.print(" cpu_core_us=");Serial.print(timings[i].cpuCore);
    Serial.print(" cpu_full_us=");Serial.print(timings[i].cpuFull);
    Serial.print(" npu_core_us=");Serial.print(timings[i].npuCore);
    Serial.print(" npu_full_us=");Serial.println(timings[i].npuFull);
  }
  Serial.print("[GOAL2_COMPLETE] pairs=");Serial.print(PAIRS);Serial.print(" checksum=");Serial.println(checksum);
}
#include "pipeline_bench.h"
#include "accuracy_bench.h"

// Setup-only JPEG transport for roi_confirm.py. Never called during capture,
// timed inference or replay; does not change camera preset or model inputs.
static void roiSnapshot() {
  uint32_t address=0,length=0;
  // Drain a few frames so the manual selection does not use an idle image.
  for(unsigned i=0;i<8;++i){Camera.getImage(0,&address,&length);delay(35);}
  if(!address||!length){Serial.println("[GOAL2_ERROR] snapshot unavailable");return;}
  Serial.print("[SNAPSHOT_BEGIN] ");Serial.print(length);Serial.print(' ');
  Serial.print(pipeX);Serial.print(' ');Serial.print(pipeY);Serial.print(' ');
  Serial.print(pipeW);Serial.print(' ');Serial.println(pipeH);
  Serial.write(reinterpret_cast<const uint8_t*>(address),length);
  Serial.println();Serial.println("[SNAPSHOT_END]");Serial.flush();
}

void setup() {
  Serial.begin(115200);delay(1500);
  Camera.configVideoChannel(0,videoConfig);Camera.videoInit(0);Camera.channelBegin(0);delay(200);
  // Exposure ablation at 7 m: 2000 us; all other settings remain unchanged.
  // Original production/previous benchmark exposure was 500 us.
  cameraControl.setWDR(0);cameraControl.setExposureMode(0);
  cameraControl.setExposureTime(2000);cameraControl.setAEGain(256);
  cameraControl.setAWB(0);cameraControl.setRedBalance(357);cameraControl.setBlueBalance(399);
  ready=initNpu(); // Pixel format is selected after every JPEG open.
  if(ready)info();else Serial.println("[GOAL2_ERROR] NPU init/contract failed");
}
void loop() {
  static char cmd[416];static unsigned n=0;static bool overflow=false;
  while(Serial.available()){
    char c=Serial.read();if(c=='\r')continue;
    if(c=='\n'){cmd[n]=0;n=0;
      if(overflow){overflow=false;Serial.println("[GOAL2_ERROR] command too long");continue;}
      if(strcmp(cmd,"GOAL2 INFO")==0)info();
      else if(strcmp(cmd,"SNAP")==0||strcmp(cmd,"GOAL2 SNAP")==0)roiSnapshot();
      else if(strcmp(cmd,"CONFIRM")==0){
        Serial.print("[GOAL2_ROI] ");Serial.print(pipeX);Serial.print(',');Serial.print(pipeY);
        Serial.print(',');Serial.print(pipeW);Serial.print(',');Serial.println(pipeH);
      }
      else if(strcmp(cmd,"GOAL2 RUN")==0)benchmark();
      else if(strncmp(cmd,"GOAL2 CAPTURE ",14)==0){
        unsigned seconds=0;char extra;
        if(sscanf(cmd+14,"%u %c",&seconds,&extra)==1)accuracyCapture(seconds);
        else Serial.println("[GOAL2_ERROR] CAPTURE seconds required");
      }else if(strncmp(cmd,"GOAL2 EVAL ",11)==0)accuracyEval(cmd+11);
      else if(strncmp(cmd,"GOAL2 PIPE ",11)==0){
        char backend[8];unsigned seconds=0;
        if(sscanf(cmd+11,"%7s %u",backend,&seconds)==2){
          if(strcmp(backend,"CPU")==0)pipelineBenchmark("cpu",seconds);
          else if(strcmp(backend,"NPU")==0)pipelineBenchmark("npu",seconds);
          else if(strcmp(backend,"CAMERA")==0)pipelineBenchmark("camera",seconds);
        }
      }else if(strncmp(cmd,"GOAL2 ROI ",10)==0||strncmp(cmd,"ROI_PX ",7)==0){
        unsigned x,y,w,h;
        const char *args=cmd+(strncmp(cmd,"ROI_PX ",7)==0?7:10);
        if(sscanf(args,"%u %u %u %u",&x,&y,&w,&h)==4&&w&&h&&x<320&&y<192&&w<=320-x&&h<=192-y){
          pipeX=x;pipeY=y;pipeW=w;pipeH=h;
          Serial.print("[GOAL2_ROI] ");Serial.print(x);Serial.print(',');Serial.print(y);Serial.print(',');Serial.print(w);Serial.print(',');Serial.println(h);
        }
      }
    }else if(c>=32&&c<=126){if(n<sizeof(cmd)-1)cmd[n++]=c;else overflow=true;}
  }
  delay(1);
}
