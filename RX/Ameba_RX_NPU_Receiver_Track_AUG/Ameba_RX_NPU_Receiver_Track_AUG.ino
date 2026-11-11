/*
  AmebaPro2 VLC receiver integration pilot

  Camera (320x192 JPEG, requested 30 fps) -> automatic modulated-light ROI ->
  192 temporal samples -> INT8 edge CNN on NPU -> preamble/CRC validation ->
  decoded medical telemetry on Serial.

  This sketch is an engineering prototype, not a clinical alarm device.
*/

#include <Arduino.h>
#include <string.h>
#include "VideoStream.h"
#include "SPI.h"  // Exposes the bundled JPEGDEC_Libraries include path.
#include <JPEGDEC_Libraries/JPEGDEC.h>
#include "vlc_edge_cnn_nb.h"
#include "edge_perf.h"
#include "frame_guard.h"
#include "led_tracking.h"

extern "C" {
#include "vip_lite.h"
#include "hal_cache.h"
void vipnn_hardware_init(void);
void *vipnn_allocate_videomemory(uint32_t size);
}

// Must match the 5 bit/s TX and the training resampling rate.
static const uint16_t CAMERA_W = 320;
static const uint16_t CAMERA_H = 192;
// Request faster capture to compensate for synchronous JPEG snapshot/decode
// overhead. Regardless of raw FPS, the model input is timestamp-resampled to
// the same 20 Hz grid used by the Python training pipeline.
static const uint8_t CAMERA_REQUEST_FPS = 30;
static const uint8_t MODEL_SAMPLE_HZ = 20;
static const uint32_t MODEL_SAMPLE_PERIOD_US = 1000000UL / MODEL_SAMPLE_HZ;
static const uint8_t FRAMES_PER_BIT = 4;
static const uint8_t FRAME_BITS = 48;
static const uint16_t WINDOW_SAMPLES = FRAME_BITS * FRAMES_PER_BIT;
static const uint16_t RING_SAMPLES = 256;

// A 16x16 coarse grid is fine enough to estimate the LED halo size while
// keeping the per-frame activity calculation inexpensive.
static const uint8_t GRID_COLS = 20;
static const uint8_t GRID_ROWS = 12;
static const uint16_t CELL_W = CAMERA_W / GRID_COLS;
static const uint16_t CELL_H = CAMERA_H / GRID_ROWS;
// One 32x32 cell prevents background/ceiling lamps from diluting a small TX
// at 3--5 m. The initial pilot keeps it fixed after acquisition so a small
// score fluctuation cannot erase the 9.6 s temporal decoder window.
static const uint16_t ROI_RADIUS_X = 0;
static const uint16_t ROI_RADIUS_Y = 0;
static const uint16_t ROI_WARMUP_FRAMES = CAMERA_REQUEST_FPS * 2;
// One complete frame is 10 s. Allow enough margin to acquire and decode one
// frame before searching for the emitter again.
static const uint32_t ROI_SEARCH_TIMEOUT_MS = 20000UL;
static const uint32_t ROI_LOW_ACTIVITY_HOLD_MS = 3000UL;
static const float MIN_AUTO_ROI_ACTIVITY = 3.5f;
static const uint16_t MIN_AUTO_ROI_SIZE = 16;
static const uint16_t MAX_AUTO_ROI_SIZE = 128;
static const uint16_t AUTO_ROI_MARGIN = 4;

static const uint32_t EXPOSURE_US = 2000;
static const uint32_t AE_GAIN = 256;
static const uint16_t RED_BALANCE = 357;
static const uint16_t BLUE_BALANCE = 399;
static const int8_t OUTPUT_ONE_THRESHOLD = 0;
// Temporary end-to-end validation mode for the fixed diagnostic TX. Disabled
// to enable real-time dynamic medical telemetry decoding.
static const bool FIXED_TX_DIAGNOSTIC = false;
static const uint64_t DIAGNOSTIC_FRAME = 0xA14BC48CE042ULL;

#define VLC_CHANNEL 0

static VideoSetting videoConfig(CAMERA_W, CAMERA_H, CAMERA_REQUEST_FPS, VIDEO_JPEG, 1);
static CameraSetting cameraControl;
static JPEGDEC jpeg;

static vip_network network = VIP_NULL;
static vip_buffer inputBuffer = VIP_NULL;
static vip_buffer outputBuffer = VIP_NULL;
static int8_t *outputData = nullptr;
static uint8_t *modelData = nullptr;
static bool npuReady = false;

static float signalRing[RING_SAMPLES];
static uint16_t ringHead = 0;
static uint16_t ringCount = 0;
static uint32_t capturedFrames = 0;
static uint32_t captureStartMs = 0;
static uint32_t validPackets = 0;
static uint32_t crcRejects = 0;
static uint32_t classicCrcRejects = 0;
static uint32_t plausibleCandidates = 0;
static uint32_t physiologyRejects = 0;
static uint32_t preamblePasses = 0;
static uint32_t crcPasses = 0;
static uint8_t lastPredictedOnes = 0;
static uint8_t bestPreambleErrors = 4;
static uint8_t lastSequence = 0xFF;
static uint8_t lastPatient = 0xFF;
static uint32_t lastPacketMs = 0;
static uint32_t lastValidPacketMs = 0;
static uint64_t lastNpuFrame = 0;
static uint32_t lastAgreementLogMs = 0;
static uint8_t bestDiagnosticErrors = FRAME_BITS;
static float signalMinimum = 1e30f;
static float signalMaximum = -1e30f;
static float lastSignal = 0.0f;
static float lockedRoiActivity = 0.0f;
static float lockedRoiMean = 0.0f;
static bool resamplerPrimed = false;
static uint32_t previousRawUs = 0;
static uint32_t nextUniformUs = 0;
static float previousRawSignal = 0.0f;

static uint32_t gridSum[GRID_ROWS][GRID_COLS];
static uint16_t gridCount[GRID_ROWS][GRID_COLS];
static float gridPrevious[GRID_ROWS][GRID_COLS];
static float gridActivity[GRID_ROWS][GRID_COLS];
static uint32_t roiSum = 0;
static uint32_t roiCount = 0;
static uint8_t roiCellX = GRID_COLS / 2;
static uint8_t roiCellY = GRID_ROWS / 2;
static uint16_t roiPixelX = roiCellX * CELL_W;
static uint16_t roiPixelY = roiCellY * CELL_H;
static uint16_t roiPixelW = CELL_W;
static uint16_t roiPixelH = CELL_H;
static bool roiLocked = false;
static bool roiManuallyConfirmed = false;
static bool roiTried[GRID_ROWS][GRID_COLS];
static uint32_t roiLockedAtMs = 0;
static uint32_t roiLowActivitySinceMs = 0;
// Fixed-memory tracking; no image buffer. History is refreshed on every locked frame.
static bool trackEnabled = true;
static bool trackWasLost = false;
static bool trackFrameActive = false;
static uint32_t trackLastOnMs = 0, trackLostSinceMs = 0;
static uint32_t trackMoves = 0, trackLost = 0, trackReacq = 0;
static uint32_t trackLogMs = 0, trackFrameUs = 0;
static int trackLeft, trackTop, trackRight, trackBottom;
static TrackBright trackWindow;
static TrackBright trackCells[GRID_ROWS][GRID_COLS];
static TrackHistory trackHistory[GRID_ROWS][GRID_COLS];
static EdgeTiming edgeTrack;
static bool snapshotRequested = false;
static bool benchmarkLoggerEnabled = false;
static bool npuRawLogEnabled = false;
static uint32_t npuRawSlideCounter = 0;
static uint32_t npuRawSyncCounter = 0;
static uint32_t uniformSampleCounter = 0;
static uint32_t lastUniformSampleIndex = 0;
static uint32_t lastUniformSampleUs = 0;
static const uint8_t NPU_RAW_LOG_STRIDE = 10;
static int8_t npuRawInputSnapshot[WINDOW_SAMPLES];
static char npuRawHexLine[WINDOW_SAMPLES * 2 + FRAME_BITS * 2 + 2];
static bool captureOnly = false;
static uint8_t allowedPatient = 0;  // 0 accepts all IDs; explicit single-TX test filter.
static uint64_t lastPublishedFrame = 0;
static uint32_t lastPublishUs = 0;
static RecentFrames recentFrames;
static uint32_t framingRejects = 0, duplicateSuppressions = 0;

