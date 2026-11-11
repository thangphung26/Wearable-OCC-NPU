# Legacy CH4 receiver reference support

This directory retains original CH4 model headers and CPU host-test support.
The original receiver sketch and pilot tooling are no longer distributed here.
Use [Ameba_RX_NPU_Receiver_Track_AUG](../Ameba_RX_NPU_Receiver_Track_AUG/README.md)
for live telemetry with the final augmented CNN and Classic fallback.

For buffered acquisition, use
[Ameba_CPU_NPU_Benchmark](../Ameba_CPU_NPU_Benchmark/README.md); final AUG v2
accuracy replays stored inputs on the board CPU and NPU. Keep 30 FPS
acquisition, 20 Hz resampling and the 5 bit/s link distinct. See the
[project README](../../README.md) for artifacts, results and build entry points.
Hardware operation requires separate verification.
