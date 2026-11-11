# TX USB-input power (rt_imu sensor firmware)
- 2026-11-10 12:01–12:11 KST, 600 s, FNB58 USB-input, 60,004 CRC-valid samples at 100 Hz.
- TX: XIAO nRF52840 Sense, firmware TX/VLC_OOK_TX_realtime_sensors_rt_imu (flashed 2026-11-10 ~11:52), 60 frames sent (one per 10 s).
- Condition: no finger on the sensors (MAX30102 "no finger"), TX transmitting, 3.7 V 600 mAh LiPo attached; battery full, charge LED OFF during the run
  (author confirmed 2026-11-10), so the USB figure is the TX load (via the board's power path), not charging.
- Result: mean 4.939 V, 96.1 mA, 0.474 W (per-minute means 95.7–96.3 mA). Bimodal current: 69.3 mA (LED off) and 124.9 mA (LED on), high level in 48.2% of samples (Manchester duty). Circuit (author, 2026-11-10): LED + 47-ohm from BAT+, 2N3904 low-side, 1-kohm base from D9. Expected LED current from BAT (<=4.2 V, blue Vf ~2.8-3.2 V) is only ~20-30 mA, so the 55.6-mA USB-input step must include charger/power-path behaviour (e.g. the charger supplying the battery node during LED-on); it is not the LED current.
- Battery-life estimate (rough, energy-based): 0.6 Ah x 3.7 V = 2.22 Wh / 0.474 W = 4.7 h; ignores USB-vs-battery power-path efficiency.
  Charge-based alternative (600 mAh / 96 mA = 6.2 h) is less conservative; report ~4.7 h.
- The frame "battery" field is a fixed placeholder (7 bars), not a measurement.