// HYBRID retains the production sliding NPU + classic timing-search fallback.
// CLASSIC uses the same timing search without any NPU calls. CAMERA still
// decodes JPEG, tracks ROI and resamples, but disables all packet decoding.
enum EdgeMode { EDGE_NORMAL, EDGE_CAMERA, EDGE_CLASSIC, EDGE_HYBRID };
static EdgeMode edgeMode = EDGE_NORMAL;
static EdgeTiming edgeIntervals, edgeNpu, edgeDecode;
static uint32_t edgeStartMs = 0, edgePreviousUs = 0;
static uint32_t edgeFrames = 0, edgeSamples = 0, edgeErrors = 0;
static bool edgePreviousValid = false;

static const char *edgeModeName() {
  return edgeMode == EDGE_CAMERA ? "camera" :
         edgeMode == EDGE_CLASSIC ? "classic" : "hybrid";
}

static void resetEdgePerf() {
  edgeIntervals.reset(); edgeNpu.reset(); edgeDecode.reset(); edgeTrack.reset();
  edgeFrames = edgeSamples = edgeErrors = 0;
  framingRejects = duplicateSuppressions = 0;
  edgePreviousValid = false;
  edgeStartMs = millis();
}

static void emitEdgeTiming(const char *name, const EdgeTiming &t) {
  Serial.print(",\""); Serial.print(name); Serial.print("\":{\"n\":");
  Serial.print(t.count); Serial.print(",\"mean_us\":"); Serial.print(t.mean(), 2);
  Serial.print(",\"p50_upper_us\":"); Serial.print(t.percentileUpper(50));
  Serial.print(",\"p95_upper_us\":"); Serial.print(t.percentileUpper(95));
  Serial.print(",\"max_us\":"); Serial.print(t.maximum); Serial.print('}');
}

static void reportEdgePerf() {
  const uint32_t elapsedMs = millis() - edgeStartMs;
  Serial.print("[EDGE_PERF] {\"mode\":\""); Serial.print(edgeModeName());
  Serial.print("\",\"elapsed_ms\":"); Serial.print(elapsedMs);
  Serial.print(",\"frames\":"); Serial.print(edgeFrames);
  Serial.print(",\"decoded_jpeg_fps\":");
  Serial.print(edgeIntervals.total ? edgeIntervals.count * 1000000.0 / edgeIntervals.total : 0.0, 3);
  Serial.print(",\"uniform_samples\":"); Serial.print(edgeSamples);
  Serial.print(",\"jpeg_errors\":"); Serial.print(edgeErrors);
  Serial.print(",\"gaps_over_100ms\":"); Serial.print(edgeIntervals.gaps100);
  Serial.print(",\"roi_locked\":"); Serial.print(roiLocked ? "true" : "false");
  Serial.print(",\"roi\":["); Serial.print(roiPixelX); Serial.print(',');
  Serial.print(roiPixelY); Serial.print(','); Serial.print(roiPixelW);
  Serial.print(','); Serial.print(roiPixelH); Serial.print(']');
  Serial.print(",\"logger\":"); Serial.print(benchmarkLoggerEnabled ? "true" : "false");
  Serial.print(",\"npu_raw\":"); Serial.print(npuRawLogEnabled ? "true" : "false");
  Serial.print(",\"frame_guard\":\"delimiter-manchester-v1\"");
  Serial.print(",\"framing_rejects\":"); Serial.print(framingRejects);
  Serial.print(",\"duplicate_suppressions\":"); Serial.print(duplicateSuppressions);
  emitEdgeTiming("frame_interval", edgeIntervals);
  emitEdgeTiming("npu_run", edgeNpu);
  emitEdgeTiming("decode_wall", edgeDecode);
  emitEdgeTiming("tracking", edgeTrack);
  Serial.println('}');
}

static void resetTemporalDecoder() {
  ringHead = ringCount = 0;
  resamplerPrimed = false;
  previousRawUs = nextUniformUs = 0;
  previousRawSignal = 0.0f;
  signalMinimum = 1e30f;
  signalMaximum = -1e30f;
  lastSignal = 0.0f;
  lastPredictedOnes = 0;
  bestPreambleErrors = 4;
  bestDiagnosticErrors = FRAME_BITS;
}

static bool check(vip_status_e status, const char *step) {
  if (status == VIP_SUCCESS) return true;
  Serial.print("[ERROR] "); Serial.print(step);
  Serial.print(" failed, VIP status="); Serial.println((int)status);
  return false;
}

static uint32_t elementCount(const vip_buffer_create_params_t &p) {
  uint32_t n = 1;
  for (uint32_t i = 0; i < p.num_of_dims; ++i) n *= p.sizes[i];
  return n;
}

static bool queryTensor(bool input, vip_buffer_create_params_t &p) {
  memset(&p, 0, sizeof(p));
  vip_status_e (*query)(vip_network, vip_uint32_t, vip_enum, void *) =
      input ? vip_query_input : vip_query_output;
  if (!check(query(network, 0, VIP_BUFFER_PROP_NUM_OF_DIMENSION, &p.num_of_dims), "query rank")) return false;
  if (!check(query(network, 0, VIP_BUFFER_PROP_SIZES_OF_DIMENSION, p.sizes), "query shape")) return false;
  if (!check(query(network, 0, VIP_BUFFER_PROP_DATA_FORMAT, &p.data_format), "query dtype")) return false;
  if (!check(query(network, 0, VIP_BUFFER_PROP_QUANT_FORMAT, &p.quant_format), "query quant")) return false;
  p.memory_type = VIP_BUFFER_MEMORY_TYPE_DEFAULT;
  if (p.quant_format == VIP_BUFFER_QUANTIZE_TF_ASYMM) {
    if (!check(query(network, 0, VIP_BUFFER_PROP_TF_SCALE, &p.quant_data.affine.scale), "query scale")) return false;
    if (!check(query(network, 0, VIP_BUFFER_PROP_TF_ZERO_POINT, &p.quant_data.affine.zeroPoint), "query zero")) return false;
  }
  return true;
}

static bool beginNpu() {
  vipnn_hardware_init();
  if (!check(vip_init(), "vip_init")) return false;
  modelData = static_cast<uint8_t *>(vipnn_allocate_videomemory(vlc_edge_cnn_nb_len));
  if (!modelData) { Serial.println("[ERROR] NBG allocation failed"); return false; }
  memcpy(modelData, vlc_edge_cnn_nb, vlc_edge_cnn_nb_len);
  dcache_clean_by_addr(reinterpret_cast<uint32_t *>(modelData), vlc_edge_cnn_nb_len);
  if (!check(vip_create_network(modelData, vlc_edge_cnn_nb_len,
                                VIP_CREATE_NETWORK_FROM_MEMORY, &network), "create network")) return false;

  vip_buffer_create_params_t inParams, outParams;
  if (!queryTensor(true, inParams) || !queryTensor(false, outParams)) return false;
  if (elementCount(inParams) != WINDOW_SAMPLES || elementCount(outParams) != FRAME_BITS ||
      inParams.data_format != VIP_BUFFER_FORMAT_INT8 || outParams.data_format != VIP_BUFFER_FORMAT_INT8) {
    Serial.println("[ERROR] Unexpected NBG tensor contract"); return false;
  }
  if (!check(vip_create_buffer(&inParams, sizeof(inParams), &inputBuffer), "create input")) return false;
  if (!check(vip_create_buffer(&outParams, sizeof(outParams), &outputBuffer), "create output")) return false;
  if (!check(vip_prepare_network(network), "prepare network")) return false;
  if (!check(vip_set_output(network, 0, outputBuffer), "set output")) return false;
  outputData = static_cast<int8_t *>(vip_map_buffer(outputBuffer));
  if (!outputData) { Serial.println("[ERROR] Map output failed"); return false; }
  Serial.println("[PASS] NPU model ready: INT8 [1,48,1,4] -> [1,48,1]");
  return true;
}

static uint8_t crc8(uint64_t data, uint8_t length) {
  uint8_t crc = 0;
  for (int i = length - 1; i >= 0; --i) {
    const uint8_t feedback = ((crc >> 7) & 1U) ^ ((data >> i) & 1U);
    crc <<= 1;
    if (feedback) crc ^= 0x07U;
  }
  return crc;
}

