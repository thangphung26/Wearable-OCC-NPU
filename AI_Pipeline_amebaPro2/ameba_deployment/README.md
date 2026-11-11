# AmebaPro2 decoder deployment

The final model is the **augmented INT8 CNN, v2**. Its TFLite is
`../retrain_sessions/results_v2/repeat_aug/deployment_candidate_aug_int8.tflite`;
its NBG is `aug/work_v2/vlc_edge_cnn_aug_v2.nb`. Follow
[aug/RUNBOOK.md](aug/RUNBOOK.md), especially its paired `results_v2` /
`corrected-7m` environment settings, for conversion, CPU export, build and replay.

The model uses signed INT8 input NHWC `[1,48,1,4]`, scale `1/255`, zero point
`-128`; signed INT8 output `[1,48,1]`, scale `1/256`, zero point `-128`.
A raw output value `>= 0` decodes as bit 1. AmebaPro2 loads the Acuity network
binary (`.nb`), not a TFLite file directly.

## Retained original CH4 reference

`vlc_edge_cnn_ch4_int8.tflite`, `vlc_edge_cnn_ch4.nb` and
`vlc_edge_cnn_ch4_nbg_meta.json` form the original matched CH4 pair used by
capture/legacy reference tools. The CH4 NBG is 62,688 bytes, SHA-256
`2838c4db8f34af99599c528a765d254e6ab82c8d2d76bdaedce8079cacdd9fb8`;
the TFLite is 32,232 bytes, SHA-256
`ce60f7f60045592f3618d717001c2962538d601175f952994802e5b9c04c8175`.
Its INT8 tensor contract matches the AUG layout above. CH4 calibration inputs
are retained for the original reference export. Earlier non-CH4 conversion
artifacts and their procedure are no longer distributed.

The capture sketch retains the earlier model so it compiles. Final accuracy
uses stored inputs replayed through AUG v2 on board CPU and NPU, not the capture
firmware's CNN output. The non-augmented retrained model is an ablation.
Conversion parity, firmware identity and CPU/NPU execution must be verified on
the hardware/toolchain host. See the [project README](../../README.md).
