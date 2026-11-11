# VLC Patient Telemetry Monitor — bản mẫu nghiên cứu

Dashboard hiển thị snapshot giải mã từ liên kết VLC. **Không phải thiết bị y tế**;
không dùng để chẩn đoán hay quyết định điều trị. Giao diện dùng tiếng Anh để đưa
vào hình minh họa bài báo; có theme sáng, tối và bản in.

## Khởi động

Từ thư mục gốc repo, dùng môi trường Python `.venv/`:

```bash
.venv/bin/python 1_VLC_code_main/web_medical_monitor/start_monitor.py --port 8080
```

Mở `http://127.0.0.1:8080`. HTTP và UDP mặc định bind **127.0.0.1**. Để nhận
UDP từ RX qua mạng lab, chỉ định địa chỉ giao diện phù hợp bằng `--host`.
`--host 0.0.0.0` mở mọi giao diện và in cảnh báo. Chưa có xác thực; chỉ dùng trên
mạng lab tin cậy. Không hỗ trợ browser POST khác origin.

### Archive raw UART cho phiên chạy thật

Mặc định không ghi raw log. Bật bằng `--raw-log-dir <dir>`; ví dụ từ gốc repo:

```bash
.venv/bin/python 1_VLC_code_main/web_medical_monitor/start_monitor.py --port 8090 --raw-log-dir 1_VLC_code_main/results/raw_serial
```

Mở `http://127.0.0.1:8090/monitor.html`. Cổng được kết nối **qua dashboard**, không
phải CLI: trong COM ports chọn RX `/dev/ttyUSB0`, TX `/dev/ttyACM0`, cả hai
`115200` baud, rồi **Connect RX + TX**. Log chỉ bắt đầu có dòng khi cổng đã kết nối.

Mỗi lần khởi động tạo `1_VLC_code_main/results/raw_serial/<YYYYmmdd_HHMMSS>/`
(timestamp UTC); từ chối nếu thư mục phiên đã tồn tại. `rx_raw.jsonl` và
`tx_raw.jsonl` giữ mọi serial read không rỗng, kể cả dòng trắng, boot/debug text
và dòng sai định dạng. Mỗi JSON có `host_time_ns` (host wall clock lúc đọc),
`port`, `line` (UTF-8, byte lỗi thay bằng U+FFFD, giữ whitespace/newline) và
`kind` (`accepted`/`rejected`/`ignored` của UART parser; không phải kết quả đối
chiếu TX hoặc bộ lọc dashboard). Read dài bị chia theo giới hạn 16385 byte hiện có.
`session_meta.json` ghi thời gian bắt đầu, ports/baud (null trước khi kết nối),
lịch sử kết nối và SHA256 từng file Python backend cùng launcher, không dùng git.
Ctrl+C đóng cổng, ghi hết queue rồi thêm thời gian kết thúc và số dòng RX/TX.

Disk writer chạy riêng, queue tối đa 4096 mục; lỗi đĩa hoặc queue đầy sẽ tắt
archive và in `WARNING` trên stderr, UART parser vẫn chạy. File JSONL dùng line
buffering để flush từng dòng. Crash có thể mất các dòng còn trong queue; không
có fsync nên không bảo đảm dữ liệu khi mất điện. Kiểm tra terminal trong phiên
chạy để phát hiện archive đã bị tắt. Không archive UDP/API/simulation.

## Giao diện và công cụ

- Header luôn ghi **Research prototype — not a medical device**. Mô phỏng có thêm
  nhãn **SYNTHETIC DATA**, decoder `simulated`, không giả lập FPS/ROI/CRC phần cứng.
- **Selected patient**, **Source**, **Snapshot age** và trạng thái alarm nằm phía trên
  các thẻ HR, SpO2, Temp, IMU state, Battery. Giá trị `--` khi chưa có snapshot hoặc
  trường không hợp lệ. Pin là mức mã hóa 0–7, không phải phần trăm.
- **TX alert bit** hiển thị một lần trên status strip; alarm banner giữ cảnh báo TX.
  Các thẻ chỉ hiển thị **dashboard warning**; ngưỡng lấy trực tiếp
  từ backend, không dùng một bộ ngưỡng khác trên trình duyệt. Ngưỡng demo:
  HR <50 hoặc >140 bpm; SpO2 <94%; nhiệt độ ≥39°C; IMU state 2/3.
  Pin không có ngưỡng cảnh báo trong backend.
- IMU: **IMU state 0 — still**, **1 — moving**, **2 — fall flag**,
  **3 — protocol flag**. Đây là enum 2 bit; state 3 chỉ hiển thị cờ giao thức,
  không suy ra chẩn đoán. TX hiện tại bypass IMU và gửi `imu=0`, `battery=7`;
  cần phần cứng để kiểm tra đo cảm biến thực tế.
- **Time-based vital trends** dùng timestamp nhận gói và trục thời gian đồng hồ,
  chỉ của patient/source hiện tại. Giá trị thật không bị cắt trần. Dữ liệu không
  hợp lệ, mất SSE và khoảng cách >25 s tạo khoảng trống; không sinh tín hiệu liên tục.
- **VLC link & packets** hiển thị seq cuối, tuổi snapshot, CRC và bộ đếm
  valid / CRC-rejected / duplicate. Đây là bộ đếm dashboard, không phải BER/PDR.
  Danh sách gói mới nhất ở trên; các chuỗi UART được đưa vào DOM bằng text node.
