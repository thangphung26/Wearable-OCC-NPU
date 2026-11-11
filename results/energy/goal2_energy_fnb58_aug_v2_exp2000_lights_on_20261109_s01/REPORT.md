# FNB58 energy: goal2_energy_fnb58_aug_v2_exp2000_lights_on_20261109_s01

complete=true

Variant: aug-v2

This is whole-board power at the USB input, including the CH340 USB-UART and the camera. The meter cannot resolve individual ~3 ms inferences. The CNN input is a fixed bank replay, not the live ROI tensor. CPU mode drops to ~14 FPS camera rate (see the per-leg reports below), so its per-job energy mixes effects.

Timing: pipelineStage(10, false) is a separate warm-up. The reported elapsed_us starts in pipelineStage(seconds, true), whose counters and bank sequence restart. The measured start is REPORT host_time_ns minus elapsed_us, with a 1 s guard at both edges. Serial formatting/transport latency shifts this estimate; there is no shared device clock. Raw CSV timestamps are packet receive times. Analysis reconstructs four sample times at receive time minus 40, 30, 20, 10 ms, following the reference logger at nominal 100 samples/s. USB jitter and host clock adjustments can affect alignment and integration.

Report jobs and expected_jobs must both equal commanded duration × 20. They describe the full post-warm-up stage. Energy per job for the guarded interval divides its trapezoid energy by guarded seconds × 20 Hz (a job-equivalent estimate, since individual job times are unavailable). The 30 FPS requested camera acquisition, 20 Hz resampling/replay, and 5 bit/s link are distinct.

CRC-failed samples remain in the raw CSV and are excluded from all power/energy statistics. Energy uses clipped trapezoids on reconstructed host times, interpolating bracketed interval edges and bridging gaps; coverage and gap counts flag missing data. Count energy is Σ(V·I) × 10 ms. Their ratio exposes sampling/timing discrepancies. One-second bins are aligned to the guarded start; the final partial bin is included.

Statistics: mean ± sample SD across repeat means describes repeat variability; within-leg power SD describes sample variation. Repeats receive equal weight. Incremental power is paired with camera-only within each repeat; incremental mJ/job = ΔP / 20 Hz × 1000. All per-repeat values are shown, including for three repeats. No confidence intervals are inferred from this small sample; these measurements do not estimate a binomial proportion.

| Repeat | Mode | P mean ± SD (W) | V | I (A) | 1-s P min/max (W) | Valid samples | CRC failed samples/packets | Gaps >50ms | E (J) | Count E (J) | Ratio | Report jobs | Guarded job equivalents | mJ/job | FPS | Inference ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | camera | 0.823682 ± 0.0376503 | 4.89278 | 0.168349 | 0.818546/0.884372 | 11801 | 0/0 | 0 | 97.1968 | 97.2027 | 0.999939 | 2400 (expected 2400, confirmed True) | 2360.07 | 41.1839 | 30.0001 | 0 |
| 1 | cpu | 0.841839 ± 0.0349029 | 4.89348 | 0.172035 | 0.820733/0.851397 | 11807 | 0/0 | 0 | 99.3871 | 99.3959 | 0.999912 | 2400 (expected 2400, confirmed True) | 2361.2 | 42.0918 | 19.9917 | 22.3326 |
| 1 | npu | 0.823155 ± 0.0368002 | 4.89673 | 0.168105 | 0.819329/0.836468 | 11804 | 0/0 | 0 | 97.1659 | 97.1652 | 1.00001 | 2400 (expected 2400, confirmed True) | 2360.76 | 41.1588 | 30.0008 | 2.93174 |
| 2 | npu | 0.827372 ± 0.0372928 | 4.897 | 0.168957 | 0.820547/0.836899 | 11804 | 0/0 | 0 | 97.6613 | 97.663 | 0.999983 | 2400 (expected 2400, confirmed True) | 2360.76 | 41.3686 | 30.0008 | 2.93606 |
| 2 | cpu | 0.838698 ± 0.0275593 | 4.89427 | 0.171366 | 0.832012/0.842716 | 11807 | 0/0 | 0 | 99.0217 | 99.025 | 0.999966 | 2400 (expected 2400, confirmed True) | 2361.32 | 41.9349 | 20.004 | 22.3327 |
| 2 | camera | 0.825846 ± 0.0364884 | 4.89703 | 0.168644 | 0.818683/0.830234 | 11805 | 0/0 | 0 | 97.4801 | 97.4911 | 0.999887 | 2400 (expected 2400, confirmed True) | 2360.76 | 41.2918 | 30 | 0 |
| 3 | camera | 0.824364 ± 0.0384136 | 4.89732 | 0.168332 | 0.789361/0.83047 | 11805 | 0/0 | 0 | 97.3061 | 97.3162 | 0.999896 | 2400 (expected 2400, confirmed True) | 2360.76 | 41.2181 | 30 | 0 |
| 3 | cpu | 0.843206 ± 0.0290992 | 4.89346 | 0.172314 | 0.83871/0.85008 | 11808 | 0/0 | 0 | 99.5558 | 99.5658 | 0.9999 | 2400 (expected 2400, confirmed True) | 2361.36 | 42.1604 | 19.9953 | 22.3325 |
| 3 | npu | 0.824629 ± 0.0364906 | 4.8963 | 0.168421 | 0.818171/0.836142 | 11805 | 0/0 | 0 | 97.3368 | 97.3475 | 0.99989 | 2400 (expected 2400, confirmed True) | 2360.76 | 41.2311 | 30.0008 | 2.92462 |

| Mode | Repeat | P (W) | Paired ΔP (W) | Δ mJ/job |
|---|---|---|---|---|
| camera | 1 | 0.823682 | 0 | 0 |
| camera | 2 | 0.825846 | 0 | 0 |
| camera | 3 | 0.824364 | 0 | 0 |
| camera | mean ± SD | 0.824631 ± 0.00110627 (n=3) | 0 ± 0 (n=3) | 0 ± 0 (n=3) |
| cpu | 1 | 0.841839 | 0.0181566 | 0.907829 |
| cpu | 2 | 0.838698 | 0.0128519 | 0.642593 |
| cpu | 3 | 0.843206 | 0.0188422 | 0.942111 |
| cpu | mean ± SD | 0.841247 ± 0.00231163 (n=3) | 0.0166169 ± 0.00327859 (n=3) | 0.830845 ± 0.163929 (n=3) |
| npu | 1 | 0.823155 | -0.000526878 | -0.0263439 |
| npu | 2 | 0.827372 | 0.00152646 | 0.0763228 |
| npu | 3 | 0.824629 | 0.000265487 | 0.0132744 |
| npu | mean ± SD | 0.825052 ± 0.00214013 (n=3) | 0.000421688 ± 0.00103554 (n=3) | 0.0210844 ± 0.051777 (n=3) |

Idle segments:
- before: idle (benchmark firmware waiting for command); 30.0001 s, 3001 valid samples, P=0.785551 ± 0.0232704 W, V=4.88391 V, I=0.160846 A, E=23.5665 J, count E=23.5744 J, ratio=0.999667, CRC failures=0 samples/0 packets, gaps >50ms=0.
- after: idle (benchmark firmware waiting for command); 30.0002 s, 3000 valid samples, P=0.789245 ± 0.0247304 W, V=4.90126 V, I=0.16103 A, E=23.6774 J, count E=23.6774 J, ratio=1, CRC failures=0 samples/0 packets, gaps >50ms=0.

Warnings:
- None.
