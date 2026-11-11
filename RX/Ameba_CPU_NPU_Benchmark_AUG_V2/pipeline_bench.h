#pragma once
#include "pipeline_schedule.h"
#include <new>

// Controlled camera workload: actual JPEG+fixed ROI+resampling and per-bit
// normalization; CNN input is the SAME replay bank, NOT live optical payload.
// Included after runCpu/runNpu to share the phase-A backend implementation.
struct PipeTiming {
  uint32_t n=0, maximum=0, bins[256]={};
  uint64_t sum=0;
  void add(uint32_t us) {
    ++n;sum+=us;if(us>maximum)maximum=us;
    uint32_t k=us/250; ++bins[k<255?k:255];
  }
  uint32_t p95() const {
    uint32_t target=(n*95+99)/100,seen=0;
    for(unsigned i=0;i<256;++i){seen+=bins[i];if(seen>=target)return i==255?maximum:(i+1)*250;}
    return 0;
  }
};
static JPEGDEC pipeJpeg;
static unsigned pipeX=92,pipeY=90,pipeW=67,pipeH=60;
static uint32_t pipeRoiSum,pipeRoiCount,pipeGridSum[240],pipeGridCount[240];
static float pipeGridPrevious[240]={},pipeGridActivity[240]={};
static float pipeRing[192]={};static unsigned pipeHead=0;
static int8_t pipeNormalized[192];
static uint32_t pipePreviousRawUs=0;static float pipePreviousRaw=0;
static bool pipePrimed=false;
static bool pipePixelFormatError=false;
static int pipeJpegBlock(JPEGDRAW *block) {
  if(block->iBpp!=8){pipePixelFormatError=true;return 0;}
  const uint8_t *pixels=reinterpret_cast<const uint8_t*>(block->pPixels);
  for(int yy=0;yy<block->iHeight;++yy){
    int y=block->y+yy;if(y<0||y>=192)continue;
    for(int xx=0;xx<block->iWidthUsed;++xx){
      int x=block->x+xx;if(x<0||x>=320)continue;
      uint8_t v=pixels[yy*block->iWidth+xx];unsigned grid=(y/16)*20+x/16;
      pipeGridSum[grid]+=v;++pipeGridCount[grid];
      if((unsigned)x>=pipeX&&(unsigned)x<pipeX+pipeW&&
         (unsigned)y>=pipeY&&(unsigned)y<pipeY+pipeH){pipeRoiSum+=v;++pipeRoiCount;}
    }
  }
  return 1;
}
struct PipeStats {
  uint32_t frames=0,jpegErrors=0,emptyImages=0,missingRoi=0;
  uint32_t previousFrame=0,firstFrame=0,lastFrame=0,gaps100=0;
  uint32_t deadlineMisses=0,catchupJobs=0,maxBatch=0,cpuMismatches=0,bitDisagreements=0;
  uint32_t firstSample=0,lastSample=0,sequenceHash=2166136261UL;
  uint64_t roiMeanMilliSum=0;
  PipeTiming intervals,inferenceCore,inferenceFull,response,releaseLateness,jpeg;
};
static void pipeMetric(const char *name,const PipeTiming &m) {
  Serial.print(",\"");Serial.print(name);Serial.print("\":{\"n\":");Serial.print(m.n);
  Serial.print(",\"mean_us\":");Serial.print(m.n?(double)m.sum/m.n:0.0,3);
  Serial.print(",\"p95_upper_us\":");Serial.print(m.p95());
  Serial.print(",\"max_us\":");Serial.print(m.maximum);Serial.print('}');
}
static bool pipelineStage(const char *backend,unsigned seconds,bool measured) {
  static PipeStats s;new (&s) PipeStats();PipelineSchedule schedule;
  uint32_t start=micros();schedule.reset(start,seconds*20U);
  unsigned finalHashInput=0;
  while(schedule.completed<schedule.expected){
    uint32_t address=0,length=0;
    pipeRoiSum=pipeRoiCount=0;
    pipePixelFormatError=false;
    memset(pipeGridSum,0,sizeof(pipeGridSum));memset(pipeGridCount,0,sizeof(pipeGridCount));
    uint32_t jpegStart=micros();Camera.getImage(0,&address,&length);
    if(!address||!length){++s.emptyImages;delay(1);}
    else if(!pipeJpeg.openFLASH(reinterpret_cast<uint8_t*>(address),length,pipeJpegBlock))++s.jpegErrors;
    else {
      // openFLASH resets JPEGDEC settings: select output format per image.
      pipeJpeg.setPixelType(EIGHT_BIT_GRAYSCALE);
      int success=pipeJpeg.decode(0,0,JPEG_LUMA_ONLY);pipeJpeg.close();
      if(!success||pipePixelFormatError)++s.jpegErrors;
      else {
        uint32_t rawUs=micros();s.jpeg.add(rawUs-jpegStart);
        if(s.frames){uint32_t dt=rawUs-s.previousFrame;s.intervals.add(dt);if(dt>100000UL)++s.gaps100;}
        else s.firstFrame=rawUs;
        s.previousFrame=s.lastFrame=rawUs;++s.frames;
        for(unsigned i=0;i<240;++i)if(pipeGridCount[i]){
          float mean=(float)pipeGridSum[i]/pipeGridCount[i];
          pipeGridActivity[i]=.92f*pipeGridActivity[i]+.08f*fabsf(mean-pipeGridPrevious[i]);
          pipeGridPrevious[i]=mean;
        }
        if(!pipeRoiCount)++s.missingRoi;
        else {
          float raw=(float)pipeRoiSum/pipeRoiCount;s.roiMeanMilliSum+=(uint32_t)(raw*1000);
          if(!pipePrimed){pipePreviousRawUs=rawUs;pipePreviousRaw=raw;pipePrimed=true;}
          unsigned batch=0;
          // Use the timestamp of this decoded image, just as a timestamp
          // resampler does. Never drop a due job to make a slow backend look fast.
          while(schedule.due(rawUs)){
            uint32_t release=schedule.releaseUs();unsigned idx=schedule.completed%VLC_CPU_BANK_N;
            float fraction=(int32_t)(release-pipePreviousRawUs)/(float)(rawUs-pipePreviousRawUs?rawUs-pipePreviousRawUs:1);
            if(fraction<0)fraction=0;if(fraction>1)fraction=1;
            pipeRing[pipeHead]=pipePreviousRaw+(raw-pipePreviousRaw)*fraction;pipeHead=(pipeHead+1)%192;
            // Same live preprocessing overhead for all modes, even though
            // the model reads a controlled replay tensor instead of this one.
            for(unsigned bit=0;bit<48;++bit){
              float v[4],lo=1e30f,hi=-1e30f;
              for(unsigned j=0;j<4;++j){v[j]=pipeRing[(pipeHead+bit*4+j)%192];if(v[j]<lo)lo=v[j];if(v[j]>hi)hi=v[j];}
              for(unsigned j=0;j<4;++j){float norm=hi-lo>.5f?(v[j]-lo)/(hi-lo):0;
                pipeNormalized[bit*4+j]=(int8_t)((int)(norm*255+.5f)-128);}
            }
            checksum+=(uint8_t)pipeNormalized[schedule.completed%192];
            uint32_t jobStart=micros();s.releaseLateness.add(jobStart-release);
            PairTiming t{};
            if(strcmp(backend,"cpu")==0){runCpu(VLC_CPU_BANK_INPUT[idx],t);s.inferenceCore.add(t.cpuCore);s.inferenceFull.add(t.cpuFull);}
            else if(strcmp(backend,"npu")==0){
              if(!runNpu(VLC_CPU_BANK_INPUT[idx],t)){Serial.println("[GOAL2_ERROR] pipeline NPU failed");return false;}
              s.inferenceCore.add(t.npuCore);s.inferenceFull.add(t.npuFull);
            }
            uint32_t finish=micros(),response=finish-release;s.response.add(response);
            if(response>50000UL)++s.deadlineMisses;
            if(strcmp(backend,"camera")!=0){
              const int8_t *output=strcmp(backend,"cpu")==0?cpuOutput:npuOutput;
              for(unsigned j=0;j<48;++j){
                if(strcmp(backend,"cpu")==0&&output[j]!=VLC_CPU_BANK_EXPECTED[idx][j])++s.cpuMismatches;
                s.bitDisagreements+=(output[j]>=0)!=(VLC_CPU_BANK_EXPECTED[idx][j]>=0);
                checksum+=(uint8_t)output[j];
              }
            }
            if(schedule.completed==0)s.firstSample=idx;
            s.lastSample=idx;s.sequenceHash=(s.sequenceHash^idx)*16777619UL;
            ++schedule.completed;++batch;finalHashInput=idx;
          }
          if(batch>1)s.catchupJobs+=batch-1;if(batch>s.maxBatch)s.maxBatch=batch;
          pipePreviousRawUs=rawUs;pipePreviousRaw=raw;
        }
      }
    }
    // Abort if camera stops; never loop forever waiting for due samples.
    if((uint32_t)(micros()-start)>(seconds+10U)*1000000UL){
      Serial.println("[GOAL2_ERROR] pipeline timeout/camera stall");return false;
    }
  }
  if(!measured)return true;
  uint32_t elapsed=micros()-start;
  Serial.print("[GOAL2_PIPE_REPORT] {\"build\":\"paired-int8-pipeline-v2\",\"backend\":\"");Serial.print(backend);
  Serial.print("\",\"elapsed_us\":");Serial.print(elapsed);Serial.print(",\"frames\":");Serial.print(s.frames);
  Serial.print(",\"decoded_jpeg_fps\":");Serial.print(s.intervals.n?(double)s.intervals.n*1000000/s.intervals.sum:0,4);
  Serial.print(",\"jobs\":");Serial.print(schedule.completed);Serial.print(",\"expected_jobs\":");Serial.print(schedule.expected);
  Serial.print(",\"sequence_hash\":");Serial.print(s.sequenceHash);
  Serial.print(",\"bank_last\":");Serial.print(finalHashInput);
  Serial.print(",\"jpeg_errors\":");Serial.print(s.jpegErrors);Serial.print(",\"empty_images\":");Serial.print(s.emptyImages);
  Serial.print(",\"missing_roi\":");Serial.print(s.missingRoi);Serial.print(",\"gaps_over_100ms\":");Serial.print(s.gaps100);
  Serial.print(",\"deadline_misses_50ms\":");Serial.print(s.deadlineMisses);
  Serial.print(",\"catchup_jobs\":");Serial.print(s.catchupJobs);Serial.print(",\"max_batch\":");Serial.print(s.maxBatch);
  Serial.print(",\"cpu_reference_unequal_values\":");Serial.print(s.cpuMismatches);
  Serial.print(",\"bit_disagreements_to_reference\":");Serial.print(s.bitDisagreements);
  Serial.print(",\"roi\":[");Serial.print(pipeX);Serial.print(',');Serial.print(pipeY);Serial.print(',');
  Serial.print(pipeW);Serial.print(',');Serial.print(pipeH);Serial.print(']');
  Serial.print(",\"roi_mean\":");Serial.print(s.frames?(double)s.roiMeanMilliSum/s.frames/1000:0,3);
  Serial.print(",\"raw_logging\":false,\"model_input\":\"fixed_bank_replay\"");
  pipeMetric("frame_interval",s.intervals);pipeMetric("capture_jpeg_wall",s.jpeg);
  pipeMetric("inference_core",s.inferenceCore);pipeMetric("inference_full",s.inferenceFull);
  pipeMetric("response",s.response);pipeMetric("release_lateness",s.releaseLateness);
  Serial.println('}');
  return true;
}
static void pipelineBenchmark(const char *backend,unsigned seconds) {
  if(!ready||seconds<10||seconds>120){Serial.println("[GOAL2_ERROR] PIPE requires ready and seconds 10..120");return;}
  PairTiming t{};unsigned mismatch=0;
  for(unsigned i=0;i<VLC_CPU_BANK_N;++i){runCpu(VLC_CPU_BANK_INPUT[i],t);
    for(unsigned j=0;j<48;++j)mismatch+=cpuOutput[j]!=VLC_CPU_BANK_EXPECTED[i][j];}
  if(mismatch){Serial.println("[GOAL2_ERROR] pipeline CPU parity failed");return;}
  Serial.print("[GOAL2_PIPE_BEGIN] backend=");Serial.print(backend);Serial.print(" seconds=");Serial.print(seconds);
  Serial.println(" warmup_s=10 input=fixed_bank_replay cpu_parity=PASS");Serial.flush();
  pipePrimed=false;memset(pipeRing,0,sizeof(pipeRing));pipeHead=0;
  if(!pipelineStage(backend,10,false))return;
  // Warm-up stage is not included; replay sequence restarts at bank input 0.
  pipelineStage(backend,seconds,true);
}
