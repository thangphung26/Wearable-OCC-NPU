# VLC OOK Real-Time Sensor Transmitter (TX) - 5 bps Manchester

Firmware phát tín hiệu VLC đo cảm biến y tế thời gian thực cho bo mạch **Seeed Studio XIAO nRF52840** (hoặc Arduino Nano 33 BLE / ESP32).

---

## 1. Sơ đồ Đấu nối Phần cứng (Pinout)

| Thiết bị / Chân | Chân XIAO nRF52840 | Ghi chú |
|---|---|---|
| **VLC LED Emitter** | **D9** | Ngõ ra điều chế Manchester OOK (100 ms/half-bit). Mặc định `HIGH = BẬT`. |
| **I2C SDA** | **D4** | Kết nối SDA của MAX30102 |
| **I2C SCL** | **D5** | Kết nối SCL của MAX30102 |
| **Cảm biến Nhiệt độ Analog** | **A0** | Tùy chọn LM35; đặt `BODY_TEMP_SOURCE=2` và hiệu chuẩn offset |
| **Nguồn Cảm biến** | **3V3 & GND** | Cấp nguồn cho các module I2C 3.3V |

---

## 2. Cảm biến Hỗ trợ
1. **MAX30102 (PPG)**:
   - Đo nồng độ oxy trong máu **SpO2 (%)** và **Nhịp tim (HR - bpm)** qua phản xạ ánh sáng Red/IR.
   - Nhiệt độ bên trong MAX30102 là **nhiệt độ chip**, không phải nhiệt độ cơ
     thể. Firmware mặc định không công bố nó như một dấu hiệu sinh tồn.
2. **Nhiệt độ cơ thể**:
   - Mặc định `BODY_TEMP_SOURCE=0`: gửi trạng thái không hợp lệ.
   - Chỉ đặt `BODY_TEMP_SOURCE=2` khi đã nối LM35 ở A0 và hiệu chuẩn với nhiệt
     kế tham chiếu. Chế độ `1` chỉ dùng chẩn đoán nhiệt độ chip và không tạo báo sốt.
3. **Chất lượng và fail-safe**:
   - Mỗi trường có validity riêng: `HR=0`, `SpO2=0` và mã nhiệt độ `127`
     lần lượt nghĩa là trường đó không hợp lệ. HR vẫn có thể được công bố khi
     ratio Red/IR chưa đủ tốt để công bố SpO2.
   - HR cần ít nhất bốn nhịp nhất quán và ba cửa sổ ổn định liên tiếp. Firmware
     còn loại bão hòa ADC, tưới máu quá thấp và biến thiên nhịp quá lớn.
   - AGC tự điều chỉnh dòng LED Red/IR theo phản xạ trán. `262143` là ADC bão
     hòa và sẽ làm AGC giảm dòng; buffer cũ được xóa sau mỗi lần đổi gain.
   - SpO2 dùng ratio-of-ratios thực nghiệm. Hệ thống này chưa phải thiết bị y tế
     được chứng nhận; phải hiệu chuẩn và so sánh với pulse oximeter tham chiếu.
4. **IMU và pin**:
   - Firmware hiện tại vẫn gửi `IMU=0`, `battery=7`; chưa có driver đo thật.

---

## 3. Giao thức Khung tin Y tế (48-bit)
- **Header**: `0xA` (4 bit)
- **Patient ID**: 1..8 (4 bit)
- **Heart Rate**: 30..220 bpm; `0` = PPG không hợp lệ (8 bit)
- **SpO2**: 70..100%; `0` = PPG không hợp lệ (7 bit)
- **Temperature Offset**: `(Temp_C - 34.0) * 10`; `127` = không hợp lệ
- **IMU**: 0 (Resting), 1 (Moving), 2 (Fall), 3 (Seizure) (2 bit)
- **Battery**: 0..7 vạch pin (3 bit)
- **Alert**: 0 (Bình thường), 1 (Báo động khẩn cấp) (1 bit)
- **Sequence**: 0..15 (4 bit, tăng sau mỗi chu kỳ đo)
- **CRC-8**: Đa thức `0x07`, khởi tạo `0x00` trên 40 bit payload.

---

## 4. Chu kỳ Phát & Đồng bộ với Ameba Pro2 RX
- Mỗi gói tin 48 bit + 400 ms Delimiter mất **10.0 giây** ở tốc độ 5 bps.
- Mỗi snapshot được phát **một lần** trong 10 giây. RX công bố ngay khung có preamble, CRC và giới hạn vật lý hợp lệ.
- MAX30102 vẫn được lấy mẫu và lọc liên tục trong lúc VLC phát. Khi khung hiện tại kết thúc, firmware chụp snapshot kết quả ổn định mới nhất, tăng `seq = (seq + 1) % 16` và phát khung tiếp theo ngay lập tức.
- MAX30102 được phục vụ nền trong khi LED đang phát. Khi phát hiện bất
  thường, TX ngắt telemetry hiện tại và bắt đầu ngay frame có `Alert=1`.
- Bất thường tự động phải tồn tại 8 giây mới kích hoạt. Cờ chỉ được xóa sau khi
  trở lại bình thường liên tục 10 giây. Lệnh `ALERT` được giữ tới lệnh `NORMAL`.