- Nút **Settings** mở source selector, **COM ports**, **Benchmark reference**,
  **Ground-truth verify**, **Sound: off**, **Test alarm (simulation)**.
  Chọn patient và test alarm chỉ hoạt động trong simulation; chế độ thật theo patient
  của gói mới nhận, không chèn dữ liệu giả. Chuyển source xóa snapshot hiện tại.
- **Light theme** đổi nền sáng; mở `/?theme=light` để chụp hình. `Ctrl+P` dùng palette
  sáng, kể cả canvas. Dialog có focus trap, Esc và trả focus về nút mở; alarm có
  vùng aria-live. Không dùng hiệu ứng nhấp nháy; tôn trọng reduced motion.

## UART, UDP và API

1. Cắm RX AmebaPro2, mở **Settings → COM ports**; chọn RX port (thường
   `/dev/ttyUSB0`) và baud `115200`, bấm **Connect RX Only**.
2. TX tùy chọn dùng để đối chiếu ground truth: chọn TX port (thường `/dev/ttyACM0`),
   bấm **Connect TX Only** (giữ nguyên kết nối RX), hoặc **Connect RX + TX**.
3. **Data source → UDP** nhận gói trên UDP 8082; bind theo `--host`.
   API POST hợp lệ tự chuyển sang source API.

Giữ nguyên định dạng UART:

```text
[TELEMETRY:NPU] {"patient":1,"hr":75,"spo2":98,"temp_c":37.5,"imu":0,"battery":7,"alert":0,"seq":0}
```

Cần `pyserial` trong `.venv/` để dùng cổng thật. CRC false bị loại và đếm;
thiếu CRC hiển thị `unverified`. Ví dụ API từ máy chạy server:

```bash
curl -X POST http://127.0.0.1:8080/api/telemetry \
  -H 'Content-Type: application/json' \
  -d '{"patient":4,"hr":145,"spo2":88,"temp_c":39.5,"imu":2,"battery":4,"alert":1,"seq":10}'
```

## Tuổi dữ liệu, cảnh báo và tốc độ

**30 FPS acquisition danh định**, **20 Hz resampling**, **5 bit/s link** là ba tốc độ
khác nhau. Chỉ hiển thị acquisition FPS khi RX thật báo cáo; nếu thiếu là `—`.
Snapshot mặc định khoảng 10 s; simulation đổi chu kỳ bằng `--sim-period 10`.
Gói trùng patient/seq không làm mới tuổi snapshot. STALE từ 25 s, khoảng 2.5 chu kỳ.
Mất kết nối hoặc thiếu trường vital hiển thị **Data unavailable**, dừng âm báo và
không coi là hồi phục. Giá trị hợp lệ cũ vẫn giữ kèm tuổi và nhãn held value.

Alarm thuộc từng patient/episode. **Acknowledge episode** gửi patient và episode_id
đang hiển thị; backend từ chối episode đã đổi/hết hiệu lực. Cảnh báo cũ chưa giải
quyết vẫn được giữ khi dữ liệu không khả dụng. Âm thanh mặc định tắt; chỉ bật qua
**Sound: off**. Test/clear chỉ thay đổi cờ test trong simulation, không ép một
profile đang vượt ngưỡng trở thành bình thường.

**Ground-truth verify** so sánh chính xác các trường chuẩn hóa (nhiệt độ 0.1°C),
không tuyên bố bit-level accuracy. Không có comparison thì hiển thị N/A.
CSV xuất tối đa 60 comparison của phiên hiện tại. **Benchmark reference** tải
`/api/benchmark`, hiển thị source path từng phép đo; không phải latency của link.

## Giới hạn lưu trữ và kiểm tra

Backend giữ tối đa 200 snapshot, 100 log, 60 comparison, SSE queue 100/client,
TX cache 256 khóa (patient, seq) hết hạn 25 s. Trình duyệt giữ tối đa 360 điểm
(tính cả dấu gap), 200 dòng log, 100 hàng mỗi bảng. Restart xóa RAM; không đọc/ghi
CSV phiên cũ. API chi tiết: [BACKEND_API.md](BACKEND_API.md).

```bash
.venv/bin/python -m unittest discover -s 1_VLC_code_main/web_medical_monitor/tests -v
```

Cần kiểm tra phần cứng riêng cho UART reconnect, acquisition FPS, timing gói VLC,
IMU/pin thật và âm thanh trình duyệt. Ngưỡng và cảnh báo chỉ phục vụ minh họa.

### Paper figure view

Open `/?view=figure` for a fixed 1600×1000 layout with 1.25× base typography.
It defaults to light theme; `/?view=figure&theme=dark` selects dark theme.
Theme/settings buttons, the simulation selector and alarm acknowledgement are hidden;
alarm text, the research-prototype badge and the simulation badge remain visible.
Capture at a 1600×1000 viewport. Dashboard counters appear once, labelled with
24-hour scope start time and source; switching source or reconnecting UART resets them.
Simulation reports `camera FPS n/a (simulation)`; 20 Hz resampling and 5 bit/s link
remain distinct from the hardware's 30 FPS acquisition rate.

The trend window is `min(10 min, max(actual span × 1.1, 6 × snapshot period))`.
Short runs are centered. With a 10 s snapshot period, a 20 s run spans one third
of the 60 s minimum window; the earlier review's ≥60% width criterion cannot
coexist with this required minimum.
