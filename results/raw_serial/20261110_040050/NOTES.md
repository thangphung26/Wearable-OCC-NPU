# TX-OFF false-publication test, ~24 h, uncontrolled room lighting (AUG v2 tracking receiver)
- RX firmware: RX/Ameba_RX_NPU_Receiver_Track_AUG, staged tools /tmp/vlc_rx_track_aug_tools, flash_ntz sha256 e7e41e29dc13cb477bb54eb2944d1f0618f8ee96f2a73d7df749776cef9e30e9, upload success 2026-11-10 ~12:03 KST, author pressed RESET. Boot [BUILD] line not captured; identity rests on the flash record.
- TX: powered (USB + LiPo (full), rt_imu firmware, transmitting) but out of the RX camera view (author, 2026-11-10; watch started 13:01 KST). TX serial is logged in tx_raw.jsonl so any RX publication can be checked against TX words.
- Before the scored interval the RX status showed crc_pass=1, valid=0 (from before the TX was moved out of view); the scored interval starts at the watch start and counts only publications after it.
- Lighting: uncontrolled normal room use (daylight, lamps by occupants). ROI brightness proxy is in the [TRACK]/[STATUS] lines (roi_mean).
- Watch: scratchpad txoff_watch_long.py, 86,400 s, summary txoff_long_summary.json (rewritten every 10 min).
- Outcome (checked 2026-11-11 09:40): the host PC crashed at ~03:07 KST 2026-11-11 (reboot 09:32), ending the run early.
  Scored interval 13:01:43 → 03:07:07 = 14.09 h; 0 accepted publications (80,050 raw RX lines, all 'ignored'); one-sided 95 % Poisson
  upper bound −ln(0.05)/14.09 = 0.213 /h. ROI-mean proxy: daylight 13–16 h (median 198–240), lamp-lit evening 18–22 h (~170–176),
  fully dark from ~23 h (0). txoff_long_summary.json is the 03:02 snapshot (14.01 h); the figures above are recomputed from rx_raw.jsonl.
