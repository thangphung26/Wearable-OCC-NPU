# Final AUG v2 benchmark sketch

Paired CPU-reference/NPU benchmark with model headers resolved locally.
See the [AUG runbook](../../AI_Pipeline_amebaPro2/ameba_deployment/aug/RUNBOOK.md)
for conversion gates, host verification, compile, upload, replay and latency.
Use its v2 section and `Ameba_CPU_NPU_Benchmark_AUG_V2` for final results;
the non-v2 sketch is retained for legacy comparisons.

Generated NBG headers must match the checked conversion artifacts before build.
This benchmark does not publish live telemetry. See the
[project README](../../README.md) for the live receiver and result locations.
Firmware operation requires hardware verification.
