# Repeat-3 augmented robustness

Four paired decoders; 8 held-out sessions. Timing denominators: 38,400 / 28,800 bits (all / 1–5 m). Noise/contrast denominators: 192,000 / 144,000 bits across five realizations. Entries below are all-distance / 1–5 m error counts. Full BER and 95% Clopper–Pearson intervals are in `summary.json`; exact positions are in `results.csv`.

Run `robustness.py --aug` from the root virtual environment. Training and augmentation details: `../../README_aug.md`. Both candidate histories are asserted disjoint from the repeat-3 test sessions. The original three decoder series exactly reproduce the prior robustness run. The clean old-model count is offline inference (35), versus 34 in stored CPU decisions; see the earlier parity caveat in `../robustness/README.md`.

Plots: 3.5-inch-wide PNG (300 dpi) and PDF, four series, log-y BER and 95% CP error bars. Top panel: 1–7 m. Bottom panel: 1–5 m. Open markers locate zero-error points at their CI upper bound. These pooled binomial intervals use correlated bits and repeated captured windows, rather than independent captures. Acquisition: 30 FPS; resampling: 20 Hz; link: 5 bit/s. Noise estimators and SNR definition are unchanged from `../robustness/README.md`.

## timing_offset

| Point | Classic | Old INT8 | New INT8 | New aug INT8 |
|---:|---:|---:|---:|---:|
| -40 | 362 / 153 | 1226 / 732 | 3607 / 2662 | 18 / 4 |
| -30 | 59 / 22 | 98 / 36 | 95 / 42 | 8 / 5 |
| -20 | 13 / 6 | 33 / 10 | 20 / 10 | 8 / 6 |
| -10 | 8 / 6 | 20 / 8 | 7 / 6 | 7 / 6 |
| 0 | 9 / 6 | 35 / 12 | 7 / 6 | 9 / 6 |
| 10 | 9 / 6 | 154 / 93 | 12 / 6 | 7 / 6 |
| 20 | 24 / 6 | 959 / 680 | 17 / 7 | 7 / 6 |
| 30 | 75 / 7 | 3548 / 2621 | 77 / 14 | 7 / 6 |
| 40 | 362 / 88 | 7185 / 5315 | 1168 / 737 | 24 / 7 |

## contrast

| Point | Classic | Old INT8 | New INT8 | New aug INT8 |
|---:|---:|---:|---:|---:|
| 1 | 58 / 26 | 402 / 108 | 53 / 24 | 64 / 28 |
| 0.5 | 498 / 75 | 1981 / 715 | 584 / 92 | 484 / 59 |
| 0.25 | 6155 / 2744 | 11592 / 6193 | 7103 / 3272 | 6163 / 2725 |
| 0.1 | 38610 / 26227 | 46266 / 32131 | 40367 / 27499 | 37617 / 25565 |

## snr

| Point | Classic | Old INT8 | New INT8 | New aug INT8 |
|---:|---:|---:|---:|---:|
| 20 | 38 / 25 | 163 / 53 | 32 / 26 | 39 / 26 |
| 15 | 41 / 27 | 201 / 71 | 37 / 26 | 45 / 30 |
| 10 | 53 / 29 | 432 / 220 | 45 / 26 | 56 / 28 |
| 6 | 137 / 38 | 1501 / 910 | 162 / 58 | 121 / 34 |
| 3 | 932 / 521 | 4307 / 2921 | 1207 / 700 | 943 / 529 |
| 0 | 5201 / 3625 | 11784 / 8421 | 6144 / 4310 | 5309 / 3696 |

Robustness runtime: 20.38 s. CSV and hashes: `verification.json`. All three PNGs visually inspected. Physical deployment remains unverified.
