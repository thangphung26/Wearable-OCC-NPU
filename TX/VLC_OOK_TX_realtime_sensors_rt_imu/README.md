# RT TX with on-board IMU activity

Copy of `../VLC_OOK_TX_realtime_sensors_rt/` for the Seeed XIAO nRF52840
Sense, Seeeduino:nrf52 core 1.1.13. The original RT sketch is unchanged.
The optical link remains 5 bit/s, Manchester OOK with 10-second frames.
TIMER4 continues to drive the LED; all IMU work runs in `loop()`.

## Hardware and registers

Core source: `~/.arduino15/packages/Seeeduino/hardware/nrf52/1.1.13/variants/Seeed_XIAO_nRF52840_Sense/variant.h`:

- `PIN_LSM6DS3TR_C_POWER = 15`, configured for H0H1 high drive before HIGH,
  followed by 100 ms power settling before initialization.
- `PIN_WIRE1_SDA = 17`, `PIN_WIRE1_SCL = 16`; configure `Wire1` at 100 kHz.
- `PIN_LSM6DS3TR_C_INT1 = 18` is unused (polling).
- External sensors retain `Wire` at 100 kHz, SDA D4 / SCL D5.

The adjacent `variant.cpp` maps power to P1.08, SDA to P0.07, SCL to P0.27,
and INT1 to P0.11. `libraries/Wire/Wire_nRF52.cpp` constructs `Wire1` with
the `PIN_WIRE1_*` macros.

Hardware root cause confirmed by the manager on 2026-11-08: normal `OUTPUT`
HIGH on P1.08 left Wire1 SDA/SCL at 0/0 even with pull-ups, and transfers hung.
With H0H1 high drive, the bus idled at 1/1, WHO_AM_I read 0x6A and acceleration
was about (-0.98, 0.00, 0.27) g. The shared `ImuBus::powerOn()` derives port/pin
from `g_ADigitalPinMap[PIN_LSM6DS3TR_C_POWER]` (40 = P1.08), configures output,
disconnected input, no pull, H0H1 and disabled sense, then drives HIGH.
Do not call `pinMode(OUTPUT)` afterward: it restores standard S0S1 drive.