static bool validateAndPublish(uint64_t frame, const char *decoder, const FrameEvidence &evidence) {
  if (FIXED_TX_DIAGNOSTIC && frame != DIAGNOSTIC_FRAME) return false;
  const uint64_t payload = frame >> 8;
  const uint8_t receivedCrc = frame & 0xFFU;
  if (((payload >> 36) & 0x0FU) != 0x0AU) return false;
  ++preamblePasses;
  if (crc8(payload, 40) != receivedCrc) return false;
  ++crcPasses;

  const uint8_t patient = (payload >> 32) & 0x0F;
  const uint8_t hr = (payload >> 24) & 0xFF;
  const uint8_t spo2 = (payload >> 17) & 0x7F;
  const uint8_t tempCode = (payload >> 10) & 0x7F;
  const uint8_t imu = (payload >> 8) & 0x03;
  const uint8_t battery = (payload >> 5) & 0x07;
  const uint8_t alertFlag = (payload >> 4) & 0x01;
  const uint8_t sequence = payload & 0x0F;
  if (allowedPatient && patient != allowedPatient) return false;

  // CRC-8 plus the four-bit preamble is not sufficient when thousands of
  // sliding windows are tested. Reject values outside the transmitter's
  // representable/physiological operating envelope before confirmation.
  const bool hrMissing = (hr == 0);
  const bool spo2Missing = (spo2 == 0);
  const bool hrPlausible = hrMissing || (hr >= 30 && hr <= 220);
  const bool spo2Plausible = spo2Missing || (spo2 >= 70 && spo2 <= 100);
  const bool tempMissing = (tempCode == 127);
  if (patient == 0 || !hrPlausible || !spo2Plausible ||
      (!tempMissing && tempCode > 80)) {
    ++physiologyRejects;
    return false;
  }

  ++plausibleCandidates;
  if (!evidence.valid) { ++framingRejects; return false; }
  const uint32_t now = millis();
  if (recentFrames.contains(frame, now)) { ++duplicateSuppressions; return true; }
  recentFrames.remember(frame, now);
  lastPublishedFrame = frame;
  lastPublishUs = micros();
  lastPatient = patient;
  lastSequence = sequence;
  lastPacketMs = now;
  lastValidPacketMs = now;
  ++validPackets;
  Serial.print("[TELEMETRY:"); Serial.print(decoder);
  Serial.print("] {\"patient\":");
  Serial.print(patient); Serial.print(",\"hr\":"); Serial.print(hr);
  Serial.print(",\"spo2\":"); Serial.print(spo2);
  Serial.print(",\"temp_c\":");
  if (tempMissing) Serial.print("0.0"); else Serial.print(34.0f + tempCode * 0.1f, 1);
  Serial.print(",\"ppg_valid\":"); Serial.print((hrMissing || spo2Missing) ? "false" : "true");
  Serial.print(",\"hr_valid\":"); Serial.print(hrMissing ? "false" : "true");
  Serial.print(",\"spo2_valid\":"); Serial.print(spo2Missing ? "false" : "true");
  Serial.print(",\"temp_valid\":"); Serial.print(tempMissing ? "false" : "true");
  Serial.print(",\"imu\":"); Serial.print(imu);
  Serial.print(",\"battery\":"); Serial.print(battery);
  Serial.print(",\"alert\":"); Serial.print(alertFlag);
  Serial.print(",\"seq\":"); Serial.print(sequence);
  Serial.print(",\"rx_us\":"); Serial.print(lastPublishUs);
  Serial.print(",\"frame_hex\":\"0x");
  for (int b = 44; b >= 0; b -= 4) Serial.print((uint8_t)((frame >> b) & 15), HEX);
  Serial.println("\"}");
  return true;
}

static bool decodeNpuAndPublish(const FrameEvidence &evidence) {
  uint64_t frame = 0;
  lastPredictedOnes = 0;
  for (uint8_t i = 0; i < FRAME_BITS; ++i) {
    if (outputData[i] >= OUTPUT_ONE_THRESHOLD) ++lastPredictedOnes;
    frame = (frame << 1) | (outputData[i] >= OUTPUT_ONE_THRESHOLD ? 1ULL : 0ULL);
  }
  lastNpuFrame = frame;
  const uint64_t payload = frame >> 8;
  const uint8_t preamble = (payload >> 36) & 0x0FU;
  const uint8_t preambleErrors = __builtin_popcount((unsigned)(preamble ^ 0x0AU));
  if (preambleErrors < bestPreambleErrors) bestPreambleErrors = preambleErrors;
  if (validateAndPublish(frame, "NPU", evidence)) return true;

  // Camera polarity and exposure response can reverse the four-sample
  // Manchester signature relative to the training set. CRC still protects
  // this alternative, so test the exact complement as a separate decoder.
  const uint64_t mask48 = (1ULL << FRAME_BITS) - 1ULL;
  if (validateAndPublish((~frame) & mask48, "NPU_INV", evidence)) return true;
  ++crcRejects;
  return false;
}

static void logNpuAgreement(uint64_t classicFrame) {
  if (edgeMode != EDGE_NORMAL) return;
  const uint32_t now = millis();
  if (now - lastAgreementLogMs < 2000UL) return;
  lastAgreementLogMs = now;
  const uint64_t mask48 = (1ULL << FRAME_BITS) - 1ULL;
  const uint8_t directErrors = __builtin_popcountll(lastNpuFrame ^ classicFrame);
  const uint8_t inverseErrors =
      __builtin_popcountll(((~lastNpuFrame) & mask48) ^ classicFrame);
  Serial.print("[NPU_DIAG] errors_to_classic="); Serial.print(directErrors);
  Serial.print(" inverted="); Serial.println(inverseErrors);
}

static bool shouldLogNpuRaw(const char *path) {
  if (!npuRawLogEnabled) return false;
  uint32_t &counter = (strcmp(path, "SLIDE") == 0) ? npuRawSlideCounter : npuRawSyncCounter;
  return (counter++ % NPU_RAW_LOG_STRIDE) == 0;
}

static void emitNpuRaw(const char *path, float samplesPerBit, float endPhase,
                       uint32_t latencyUs, const int8_t *output) {
  static const char hex[] = "0123456789ABCDEF";
  Serial.print("[NPU_RAW] path="); Serial.print(path);
  Serial.print(" sample="); Serial.print(lastUniformSampleIndex);
  Serial.print(" rx_us="); Serial.print(lastUniformSampleUs);
  Serial.print(" spb="); Serial.print(samplesPerBit, 3);
  Serial.print(" phase="); Serial.print(endPhase, 3);
  Serial.print(" latency_us="); Serial.print(latencyUs);
  Serial.print(" in=");
  uint16_t pos = 0;
  for (uint16_t i = 0; i < WINDOW_SAMPLES; ++i) {
    const uint8_t value = (uint8_t)npuRawInputSnapshot[i];
    npuRawHexLine[pos++] = hex[value >> 4];
    npuRawHexLine[pos++] = hex[value & 0x0F];
  }
  npuRawHexLine[pos++] = ',';
  for (uint8_t i = 0; i < FRAME_BITS; ++i) {
    const uint8_t value = (uint8_t)output[i];
    npuRawHexLine[pos++] = hex[value >> 4];
    npuRawHexLine[pos++] = hex[value & 0x0F];
  }
  Serial.write(reinterpret_cast<const uint8_t *>(npuRawHexLine), pos);
  Serial.println();
}

static float ringSampleAgo(float age) {
  if (age < 0.0f) age = 0.0f;
  const uint16_t whole = (uint16_t)age;
  const float fraction = age - whole;
  const uint16_t newest = (ringHead + RING_SAMPLES - 1) % RING_SAMPLES;
  const uint16_t index0 = (newest + RING_SAMPLES - whole) % RING_SAMPLES;
  const uint16_t index1 = (index0 + RING_SAMPLES - 1) % RING_SAMPLES;
  return signalRing[index0] + fraction * (signalRing[index1] - signalRing[index0]);
}

static FrameEvidence candidateEvidence(float samplesPerBit, float endPhase) {
  FrameEvidence missing = {false, 0.0f, 0};
  const float step = samplesPerBit / FRAMES_PER_BIT;
  const float oldestAge = endPhase + FRAME_BITS * samplesPerBit + 5.5f * step;
  if (oldestAge + 1.0f >= ringCount) return missing;
  float values[WINDOW_SAMPLES], delimiter[6];
  for (uint16_t i = 0; i < WINDOW_SAMPLES; ++i)
    values[i] = ringSampleAgo(endPhase + FRAME_BITS * samplesPerBit - (i + 0.5f) * step);
  for (uint8_t i = 0; i < 6; ++i)
    delimiter[i] = ringSampleAgo(endPhase + FRAME_BITS * samplesPerBit + (i + 0.5f) * step);
  return checkFrameEvidence(values, delimiter);
}

