#pragma once
// Capture only: no CNN, CRC, or per-sample UART during the timed interval.
// Float storage avoids additional quantization before window normalization.
static float accuracySamples[24000]; // 20 Hz for at most 20 minutes, 96 kB.
static int hexNibble(char c) {
  if(c>='0'&&c<='9')return c-'0';
  if(c>='a'&&c<='f')return c-'a'+10;
  if(c>='A'&&c<='F')return c-'A'+10;
  return -1;
}
static void accuracyEval(const char *s) {
  if(!ready||strlen(s)!=384){Serial.println("[GOAL2_ERROR] EVAL needs 192 hex bytes");return;}
  int8_t input[192];
  for(unsigned i=0;i<192;++i){int a=hexNibble(s[2*i]),b=hexNibble(s[2*i+1]);
    if(a<0||b<0){Serial.println("[GOAL2_ERROR] invalid hex");return;}
    input[i]=(int8_t)((a<<4)|b);
  }
  PairTiming t{};runCpu(input,t);if(!runNpu(input,t))return;
  Serial.print("[GOAL2_EVAL] cpu=");hex(cpuOutput);Serial.print(" npu=");hex(npuOutput);Serial.println();
}
static void accuracyCapture(unsigned seconds) {
  if(!ready||seconds<10||seconds>1200){Serial.println("[GOAL2_ERROR] CAPTURE seconds 10..1200");return;}
  Serial.print("[GOAL2_CAPTURE_BEGIN] seconds=");Serial.print(seconds);
  Serial.print(" roi=");Serial.print(pipeX);Serial.print(',');Serial.print(pipeY);Serial.print(',');
  Serial.print(pipeW);Serial.print(',');Serial.println(pipeH);Serial.flush();
  uint32_t start=micros(),next=start+50000UL,prevUs=start;
  float prev=0;bool primed=false;unsigned count=0,frames=0,errors=0,gaps=0;uint32_t last=0;
  while(count<seconds*20U){
    uint32_t address=0,length=0;pipeRoiSum=pipeRoiCount=0;
    pipePixelFormatError=false;
    memset(pipeGridSum,0,sizeof(pipeGridSum));memset(pipeGridCount,0,sizeof(pipeGridCount));
    Camera.getImage(0,&address,&length);
    if(address&&length&&pipeJpeg.openFLASH(reinterpret_cast<uint8_t*>(address),length,pipeJpegBlock)){
      // Must be AFTER openFLASH, which clears the pixel-format setting.
      pipeJpeg.setPixelType(EIGHT_BIT_GRAYSCALE);
      int good=pipeJpeg.decode(0,0,JPEG_LUMA_ONLY);pipeJpeg.close();
      if(good&&!pipePixelFormatError&&pipeRoiCount){
        uint32_t now=micros();float raw=(float)pipeRoiSum/pipeRoiCount;
        if(frames&&now-last>100000UL)++gaps;last=now;++frames;
        if(!primed){prev=raw;primed=true;}
        while(count<seconds*20U&&(int32_t)(now-next)>=0){
          float f=(float)(next-prevUs)/(now-prevUs?now-prevUs:1);
          if(f<0)f=0;if(f>1)f=1;
          accuracySamples[count++]=prev+(raw-prev)*f;next+=50000UL;
        }
        prevUs=now;prev=raw;
      }else ++errors;
    }else ++errors;
    if((uint32_t)(micros()-start)>(seconds+10U)*1000000UL){
      Serial.println("[GOAL2_ERROR] capture timeout; buffer incomplete");return;
    }
    delay(1);
  }
  uint32_t elapsed=micros()-start;
  // Output begins only after capture ends. Indices define exact uniform timestamps.
  Serial.print("[GOAL2_CAPTURE_STATS] start_us=");Serial.print(start);
  Serial.print(" elapsed_us=");Serial.print(elapsed);Serial.print(" frames=");Serial.print(frames);
  Serial.print(" errors=");Serial.print(errors);Serial.print(" gaps100=");Serial.println(gaps);
  for(unsigned i=0;i<count;++i){Serial.print("[GOAL2_SAMPLE] i=");Serial.print(i);
    Serial.print(" value=");Serial.println(accuracySamples[i],6);}
  Serial.print("[GOAL2_CAPTURE_COMPLETE] samples=");Serial.println(count);
}