Direct register access requires no added library. Address 0x6A; WHO_AM_I
(0x0F) must be 0x6A or 0x69. CTRL3_C=0x44 enables BDU and auto-increment;
CTRL2_G=0x00 powers down the gyro; CTRL1_XL=0x40 selects 104 Hz, +/-2 g.
Configuration is read back. OUTX_L_XL (0x28) starts the six-byte acceleration
read; scale is 0.000061 g/LSB. Register reference:
[ST LSM6DS3TR-C datasheet](https://www.st.com/resource/en/datasheet/lsm6ds3tr-c.pdf).

## Activity rule and limitations

At approximately 50 Hz (one scheduled read every 20 ms), compute
`|a| = sqrt(ax*ax + ay*ay + az*az)` in g. Each non-overlapping wall-clock
one-second window uses Welford accumulation and population SD (`sqrt(M2/N)`).
A window is moving only if SD **exceeds** `IMU_MOVE_SD_G`, **0.015 g** (tuned 2026-11-08 with the TX worn on the forehead, cued still/nod/still protocol: seated still had at most 2 windows >0.015 g in any 10 s; head nodding had up to 10/10; initially 0.03 g)
in `ImuActivity.h`. At each frame build, including immediate alert frames,
Activity is **1** if at least **4 of the last 10** completed windows moved (`IMU_ACTIVITY_MIN_WINDOWS`);
otherwise it is **0**. Startup history is zero-filled. Empty/one-sample windows
do not count as moving; a gap longer than 10 seconds clears history. A frame is
normally prepared during the last second of the preceding frame, so Activity
and validity describe that snapshot, not the instant deferred serial logs print.

Missing IMU, wrong identity/configuration, or a failed sample read marks it
invalid and forces Activity 0 until reset. Transmission continues.
`imu_valid=0` is appended to `[TX_GT]`; numeric `"imu_valid":0` is appended
to `[TX_TELEMETRY]` JSON. Valid readings report 1. Existing fields retain order.

**IMU-based fall and seizure detection is NOT implemented.** Neither measured
nor synthetic frames emit Activity 2 or 3. The synthetic benchmark generator
uses only one random activity bit in this copy. Activity 1 does not assert an
alert; the original vital-sign/manual serial alert logic is unchanged.
Activity measures forehead acceleration variability, not posture or walking.
Slow rotation with nearly constant magnitude may remain 0.

## Serial tuning (hardware only)

`IMU ON` enables one diagnostic line per second; `IMU OFF` disables it:

```text
[IMU] sd_g=0.00420 moving=0 windows_moving=1/10 ax=0.0100 ay=-0.0200 az=0.9990 imu_valid=1
```

`sd_g`/`moving` describe the last completed window; axes are the latest sample
in g. Invalid/unavailable SD or axes print NaN. Tune the threshold using actual
forehead placement. No sample-ready wait or catch-up read burst is used.
IMU transfers use the shared `../tools/imu_probe/BoundedImuBus.h` helper with
interrupts enabled. `Wire1.begin()` and `setClock()` configure TWIM1; transfers
then use that peripheral's EasyDMA/events directly because core 1.1.13 has no
Wire timeout API. Every phase checks GPIO bus idle first and has a 5 ms deadline,
plus at most 1 ms for STOP on failure. STOP shortcuts finish successful phases;
errors/timeouts disable TWIM1 before stack DMA buffers leave scope. A register
read has two STOP-separated phases. Do not mix this helper with Wire1 transfer
calls or another driver using peripheral instance 1.

Initialization happens once in the first loop pass, after Serial and TIMER4
start, including in battery benchmark mode. It uses the 100 ms power delay,
checks bus idle, verifies identity and reads back configuration. The first frame
is IMU-invalid. One `[IMU] init ok whoami=0x..` or
`[IMU] init FAIL step=... reason=... errorsrc=0x..` line reports the result.
Initialization/sample failures keep Activity 0 and `imu_valid=0` until reset.
The external-sensor **Wire/TWIM0** calls are unchanged and still have the core's
unbounded waits. TIMER4 timing and IMU timeout recovery need hardware verification.
See the [boot probe and diagnosis](../tools/imu_probe/README.md).

## Verification

From the repository root, compile without upload:

```sh
/home/usnlab/ros2_ws/tools/arduino-cli/arduino-cli compile --fqbn Seeeduino:nrf52:xiaonRF52840Sense --build-path /tmp/vlc_tx_rt_imu_build 1_VLC_code_main/TX/VLC_OOK_TX_realtime_sensors_rt_imu
g++ -std=c++11 -Wall -Wextra -Werror -pedantic 1_VLC_code_main/TX/VLC_OOK_TX_realtime_sensors_rt_imu/tests/test_imu_activity.cpp -o /tmp/vlc_tx_imu_activity_test
/tmp/vlc_tx_imu_activity_test
```

Hardware plan, once the live serial session is released:

1. Upload, enable `IMU ON` and `TIMING ON`; confirm `imu_valid=1` and gravity
   magnitude near 1 g. Sit still for 30 s: expect Activity 0.
2. Nod/move the head for at least two one-second windows with SD >0.015 g (at least four windows):
   expect Activity 1 at the next snapshot/frame. Confirm no IMU-only alert.
3. Sit still again: expect 0 once fewer than two moving windows remain in the
   ten-window history, allowing for frame snapshot/transmission latency.
4. Check `IMU OFF` stops diagnostics. Verify 10.000-s frames and compare
   `max_edge_late_us` with the RT baseline of 9 us, with diagnostics both on/off.
5. On an appropriate absent-IMU setup, verify Activity 0, `imu_valid=0`, and
   ongoing frames. Confirm no state 2/3 appears.

Exact upload command (documented only; not run):

```sh
arduino-cli upload --fqbn Seeeduino:nrf52:xiaonRF52840Sense --port /dev/ttyACM0 --input-dir /tmp/vlc_tx_rt_imu_build 1_VLC_code_main/TX/VLC_OOK_TX_realtime_sensors_rt_imu
```