static uint64_t decodeClassicAtRate(float samplesPerBit, float endPhase) {
  uint64_t frame = 0;
  const float sampleStep = samplesPerBit / FRAMES_PER_BIT;
  for (uint8_t bit = 0; bit < FRAME_BITS; ++bit) {
    float firstHalf = 0.0f, secondHalf = 0.0f;
    for (uint8_t j = 0; j < FRAMES_PER_BIT; ++j) {
      // Read the candidate frame backwards from the newest ring sample. The
      // quarter-sample phase bank and interpolation tolerate independent TX
      // and RX oscillator drift without changing the model's 20 Hz grid.
      const float age = endPhase + (FRAME_BITS - bit) * samplesPerBit -
                        (j + 0.5f) * sampleStep;
      const float value = ringSampleAgo(age);
      if (j < FRAMES_PER_BIT / 2) firstHalf += value;
      else secondHalf += value;
    }
    frame = (frame << 1) | (firstHalf > secondHalf ? 1ULL : 0ULL);
  }
  return frame;
}

static bool runNpuInferenceAtRate(float samplesPerBit, float endPhase, uint64_t classicRefFrame,
                                const FrameEvidence &evidence) {
  if (!npuReady) return false;
  int8_t *input = static_cast<int8_t *>(vip_map_buffer(inputBuffer));
  if (!input) return false;

  const float sampleStep = samplesPerBit / FRAMES_PER_BIT;
  for (uint8_t bit = 0; bit < FRAME_BITS; ++bit) {
    float lo = 1e30f, hi = -1e30f;
    float values[FRAMES_PER_BIT];
    for (uint8_t j = 0; j < FRAMES_PER_BIT; ++j) {
      const float age = endPhase + (FRAME_BITS - bit) * samplesPerBit -
                        (j + 0.5f) * sampleStep;
      const float val = ringSampleAgo(age);
      values[j] = val;
      if (val < lo) lo = val;
      if (val > hi) hi = val;
    }
    const float amplitude = hi - lo;
    for (uint8_t j = 0; j < FRAMES_PER_BIT; ++j) {
      float normalized = amplitude > 0.5f ? (values[j] - lo) / amplitude : 0.0f;
      int32_t q = (int32_t)(normalized * 255.0f + 0.5f) - 128;
      if (q < -128) q = -128;
      if (q > 127) q = 127;
      input[bit * FRAMES_PER_BIT + j] = (int8_t)q;
    }
  }

  const bool logRaw = shouldLogNpuRaw("SYNC");
  if (logRaw) memcpy(npuRawInputSnapshot, input, WINDOW_SAMPLES);
  dcache_clean_by_addr(reinterpret_cast<uint32_t *>(input), WINDOW_SAMPLES);
  if (!check(vip_unmap_buffer(inputBuffer), "unmap input")) return false;
  const uint32_t startNpuUs = micros();
  if (!check(vip_run_network(network), "inference")) return false;
  const uint32_t npuLatencyUs = micros() - startNpuUs;
  if (edgeMode != EDGE_NORMAL) edgeNpu.add(npuLatencyUs);
  if (!check(vip_flush_buffer(outputBuffer, VIP_BUFFER_OPER_TYPE_INVALIDATE), "invalidate output")) return false;
  if (logRaw) emitNpuRaw("SYNC", samplesPerBit, endPhase, npuLatencyUs, outputData);

  uint64_t npuFrame = 0;
  for (uint8_t i = 0; i < FRAME_BITS; ++i) {
    npuFrame = (npuFrame << 1) | (outputData[i] >= OUTPUT_ONE_THRESHOLD ? 1ULL : 0ULL);
  }
  lastNpuFrame = npuFrame;

  const uint64_t mask48 = (1ULL << FRAME_BITS) - 1ULL;
  const uint8_t directErrors = __builtin_popcountll(npuFrame ^ classicRefFrame);
  const uint8_t inverseErrors = __builtin_popcountll(((~npuFrame) & mask48) ^ classicRefFrame);
  static uint32_t lastTimingLogMs = 0;
  if (edgeMode == EDGE_NORMAL && millis() - lastTimingLogMs >= 2000UL) {
    lastTimingLogMs = millis();
    Serial.print("[NPU_TIMING] us="); Serial.println(npuLatencyUs);
    Serial.print("[NPU_SYNC] direct_err="); Serial.print(directErrors);
    Serial.print(" inv_err="); Serial.println(inverseErrors);
  }

  // If direct NPU output validates, publish NPU!
  if (validateAndPublish(npuFrame, "NPU", evidence)) return true;
  // If inverted NPU output validates, publish NPU_INV!
  if (validateAndPublish((~npuFrame) & mask48, "NPU_INV", evidence)) return true;

  return false;
}

static bool decodeClassicAndPublish(uint16_t oldest) {
  (void)oldest;
  // A 5 bit/s symbol nominally spans 4.0 samples at 20 Hz. Search +/-2.5%
  // because the two battery-powered MCUs do not share a clock. The dump from
  // the diagnostic TX measured about 4.01 samples/bit.
  static const float rateBank[] = {
      3.90f, 3.925f, 3.95f, 3.975f, 4.00f,
      4.025f, 4.05f, 4.075f, 4.10f};
  static const float phaseBank[] = {0.0f, 0.25f, 0.50f, 0.75f};
  const uint64_t mask48 = (1ULL << FRAME_BITS) - 1ULL;

  for (float samplesPerBit : rateBank) {
    for (float endPhase : phaseBank) {
      const uint64_t frame = decodeClassicAtRate(samplesPerBit, endPhase);
      const uint64_t payload = frame >> 8;
      const uint8_t receivedCrc = frame & 0xFFU;

      // 1. Check direct polarity candidate
      if (((payload >> 36) & 0x0FU) == 0x0AU && crc8(payload, 40) == receivedCrc) {
        const FrameEvidence evidence = candidateEvidence(samplesPerBit, endPhase);
        if (!evidence.valid) { ++framingRejects; continue; }
        // Run NPU with the synchronized phase (Primary AI Decoder)
        if (edgeMode != EDGE_CLASSIC && runNpuInferenceAtRate(samplesPerBit, endPhase, frame, evidence)) {
          return true;
        }
        // Fallback to Classic PLL
        if (validateAndPublish(frame, "CLASSIC_PLL", evidence)) {
          logNpuAgreement(frame);
          return true;
        }
      }

      // 2. Check inverted polarity candidate
      const uint64_t inverseFrame = (~frame) & mask48;
      const uint64_t invPayload = inverseFrame >> 8;
      const uint8_t invReceivedCrc = inverseFrame & 0xFFU;
      if (((invPayload >> 36) & 0x0FU) == 0x0AU && crc8(invPayload, 40) == invReceivedCrc) {
        const FrameEvidence evidence = candidateEvidence(samplesPerBit, endPhase);
        if (!evidence.valid) { ++framingRejects; continue; }
        // Run NPU with the synchronized phase (Primary AI Decoder)
        if (edgeMode != EDGE_CLASSIC && runNpuInferenceAtRate(samplesPerBit, endPhase, inverseFrame, evidence)) {
          return true;
        }
        // Fallback to Classic PLL
        if (validateAndPublish(inverseFrame, "CLASSIC_PLL_INV", evidence)) {
          logNpuAgreement(inverseFrame);
          return true;
        }
      }
    }
  }

  ++classicCrcRejects;
  return false;
}

