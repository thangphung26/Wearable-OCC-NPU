# IMU boot probe and init hardening

Built against installed Seeeduino:nrf52 1.1.13; no upload or serial access was
performed. The probe holds optical D9 LOW and all three active-low board LEDs
HIGH. Firmware behavior, electrical faults and timing require hardware testing.

## Confirmed hardware root cause and local core evidence

The manager confirmed on hardware (2026-11-08) that normal `OUTPUT` HIGH on
IMU power P1.08 left Wire1 SDA/SCL at 0/0, even with pull-ups, and transfers
hung. With H0H1 high drive, SDA/SCL idled at 1/1, WHO_AM_I was 0x6A and
acceleration was about (-0.98, 0.00, 0.27) g. The shared `ImuBus::powerOn()`
derives port/pin from `g_ADigitalPinMap[PIN_LSM6DS3TR_C_POWER]` (40 = P1.08),
configures output, disconnected input, no pull, H0H1 and disabled sense before
driving HIGH. Both sketches then allow 100 ms power settling. A subsequent
`pinMode(OUTPUT)` would restore S0S1 and must not be used on the power pin.

Sources below are relative to
`~/.arduino15/packages/Seeeduino/hardware/nrf52/1.1.13/`:

- `variants/Seeed_XIAO_nRF52840_Sense/variant.h` defines
  `WIRE_INTERFACES_COUNT=2`, power pin 15, SDA1 17 and SCL1 16.
  `variant.cpp` maps them to P1.08, P0.07 and P0.27 respectively.
- `libraries/Wire/Wire_nRF52.cpp` constructs **Wire on TWIM0** and **Wire1 on
  TWIM1**, with distinct IRQs. `TwoWire::begin()` and `setClock()` only configure
  registers/GPIO/NVIC; neither has a polling loop. They are less likely hang
  sites than the transfers.
- That file's `endTransmission()` waits indefinitely for TXSTARTED, LASTTX,
  STOPPED or SUSPENDED. `requestFrom()` likewise waits for RXSTARTED, LASTRX,
  STOPPED or SUSPENDED. `libraries/Wire/Wire.h` exposes no timeout setter.
  Checking idle before a normal Wire transfer cannot bound a mid-transfer stall.
- `libraries/SPI/SPI.cpp` selects SPIM3/SPIM2 for SPI/SPI1 on nRF52840 (swapped
  by `SPI_32MHZ_INTERFACE`). Thus the standard SPI objects do not share instance
  0/1 with these Wire objects. This sketch does not use SPI.
- The old IMU initializer already delayed **20 ms** after power HIGH. Missing
  power delay is not supported by this source. It called init before TIMER4,
  though after Serial and startup banners. Complete USB silence therefore
  cannot conclusively be attributed to IMU init: early output may have been
  missed, or the fault may precede it (external Wire/USB/power/reset).

The fix uses H0H1 and 100 ms power settling, selects 100 kHz and initializes the
TX in the first loop pass after TIMER4 starts. Both sketches share `BoundedImuBus.h`, which uses
the core's TWIM1 register mechanism with STOP shortcuts, GPIO idle checks and
bounded event polling. Each phase gets 5 ms, plus 1 ms STOP cleanup on failure;
the peripheral is disabled on failure to end DMA ownership. No interrupt masking,
retry loop, bus recovery clocking or core installation changes are used.

