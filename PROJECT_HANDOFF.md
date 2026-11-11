# Project handoff

Start with [README.md](README.md) for the system, build commands, final artifacts
and result locations. The final decoder is the augmented INT8 CNN (AUG v2);
the model without augmentation is an ablation.

The independent test records 2/115,200 bit errors on the CNN NPU versus 3 for
Classic. Final training/evaluation outputs are under
`AI_Pipeline_amebaPro2/retrain_sessions/results_v2/`; independent-test results
are in its `fresh/campaign_board_v2/` folder.

Use [README_aug.md](AI_Pipeline_amebaPro2/retrain_sessions/README_aug.md),
the [AUG runbook](AI_Pipeline_amebaPro2/ameba_deployment/aug/RUNBOOK.md), and
[CAPTURE_FRESH_TEST_RUNBOOK.md](CAPTURE_FRESH_TEST_RUNBOOK.md) for reproduction.
Keep 30 FPS acquisition, 20 Hz resampling and the 5 bit/s link distinct.
Firmware flashing, sensor behavior and optical reception require hardware
verification. This is a research prototype, not a medical device.