static void runSlidingInference() {
  if (captureOnly) return;
  if (ringCount < WINDOW_SAMPLES) return;
  if (edgeMode == EDGE_CLASSIC) {
    decodeClassicAndPublish((ringHead + RING_SAMPLES - WINDOW_SAMPLES) % RING_SAMPLES);
    return;
  }
  if (!npuReady) return;
  int8_t *input = static_cast<int8_t *>(vip_map_buffer(inputBuffer));
  if (!input) return;
  const uint16_t oldest = (ringHead + RING_SAMPLES - WINDOW_SAMPLES) % RING_SAMPLES;
  for (uint8_t bit = 0; bit < FRAME_BITS; ++bit) {
    float lo = 1e30f, hi = -1e30f;
    float values[FRAMES_PER_BIT];
    for (uint8_t j = 0; j < FRAMES_PER_BIT; ++j) {
      const uint16_t k = (oldest + bit * FRAMES_PER_BIT + j) % RING_SAMPLES;
      values[j] = signalRing[k];
      if (values[j] < lo) lo = values[j];
      if (values[j] > hi) hi = values[j];
    }
    const float amplitude = hi - lo;
    for (uint8_t j = 0; j < FRAMES_PER_BIT; ++j) {
      float normalized = amplitude > 0.5f ? (values[j] - lo) / amplitude : 0.0f;
      int32_t q = (int32_t)(normalized * 255.0f + 0.5f) - 128;
      if (q < -128) q = -128;
      if (q > 127) q = 127;
      input[bit * FRAMES_PER_BIT + j] = (int8_t)q;
    }
  }
  const bool logRaw = shouldLogNpuRaw("SLIDE");
  if (logRaw) memcpy(npuRawInputSnapshot, input, WINDOW_SAMPLES);
  dcache_clean_by_addr(reinterpret_cast<uint32_t *>(input), WINDOW_SAMPLES);
  if (!check(vip_unmap_buffer(inputBuffer), "unmap input")) return;
  if (!check(vip_set_input(network, 0, inputBuffer), "set input")) return;
  const uint32_t startNpuUs = micros();
  if (!check(vip_run_network(network), "inference")) return;
  const uint32_t npuLatencyUs = micros() - startNpuUs;
  if (edgeMode != EDGE_NORMAL) edgeNpu.add(npuLatencyUs);
  if (!check(vip_flush_buffer(outputBuffer, VIP_BUFFER_OPER_TYPE_INVALIDATE), "invalidate output")) return;
  if (logRaw) emitNpuRaw("SLIDE", 4.0f, 0.0f, npuLatencyUs, outputData);
  // Always run the deterministic decoder as well. A CRC-valid but unrelated
  // NPU sliding-window candidate must never suppress a real classic frame.
  // The contiguous sliding input uses ages 191..0, half a sample newer
  // than the rate-search convention's 191.5..0.5.
  const FrameEvidence evidence = candidateEvidence(4.0f, -0.5f);
  decodeNpuAndPublish(evidence);
  decodeClassicAndPublish(oldest);
}

static void primeTracking() {
  trackLastOnMs = millis();
  trackWasLost = false;
  memset(trackHistory, 0, sizeof(trackHistory));
}

static bool jpegPixelFormatError = false;
static void clearFrameStats() {
  jpegPixelFormatError = false;
  memset(gridSum, 0, sizeof(gridSum));
  memset(gridCount, 0, sizeof(gridCount));
  roiSum = roiCount = 0;
  trackFrameUs = 0;
  trackFrameActive = trackEnabled && roiLocked;
  if (trackFrameActive) {
    const uint32_t start = micros();
    memset(&trackWindow, 0, sizeof(trackWindow));
    memset(trackCells, 0, sizeof(trackCells));
    trackLeft = max(0, (int)roiPixelX - TRACK_MARGIN_PX);
    trackTop = max(0, (int)roiPixelY - TRACK_MARGIN_PX);
    trackRight = min((int)CAMERA_W, roiPixelX + roiPixelW + TRACK_MARGIN_PX);
    trackBottom = min((int)CAMERA_H, roiPixelY + roiPixelH + TRACK_MARGIN_PX);
    trackFrameUs += micros() - start;
  }
}

// One extra grayscale pass, no per-pixel clock calls. Full-frame bright cell
// statistics keep a recent modulation history even before the LED is lost.
static void trackBlock(JPEGDRAW *block) {
  const uint32_t start = micros();
  const uint8_t *pixels = reinterpret_cast<const uint8_t *>(block->pPixels);
  for (int yy = 0; yy < block->iHeight; ++yy) {
    const int y = block->y + yy;
    if (y < 0 || y >= CAMERA_H) continue;
    const int gy = y / CELL_H;
    const bool inY = y >= trackTop && y < trackBottom;
    for (int xx = 0; xx < block->iWidthUsed; ++xx) {
      const int x = block->x + xx;
      if (x < 0 || x >= CAMERA_W ||
          pixels[yy * block->iWidth + xx] < TRACK_BRIGHT_MIN) continue;
      trackCells[gy][x / CELL_W].add(x, y);
      if (inY && x >= trackLeft && x < trackRight) trackWindow.add(x, y);
    }
  }
  trackFrameUs += micros() - start;
}

static int jpegBlockCallback(JPEGDRAW *block) {
  if (block->iBpp != 8) { jpegPixelFormatError = true; return 0; }
  if (trackFrameActive) trackBlock(block);
  const uint8_t *pixels = reinterpret_cast<const uint8_t *>(block->pPixels);
  for (int yy = 0; yy < block->iHeight; ++yy) {
    const int y = block->y + yy;
    if (y < 0 || y >= CAMERA_H) continue;
    for (int xx = 0; xx < block->iWidthUsed; ++xx) {
      const int x = block->x + xx;
      if (x < 0 || x >= CAMERA_W) continue;
      const uint8_t luma = pixels[yy * block->iWidth + xx];
      const uint8_t gx = min((int)GRID_COLS - 1, x / (int)CELL_W);
      const uint8_t gy = min((int)GRID_ROWS - 1, y / (int)CELL_H);
      gridSum[gy][gx] += luma;
      ++gridCount[gy][gx];
      if (roiLocked && x >= roiPixelX && y >= roiPixelY &&
          x < roiPixelX + roiPixelW && y < roiPixelY + roiPixelH) {
        roiSum += luma;
        ++roiCount;
      }
    }
  }
  return 1;
}