Register settings and 0.000061 g/LSB scale at +/-2 g follow the
[ST LSM6DS3TR-C datasheet](https://www.st.com/resource/en/datasheet/lsm6ds3tr-c.pdf).
Expected TR-C WHO_AM_I is 0x6A; 0x69 remains accepted for compatibility with the
existing sketch. A healthy probe also checks CTRL3_C/CTRL2_G/CTRL1_XL readback.

## Reading the probe

Every line is flushed. Operations have a preceding `begin` line and a following
result, so the last line identifies where execution stopped.

| Output | Meaning / next check |
| --- | --- |
| `USB wait complete...` | Setup reached Serial after waiting at most 3 s for USB. If absent, investigate USB/reset/power or attach timing before blaming I2C. |
| `power pin H0H1 HIGH begin/ok` | Pin 15 was configured for high drive and commanded HIGH; measure P1.08/IMU supply to verify actual power. |
| `power delay 100 ms begin/ok` | Power settling delay completed. |
| `GPIO idle SDA=... SCL=...` | GPIO readings with input pullups, before Wire1.begin. Both should be 1. A 0 aborts before any transfer; inspect that line, IMU supply and shorts. |
| `Wire1.begin begin/ok` | Isolates core pin/peripheral configuration from transfers. |
| `setClock(100000) begin/ok` | Isolates the core frequency configuration. |
| `WHO_AM_I read begin/ok`, `WHO_AM_I=0x..` | STOP-separated register-pointer TX and RX succeeded; value identifies the responding part. |
| `CTRL3_C write`, `CTRL2_G write`, `CTRL1_XL write` | Confirms BDU/auto-increment, gyro off and 104 Hz +/-2 g writes respectively. |
| `configuration readback` | Confirms all three settings persisted; `config-value` indicates a mismatch. |
| `accel read begin n=1..20`, `accel_g n=... x=... y=... z=...` | Six-byte reads converted to g, spaced 20 ms apart after 100 ms settling. Stationary magnitude should be near 1 g; rotate the board to check axes. |
| `FAIL reason=... errorsrc=0x..` | `bus-low`, `transfer-timeout`, `stop-timeout`, `nack-or-bus-error` or `short-transfer`; ERROR bits 0/1/2 represent overrun/address NACK/data NACK. A timeout may have ERROR bits 0. |
| `[PROBE] DONE` | Probe returned, including on failure. Success requires 20 sample lines and no FAIL line. No subsequent bus activity occurs. |

## Commands and verification

Commands run from the repo root:

```sh
/home/usnlab/ros2_ws/tools/arduino-cli/arduino-cli compile --fqbn Seeeduino:nrf52:xiaonRF52840Sense --build-path /tmp/vlc_imu_probe_build 1_VLC_code_main/TX/tools/imu_probe
/home/usnlab/ros2_ws/tools/arduino-cli/arduino-cli compile --fqbn Seeeduino:nrf52:xiaonRF52840Sense --build-path /tmp/vlc_tx_rt_imu_build 1_VLC_code_main/TX/VLC_OOK_TX_realtime_sensors_rt_imu
g++ -std=c++11 -Wall -Wextra -Werror -pedantic 1_VLC_code_main/TX/VLC_OOK_TX_realtime_sensors_rt_imu/tests/test_imu_activity.cpp -o /tmp/vlc_tx_imu_activity_test
/tmp/vlc_tx_imu_activity_test
```

Both builds passed. Host classifier: `IMU activity tests passed`.

| Target | Flash / 811008 bytes | Global RAM / 237568 bytes |
| --- | ---: | ---: |
| imu_probe | 49828 (6%) | 7532 (3%) |
| rt_imu | 69088 (8%) | 15880 (6%) |

Exact upload commands for the manager, **documented only, not run** (port may
change during bootloader re-enumeration):

```sh
/home/usnlab/ros2_ws/tools/arduino-cli/arduino-cli upload --fqbn Seeeduino:nrf52:xiaonRF52840Sense --port /dev/ttyACM0 --input-dir /tmp/vlc_imu_probe_build 1_VLC_code_main/TX/tools/imu_probe
/home/usnlab/ros2_ws/tools/arduino-cli/arduino-cli upload --fqbn Seeeduino:nrf52:xiaonRF52840Sense --port /dev/ttyACM0 --input-dir /tmp/vlc_tx_rt_imu_build 1_VLC_code_main/TX/VLC_OOK_TX_realtime_sensors_rt_imu
```

On hardware: run the probe first, capture every step and verify the LEDs stay
off. Then verify the TX's one-time init result, `IMU ON`, gravity/activity and
10-second frames at the unchanged 5 bit/s link rate. Safely injected stuck-low
faults before init and during acquisition should leave `imu_valid=0`, responsive
commands and ongoing optical frames. Check edge lateness against the RT baseline.
External Wire sensor stalls and USB power/reset faults are not addressed here.