static void updateAutoRoi() {
  for (uint8_t y = 0; y < GRID_ROWS; ++y) {
    for (uint8_t x = 0; x < GRID_COLS; ++x) {
      if (!gridCount[y][x]) continue;
      const float mean = (float)gridSum[y][x] / gridCount[y][x];
      const float delta = fabsf(mean - gridPrevious[y][x]);
      gridActivity[y][x] = 0.92f * gridActivity[y][x] + 0.08f * delta;
      gridPrevious[y][x] = mean;
    }
  }

  if (capturedFrames >= ROI_WARMUP_FRAMES && !roiLocked) {
    float sumActivity = 0.0f, sumActivity2 = 0.0f;
    uint16_t activityCount = 0;
    for (uint8_t y = 0; y < GRID_ROWS; ++y) {
      for (uint8_t x = 0; x < GRID_COLS; ++x) {
        if (!gridCount[y][x]) continue;
        sumActivity += gridActivity[y][x];
        sumActivity2 += gridActivity[y][x] * gridActivity[y][x];
        ++activityCount;
      }
    }
    const float activityMean = activityCount ? sumActivity / activityCount : 0.0f;
    float activityVariance = activityCount
        ? sumActivity2 / activityCount - activityMean * activityMean : 0.0f;
    if (activityVariance < 0.0f) activityVariance = 0.0f;
    const float adaptiveThreshold = max(MIN_AUTO_ROI_ACTIVITY,
                                        activityMean + 2.0f * sqrtf(activityVariance));

    float bestScore = -1.0f;
    uint8_t bestX = 0, bestY = 0;
    for (uint8_t y = 0; y < GRID_ROWS; ++y) {
      for (uint8_t x = 0; x < GRID_COLS; ++x) {
        if (roiTried[y][x] || !gridCount[y][x]) continue;
        // Select temporal modulation, not the brightest object in the image.
        if (gridActivity[y][x] < adaptiveThreshold) continue;
        const float score = gridActivity[y][x];
        if (score > bestScore) {
          bestScore = score;
          bestX = x;
          bestY = y;
        }
      }
    }

    if (bestScore < 0.0f) {
      static uint32_t lastScanLogMs = 0;
      if (millis() - lastScanLogMs >= 3000) {
        lastScanLogMs = millis();
        float maxAct = 0.0f;
        for (uint8_t y = 0; y < GRID_ROWS; ++y) {
          for (uint8_t x = 0; x < GRID_COLS; ++x) {
            if (gridActivity[y][x] > maxAct) maxAct = gridActivity[y][x];
          }
        }
        Serial.print("[AUTO_ROI] Scanning frame for VLC optical signal... max_activity=");
        Serial.print(maxAct, 2);
        Serial.print(" (adaptive_thresh="); Serial.print(adaptiveThreshold, 1); Serial.println(")");
        memset(roiTried, 0, sizeof(roiTried));
      }
      return;
    }

    // Grow a connected component around the strongest temporally-modulated
    // cell. Its bounding box expands for a nearby/defocused lamp and contracts
    // for a distant point source, so ROI dimensions are not fixed in pixels.
    bool visited[GRID_ROWS][GRID_COLS] = {};
    uint8_t queueX[GRID_ROWS * GRID_COLS];
    uint8_t queueY[GRID_ROWS * GRID_COLS];
    uint16_t queueRead = 0, queueWrite = 0;
    queueX[queueWrite] = bestX; queueY[queueWrite++] = bestY;
    visited[bestY][bestX] = true;
    uint8_t minCellX = bestX, maxCellX = bestX;
    uint8_t minCellY = bestY, maxCellY = bestY;
    const float componentThreshold = max(adaptiveThreshold, bestScore * 0.30f);
    while (queueRead < queueWrite) {
      const int cx = queueX[queueRead];
      const int cy = queueY[queueRead++];
      minCellX = min(minCellX, (uint8_t)cx); maxCellX = max(maxCellX, (uint8_t)cx);
      minCellY = min(minCellY, (uint8_t)cy); maxCellY = max(maxCellY, (uint8_t)cy);
      for (int dy = -1; dy <= 1; ++dy) {
        for (int dx = -1; dx <= 1; ++dx) {
          if (dx == 0 && dy == 0) continue;
          const int nx = cx + dx, ny = cy + dy;
          if (nx < 0 || nx >= GRID_COLS || ny < 0 || ny >= GRID_ROWS ||
              visited[ny][nx] || gridActivity[ny][nx] < componentThreshold) continue;
          visited[ny][nx] = true;
          queueX[queueWrite] = (uint8_t)nx;
          queueY[queueWrite++] = (uint8_t)ny;
        }
      }
    }

    roiCellX = bestX;
    roiCellY = bestY;
    roiLocked = true;
    roiTried[bestY][bestX] = true;
    roiLockedAtMs = millis();
    primeTracking();
    lastValidPacketMs = 0;
    roiLowActivitySinceMs = 0;
    lockedRoiActivity = gridActivity[bestY][bestX];
    lockedRoiMean = gridPrevious[bestY][bestX];

    int left = minCellX * CELL_W - AUTO_ROI_MARGIN;
    int top = minCellY * CELL_H - AUTO_ROI_MARGIN;
    int right = (maxCellX + 1) * CELL_W + AUTO_ROI_MARGIN;
    int bottom = (maxCellY + 1) * CELL_H + AUTO_ROI_MARGIN;
    left = max(0, left); top = max(0, top);
    right = min((int)CAMERA_W, right); bottom = min((int)CAMERA_H, bottom);
    int width = constrain(right - left, (int)MIN_AUTO_ROI_SIZE, (int)MAX_AUTO_ROI_SIZE);
    int height = constrain(bottom - top, (int)MIN_AUTO_ROI_SIZE, (int)MAX_AUTO_ROI_SIZE);
    if (left + width > CAMERA_W) left = CAMERA_W - width;
    if (top + height > CAMERA_H) top = CAMERA_H - height;
    roiPixelX = left; roiPixelY = top; roiPixelW = width; roiPixelH = height;

    resetTemporalDecoder();
    Serial.print("[AUTO_ROI] Locked LED at cell=("); Serial.print(bestX);
    Serial.print(","); Serial.print(bestY);
    Serial.print(") centered px=("); Serial.print(roiPixelX);
    Serial.print(","); Serial.print(roiPixelY);
    Serial.print(","); Serial.print(roiPixelW);
    Serial.print(","); Serial.print(roiPixelH);
    Serial.print(") component_cells="); Serial.print(queueWrite);
    Serial.print(" activity="); Serial.println(lockedRoiActivity, 2);
  }

  if (roiLocked) {
    lockedRoiActivity = gridActivity[roiCellY][roiCellX];
    lockedRoiMean = gridPrevious[roiCellY][roiCellX];
    const uint32_t now = millis();
    if (lockedRoiActivity < 1.5f) {
      if (!roiLowActivitySinceMs) roiLowActivitySinceMs = now;
    } else {
      roiLowActivitySinceMs = 0;
    }
    const bool activityLost = roiLowActivitySinceMs &&
        now - roiLowActivitySinceMs >= ROI_LOW_ACTIVITY_HOLD_MS;
    const uint32_t referenceMs = lastValidPacketMs ? lastValidPacketMs : roiLockedAtMs;
    const bool packetTimeout = now - referenceMs >= ROI_SEARCH_TIMEOUT_MS;
    if (!trackEnabled && !roiManuallyConfirmed && (activityLost || packetTimeout)) {
      Serial.print("[AUTO_ROI] VLC track lost or no valid packet within timeout (cell ");
      Serial.print(roiCellX); Serial.print(","); Serial.print(roiCellY);
      Serial.println("). Releasing lock to re-scan frame...");
      roiLocked = false;
      roiLowActivitySinceMs = 0;
      resetTemporalDecoder();
    }
  }
}

static void moveTrackRoi(const TrackBright &b, bool limit, int &dx, int &dy) {
  int x, y;
  trackPosition(b.x(), b.y(), roiPixelW, roiPixelH, CAMERA_W, CAMERA_H,
                roiPixelX, roiPixelY, limit, x, y);
  dx = x - roiPixelX; dy = y - roiPixelY;
  roiPixelX = x; roiPixelY = y;
  roiCellX = min((int)GRID_COLS - 1, (x + roiPixelW / 2) / CELL_W);
  roiCellY = min((int)GRID_ROWS - 1, (y + roiPixelH / 2) / CELL_H);
  if (dx || dy) ++trackMoves;
  // A move deliberately preserves the temporal decoder, including fast reacquire.
}

static void updateTracking() {
  if (!trackFrameActive || !roiLocked) return;
  const uint32_t start = micros(), now = millis();
  for (int y = 0; y < GRID_ROWS; ++y) {
    for (int x = 0; x < GRID_COLS; ++x) {
      if (gridCount[y][x]) trackHistory[y][x].observe(trackCells[y][x].count, now);
    }
  }
  int dx = 0, dy = 0;
  const char *state = "off";
  uint32_t bright = trackWindow.count;
  if (bright >= TRACK_MIN_PIXELS) {
    trackLastOnMs = now;
    trackWasLost = false;
    moveTrackRoi(trackWindow, true, dx, dy);
    state = "on";
  } else if (trackExpired(now, trackLastOnMs, TRACK_LOST_MS)) {
    if (!trackWasLost) {
      trackWasLost = true; trackLostSinceMs = now; ++trackLost;
    }
    state = "lost";
    TrackBright blob;
    if (trackFindBlob(trackCells, trackHistory, CELL_W, CELL_H, now, blob)) {
      moveTrackRoi(blob, false, dx, dy);
      bright = blob.count;
      trackLastOnMs = now; trackWasLost = false; ++trackReacq;
      state = "reacquired";
    } else if (trackExpired(now, trackLostSinceMs, ROI_SEARCH_TIMEOUT_MS)) {
      roiLocked = false;
      roiManuallyConfirmed = false;
      roiLowActivitySinceMs = 0;
      memset(roiTried, 0, sizeof(roiTried));
      resetTemporalDecoder();
      state = "released";
    }
  }
  trackFrameUs += micros() - start;
  edgeTrack.add(trackFrameUs);
  // Serial time excluded; benchmark logging disabled in PERF modes.
  if (edgeMode == EDGE_NORMAL && now - trackLogMs >= 500UL) {
    trackLogMs = now;
    Serial.print("[TRACK] dx="); Serial.print(dx);
    Serial.print(" dy="); Serial.print(dy);
    Serial.print(" bright="); Serial.print(bright);
    Serial.print(" state="); Serial.print(state);
    Serial.print(" cost_us="); Serial.println(trackFrameUs);
  }
}

static void lockManualRoi(uint8_t x, uint8_t y) {
  if (x >= GRID_COLS || y >= GRID_ROWS) return;
  roiCellX = x;
  roiCellY = y;
  roiPixelX = x * CELL_W;
  roiPixelY = y * CELL_H;
  roiPixelW = CELL_W;
  roiPixelH = CELL_H;
  roiLocked = true;
  roiManuallyConfirmed = true;
  roiLockedAtMs = millis();
  primeTracking();
  lastValidPacketMs = 0;
  resetTemporalDecoder();
  Serial.print("[ROI_CONFIRMED] x="); Serial.print(roiCellX);
  Serial.print(" y="); Serial.println(roiCellY);
}

static void lockManualPixelRoi(uint16_t x, uint16_t y,
                               uint16_t w, uint16_t h) {
  if (x >= CAMERA_W || y >= CAMERA_H || w == 0 || h == 0) return;
  if (x + w > CAMERA_W) w = CAMERA_W - x;
  if (y + h > CAMERA_H) h = CAMERA_H - y;
  roiPixelX = x; roiPixelY = y; roiPixelW = w; roiPixelH = h;
  roiCellX = min((int)GRID_COLS - 1, x / (int)CELL_W);
  roiCellY = min((int)GRID_ROWS - 1, y / (int)CELL_H);
  roiLocked = true;
  roiManuallyConfirmed = true;
  roiLockedAtMs = millis();
  primeTracking();
  lastValidPacketMs = 0;
  resetTemporalDecoder();
  Serial.print("[ROI_CONFIRMED_PX] x="); Serial.print(roiPixelX);
  Serial.print(" y="); Serial.print(roiPixelY);
  Serial.print(" w="); Serial.print(roiPixelW);
  Serial.print(" h="); Serial.println(roiPixelH);
}

static void dumpSignalRing() {
  Serial.print("[SIGNAL_BEGIN] "); Serial.print(ringCount);
  Serial.print(" "); Serial.println(MODEL_SAMPLE_HZ);
  const uint16_t oldest = (ringHead + RING_SAMPLES - ringCount) % RING_SAMPLES;
  for (uint16_t i = 0; i < ringCount; ++i) {
    if (i) Serial.print(',');
    Serial.print(signalRing[(oldest + i) % RING_SAMPLES], 3);
  }
  Serial.println();
  Serial.println("[SIGNAL_END]");
  Serial.flush();
}

static void processSerialCommand() {
  static char command[40];
  static uint8_t length = 0;
  while (Serial.available()) {
    const char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c != '\n' && length < sizeof(command) - 1) {
      // Ignore terminal-control bytes sometimes emitted when a Linux serial
      // client opens the port. Commands themselves are printable ASCII.
      if (c >= 32 && c <= 126) command[length++] = c;
      continue;
    }
    command[length] = '\0';
    char *start = command;
    while (*start == ' ' || *start == '\t') ++start;
    Serial.print("[CMD] "); Serial.println(start);
    if (strcmp(start, "TRACK ON") == 0 || strcmp(start, "TRACK OFF") == 0) {
      trackEnabled = strcmp(start, "TRACK ON") == 0;
      primeTracking();
      Serial.print("[TRACK_CMD] track="); Serial.println(trackEnabled ? "on" : "off");
    } else if (strncmp(start, "PERF ", 5) == 0) {
      if (strcmp(start, "PERF REPORT") == 0) {
        reportEdgePerf();
      } else if (strcmp(start, "PERF RESET") == 0) {
        resetEdgePerf();
        Serial.println("[PERF] RESET");
      } else if (strcmp(start, "PERF OFF") == 0) {
        edgeMode = EDGE_NORMAL; captureOnly = false;
        Serial.println("[PERF] OFF");
      } else if (strcmp(start, "PERF CAMERA") == 0 ||
                 strcmp(start, "PERF CLASSIC") == 0 ||
                 strcmp(start, "PERF HYBRID") == 0) {
        if (strcmp(start, "PERF HYBRID") == 0 && !npuReady) {
          Serial.println("[PERF] ERROR NPU unavailable");
        } else {
          edgeMode = strcmp(start, "PERF CAMERA") == 0 ? EDGE_CAMERA :
                     strcmp(start, "PERF CLASSIC") == 0 ? EDGE_CLASSIC : EDGE_HYBRID;
          captureOnly = edgeMode == EDGE_CAMERA;
          benchmarkLoggerEnabled = npuRawLogEnabled = false;
          resetTemporalDecoder(); resetEdgePerf();
          Serial.print("[PERF] MODE "); Serial.println(edgeModeName());
        }
      } else Serial.println("[PERF] ERROR unknown command");
    } else if (strstr(start, "SNAP") != NULL) {
      snapshotRequested = true;
      Serial.println("[SNAPSHOT] Request accepted; capturing next JPEG.");
    } else if (strstr(start, "DUMP") != NULL) {
      dumpSignalRing();
    } else if (strstr(start, "LOGGER ON") != NULL) {
      benchmarkLoggerEnabled = true;
      Serial.println("[LOGGER] ON");
    } else if (strstr(start, "LOGGER OFF") != NULL) {
      benchmarkLoggerEnabled = false;
      Serial.println("[LOGGER] OFF");
    } else if (strcmp(start, "NPU_RAW ON") == 0) {
      npuRawLogEnabled = true;
      npuRawSlideCounter = npuRawSyncCounter = 0;
      Serial.print("[NPU_RAW] ON stride="); Serial.print(NPU_RAW_LOG_STRIDE);
      Serial.println(" format=hex-int8 (input,output)");
    } else if (strcmp(start, "NPU_RAW OFF") == 0) {
      npuRawLogEnabled = false;
      Serial.println("[NPU_RAW] OFF");
    } else if (strcmp(start, "DECODE OFF") == 0) {
      captureOnly = true;
      Serial.println("[DECODE] OFF - capture-only, no telemetry acceptance");
    } else if (strcmp(start, "DECODE ON") == 0) {
      captureOnly = false;
      Serial.println("[DECODE] ON");
    } else if (strncmp(start, "PATIENT ", 8) == 0) {
      unsigned int patientFilter = 0;
      if (sscanf(start, "PATIENT %u", &patientFilter) == 1 && patientFilter <= 15) {
        allowedPatient = (uint8_t)patientFilter;
        Serial.print("[PATIENT] "); Serial.println(allowedPatient);
      }
    } else if (strstr(start, "COUNTERS RESET") != NULL) {
      validPackets = crcRejects = classicCrcRejects = 0;
      plausibleCandidates = physiologyRejects = 0;
      preamblePasses = crcPasses = 0;
      framingRejects = duplicateSuppressions = 0;
      recentFrames.reset();
      lastPatient = lastSequence = 0xFF;
      lastPacketMs = lastValidPacketMs = 0;
      lastPublishedFrame = 0;
      Serial.println("[COUNTERS] RESET");
    } else if (strstr(start, "CONFIRM") != NULL) {
      if (roiLocked) {
        if (trackEnabled) lockManualPixelRoi(roiPixelX, roiPixelY, roiPixelW, roiPixelH);
        else lockManualRoi(roiCellX, roiCellY);
      }
    } else if (strstr(start, "AUTO") != NULL) {
      roiLocked = false;
      roiManuallyConfirmed = false;
      memset(roiTried, 0, sizeof(roiTried));
      resetTemporalDecoder();
      Serial.println("[ROI] Switched to Auto-Detect mode. Scanning for VLC LED...");
    } else {
      unsigned int x = 0, y = 0, w = 0, h = 0;
      char *pixelStart = strstr(start, "ROI_PX ");
      char *roiStart = strstr(start, "ROI ");
      if (pixelStart != NULL &&
          sscanf(pixelStart, "ROI_PX %u %u %u %u", &x, &y, &w, &h) == 4 &&
          x < CAMERA_W && y < CAMERA_H && w > 0 && h > 0) {
        lockManualPixelRoi(x, y, w, h);
      } else if (roiStart != NULL && sscanf(roiStart, "ROI %u %u", &x, &y) == 2 &&
          x < GRID_COLS && y < GRID_ROWS) {
        lockManualRoi((uint8_t)x, (uint8_t)y);
      } else if (*start) {
        Serial.println("[HELP] TRACK ON|OFF | SNAP | DUMP | LOGGER ON|OFF | NPU_RAW ON|OFF | COUNTERS RESET | CONFIRM | AUTO | ROI_PX <x> <y> <w> <h>");
      }
    }
    length = 0;
  }
}

static void sendSnapshot(uint32_t address, uint32_t length) {
  Serial.print("[SNAPSHOT_BEGIN] "); Serial.print(length);
  Serial.print(" "); Serial.print(roiPixelX);
  Serial.print(" "); Serial.print(roiPixelY);
  Serial.print(" "); Serial.print(roiPixelW);
  Serial.print(" "); Serial.println(roiPixelH);
  Serial.write(reinterpret_cast<const uint8_t *>(address), length);
  Serial.println();
  Serial.println("[SNAPSHOT_END]");
  Serial.flush();
}

static void pushSignal(float value, uint32_t sampleUs) {
  if (edgeMode != EDGE_NORMAL) ++edgeSamples;
  lastUniformSampleIndex = uniformSampleCounter++;
  lastUniformSampleUs = sampleUs;
  lastSignal = value;
  if (value < signalMinimum) signalMinimum = value;
  if (value > signalMaximum) signalMaximum = value;
  signalRing[ringHead] = value;
  ringHead = (ringHead + 1) % RING_SAMPLES;
  if (ringCount < RING_SAMPLES) ++ringCount;
  if (benchmarkLoggerEnabled) {
    char line[192];
    snprintf(line, sizeof(line), "[RX_SAMPLE] rx_us=%lu signal=%.4f roi=%u,%u,%u,%u activity=%.3f mean=%.3f locked=%u\n",
             (unsigned long)sampleUs, value, roiPixelX, roiPixelY, roiPixelW, roiPixelH,
             lockedRoiActivity, lockedRoiMean, roiLocked ? 1U : 0U);
    Serial.print(line);
  }
}

static void timedSlidingInference() {
  if (edgeMode == EDGE_NORMAL) { runSlidingInference(); return; }
  if (captureOnly || ringCount < WINDOW_SAMPLES) return;
  const uint32_t startUs = micros();
  runSlidingInference();
  edgeDecode.add(micros() - startUs);
}

static void resampleAndDecode(float rawSignal, uint32_t nowUs) {
  if (!resamplerPrimed) {
    resamplerPrimed = true;
    previousRawUs = nowUs;
    nextUniformUs = nowUs;
    previousRawSignal = rawSignal;
    pushSignal(rawSignal, nowUs);
    timedSlidingInference();
    nextUniformUs += MODEL_SAMPLE_PERIOD_US;
    return;
  }

  const uint32_t spanUs = nowUs - previousRawUs;
  if (spanUs == 0) return;

  // Produce every missing point on the uniform 20 Hz timeline. Linear
  // interpolation preserves Manchester edges better than duplicating frames.
  while ((int32_t)(nowUs - nextUniformUs) >= 0) {
    float fraction = (float)(nextUniformUs - previousRawUs) / (float)spanUs;
    if (fraction < 0.0f) fraction = 0.0f;
    if (fraction > 1.0f) fraction = 1.0f;
    const float uniformSignal = previousRawSignal +
                                fraction * (rawSignal - previousRawSignal);
    pushSignal(uniformSignal, nextUniformUs);
    timedSlidingInference();
    nextUniformUs += MODEL_SAMPLE_PERIOD_US;
  }
  previousRawUs = nowUs;
  previousRawSignal = rawSignal;
}

static void applyCameraPreset() {
  cameraControl.setWDR(0);
  cameraControl.setExposureMode(0);
  cameraControl.setExposureTime(EXPOSURE_US);
  cameraControl.setAEGain(AE_GAIN);
  cameraControl.setAWB(0);
  cameraControl.setRedBalance(RED_BALANCE);
  cameraControl.setBlueBalance(BLUE_BALANCE);
}

void setup() {
  Serial.begin(115200);
  delay(1200);
  Serial.println("\n[VLC RX] Camera + edge CNN NPU receiver v1");
  Serial.println("[INFO] 320x192 JPEG request 30 fps; resample to 20 Hz");
  Serial.println("[BUILD] edge-perf-v2-framed-track-v1 AUG-v2 nbg_sha256=c3f0eec20652e9e786904bb5bd0281e87903eeb0fae19d9622181431627fc097 tflite_sha256=ef9076f8f5c08cf17ded2e5b5374f92d40b814ab0ad084462a7fe1eaa9e373ca");
  Serial.println("[FRAME_GUARD] delimiter-manchester-v1 recent_cache=4_per_sender ttl_ms=15000");

  Camera.configVideoChannel(VLC_CHANNEL, videoConfig);
  Camera.videoInit(VLC_CHANNEL);
  Camera.channelBegin(VLC_CHANNEL);
  delay(200);
  applyCameraPreset();

  // NPU initialization must follow video-domain initialization on AMB82.
  npuReady = beginNpu();
  // Output pixel format is selected after every openFLASH(), not here.
  Serial.println("[READY] Aim the TX at the camera; ROI locks after about 3 s.");
  Serial.println("[READY] Run roi_confirm.py to verify or replace the ROI.");
  captureStartMs = millis();
}

void loop() {
  processSerialCommand();
  uint32_t imageAddress = 0, imageLength = 0;
  clearFrameStats();
  Camera.getImage(VLC_CHANNEL, &imageAddress, &imageLength);
  if (!imageAddress || !imageLength) return;
  if (snapshotRequested) {
    snapshotRequested = false;
    sendSnapshot(imageAddress, imageLength);
  }
  if (!jpeg.openFLASH(reinterpret_cast<uint8_t *>(imageAddress), imageLength, jpegBlockCallback)) {
    if (edgeMode != EDGE_NORMAL) ++edgeErrors;
    Serial.println("[WARN] JPEG open failed"); return;
  }
  // JPEGDEC::decode() returns 1 on success and 0 on failure. The JPEG_SUCCESS
  // enum belongs to getLastError() and must not be used as decode's return
  // value (JPEG_SUCCESS is numerically zero).
  jpeg.setPixelType(EIGHT_BIT_GRAYSCALE);
  const int result = jpeg.decode(0, 0, JPEG_LUMA_ONLY);
  const int jpegError = jpeg.getLastError();
  jpeg.close();
  if (!result || jpegPixelFormatError) {
    if (edgeMode != EDGE_NORMAL) ++edgeErrors;
    Serial.print("[WARN] JPEG decode failed, error=");
    Serial.println(jpegError);
    return;
  }

  ++capturedFrames;
  const uint32_t rawSampleUs = micros();
  if (edgeMode != EDGE_NORMAL) {
    ++edgeFrames;
    if (edgePreviousValid) edgeIntervals.add(rawSampleUs - edgePreviousUs);
    edgePreviousUs = rawSampleUs; edgePreviousValid = true;
  }
  updateAutoRoi();
  if (benchmarkLoggerEnabled) {
    char line[112];
    snprintf(line, sizeof(line), "[RX_RAW] rx_us=%lu frame=%lu signal=%.4f locked=%u\n",
             (unsigned long)rawSampleUs, (unsigned long)capturedFrames,
             roiLocked && roiCount ? (float)roiSum / roiCount : 0.0f,
             roiLocked && roiCount ? 1U : 0U);
    Serial.print(line);
  }
  if (roiLocked && roiCount) {
    resampleAndDecode((float)roiSum / roiCount, rawSampleUs);
  }
  // Sample the OLD ROI first; tracking positions are used by the next JPEG.
  updateTracking();
  if (edgeMode == EDGE_NORMAL && capturedFrames % 100U == 0) {
    const uint32_t elapsedMs = millis() - captureStartMs;
    const float measuredFps = elapsedMs ? capturedFrames * 1000.0f / elapsedMs : 0.0f;
    Serial.print("[STATUS] frames="); Serial.print(capturedFrames);
    Serial.print(" fps="); Serial.print(measuredFps, 2);
    Serial.print(" samples="); Serial.print(ringCount);
    Serial.print(" valid="); Serial.print(validPackets);
    Serial.print(" rejected="); Serial.print(crcRejects);
    Serial.print(" classic_rejected="); Serial.print(classicCrcRejects);
    Serial.print(" candidates="); Serial.print(plausibleCandidates);
    Serial.print(" preamble_pass="); Serial.print(preamblePasses);
    Serial.print(" crc_pass="); Serial.print(crcPasses);
    Serial.print(" phys_reject="); Serial.print(physiologyRejects);
    Serial.print(" signal="); Serial.print(lastSignal, 1);
    Serial.print(" range=");
    if (ringCount) {
      Serial.print(signalMinimum, 1); Serial.print(".."); Serial.print(signalMaximum, 1);
    } else Serial.print("n/a");
    Serial.print(" track="); Serial.print(trackEnabled ? "on" : "off");
    Serial.print(" track_moves="); Serial.print(trackMoves);
    Serial.print(" track_lost="); Serial.print(trackLost);
    Serial.print(" track_reacq="); Serial.print(trackReacq);
    Serial.print(" roi_activity="); Serial.print(lockedRoiActivity, 2);
    Serial.print(" roi_mean="); Serial.print(lockedRoiMean, 1);
    Serial.print(" roi=");
    if (roiLocked) {
      Serial.print(roiPixelX); Serial.print(",");
      Serial.print(roiPixelY); Serial.print(",");
      Serial.print(roiPixelW); Serial.print(","); Serial.print(roiPixelH);
    } else Serial.print("searching");
    Serial.print(" last_valid_age_s=");
    if (lastValidPacketMs) Serial.print((millis() - lastValidPacketMs) / 1000UL);
    else Serial.print(-1);
    Serial.print(" ones="); Serial.print(lastPredictedOnes);
    Serial.print(" best_pre_err="); Serial.print(bestPreambleErrors);
    Serial.print(" diag_err="); Serial.println(bestDiagnosticErrors);
  }
}
