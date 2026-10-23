# Vision Expo – theo dõi người đa camera cho triển lãm

Ứng dụng web chạy trên máy local: xem trực tiếp nhiều camera, thêm camera RTSP, chỉnh thông số, vẽ line hoặc vùng (polygon) đếm người vào/ra, phát hiện người (YOLO pose + ByteTrack) và kiểm tra PPE (mũ bảo hộ, găng tay từng tay) cho người trong vùng.

Nhận diện đồ vật theo từ khóa (YOLOE + CLIP) đang **gỡ tạm**; commit cuối còn tính năng này là `751e2bd`. Màn hình trình chiếu 3D (`Greco2026DashBoard`, build vào `show/`) mở ở `/show/`.

## Tài liệu

| File | Dành cho |
|---|---|
| [docs/SETUP.md](docs/SETUP.md) | Cài đặt sau khi clone: Windows, Docker, gói portable, mở LAN, xử lý sự cố |
| [docs/WEBSOCKET_API.md](docs/WEBSOCKET_API.md) | Bên làm app nhận dữ liệu: kết nối, định dạng từng message, code mẫu |
| [docs/DASHBOARD_GUIDE.md](docs/DASHBOARD_GUIDE.md) | Người vận hành: cách dùng từng trang của dashboard, chỉnh ngưỡng |
| [docs/CODE.md](docs/CODE.md) | Lập trình viên: kiến trúc, luồng xử lý, thuật toán, cách mở rộng |

## Cài đặt (một lần, cần internet)

Chạy trên chính máy mang đi triển lãm (môi trường Python không copy sang máy khác được):

1. Cài Python 3.10–3.12 (tick "Add to PATH").
2. Double-click `setup.bat`. Script tự:
   - tạo `.venv`, cài PyTorch (bản CUDA nếu có GPU NVIDIA, ngược lại bản CPU) và thư viện;
   - tải `yolo26n-pose.pt` (hoặc copy từ `../Production/models`) và `yolo26n-seg.pt` (mask người cho PPE); sau bước này chạy offline được.
3. Copy tay `yolo_ppe.pt` vào `models/` (không tải tự động được; thiếu file thì PPE tắt).

## Đóng gói mang sang máy khác (bản portable, khuyên dùng)

Trên máy dev (có internet), chạy `build_portable.bat`. Script tạo:

- `dist\VisionExpo\`: Python nhúng 3.11 + torch CUDA + toàn bộ thư viện + `app/`, `web/`, `models/`, `config/`, màn hình 3D `show/` + `start_show.bat` (khoảng 5 GB). Máy build có Node thì `build_show.bat` được chạy trước để `show/` khớp source `Greco2026DashBoard`; máy đích không cần Node;
- `dist\VisionExpo.zip` (khoảng 3 GB).

Máy đích chỉ cần Windows 10/11 64-bit và driver NVIDIA hỗ trợ CUDA 12.6 (không cần cài Python, không cần internet). Giải nén ở đâu cũng được, chạy `start.bat` hoặc `start_kiosk.bat`. Không có GPU thì app tự chuyển sang CPU.

Tham số: `build_portable.bat -Clean` (build lại Python từ đầu), `-Cpu` (gói nhẹ hơn, chỉ CPU), `-NoShow` (không kèm màn hình 3D). Lần build sau chỉ cập nhật code/model/config, chạy nhanh. Gói chứa `config/settings.json` có mật khẩu RTSP.

## Đóng gói bằng Docker

Chạy trực tiếp trên máy dev: `docker compose up -d --build` rồi mở `http://127.0.0.1:8000/`. `config/` và `data/` được mount từ thư mục này nên cấu hình và database dùng chung với bản chạy bằng `start.bat`.

Đóng gói mang đi: chạy `docker_export.bat` (thêm `-Zip` để nén, `-Cpu` cho image không cần GPU). Kết quả ở `dist\VisionExpo-docker\`:

| File | Nội dung |
|---|---|
| `vision-expo-image.tar` | Image (Python 3.11 + torch CUDA 12.6 + model, chạy offline) |
| `docker-compose.yml` | Chạy container, cổng 8000, bật GPU, `restart: unless-stopped` |
| `config/`, `data/` | Cấu hình camera/line/socket và database SQLite, nằm ngoài container nên không mất khi cập nhật image |
| `install.bat` | `docker load` + `docker compose up -d` (chạy một lần) |
| `start.bat`, `stop.bat`, `logs.bat` | Bật, tắt, xem log |

Máy đích cần Docker Desktop (backend WSL2) và driver NVIDIA mới (GPU trong container đi qua WSL2). Không có GPU thì dùng image `-Cpu`, hoặc xóa khối `deploy:` trong `docker-compose.yml` (app tự chuyển sang CPU). Lưu ý:

- Docker Desktop trên Windows không truy cập được webcam USB; dùng camera RTSP (container ra mạng LAN bình thường). Trên máy Linux có thể map `/dev/video0` (xem chú thích trong `docker-compose.yml`).
- Image chứa sẵn `config/` lúc build làm cấu hình mặc định: lần chạy đầu, file nào chưa có trong thư mục `config/` mount vào sẽ được copy ra.
- Múi giờ mặc định `Asia/Ho_Chi_Minh` (biến `TZ`), dùng cho biểu đồ theo giờ.

## Chạy

| Cách chạy | Dùng khi |
|---|---|
| `start.bat` | Mở server và trình duyệt tại `http://127.0.0.1:8000/` |
| `start.bat --host 0.0.0.0` | Cho tablet/máy khác trong mạng LAN truy cập |
| `start.bat --device cpu` | Máy không có GPU |
| `start_kiosk.bat` | Màn hình trình chiếu: Edge toàn màn hình, lưới camera, ẩn menu |
| `build_show.bat`, `start_show.bat` | Build màn hình 3D `Greco2026DashBoard` vào `show/` (cần Node trên máy dev); mở nó toàn màn hình tại `/show/` |

## Các trang

| Đường dẫn | Chức năng |
|---|---|
| `/` | Live: số người, vào/ra, thiếu PPE; chuyển camera (phím 1–9), lưới (G), tự chuyển (A), kiosk (K), toàn màn hình (F) |
| `/cameras` | Thêm camera (RTSP / webcam / file video), nút Kiểm tra xem trước, bật/tắt, sửa thông số, khởi động lại, xóa |
| `/calibrate/<camera_id>` | Chọn chế độ **Line** (click A, click B, click phía bên trong) hoặc **Vùng** (click lần lượt các góc, ≥ 3 điểm; click lên cạnh để chèn điểm, chuột phải để xóa điểm), kéo để chỉnh, Lưu (áp dụng ngay, không cần khởi động lại) |
| `/ppe` | Cấu hình PPE: bật/tắt, loại cần kiểm tra (mũ / găng), báo thiếu, số lần xác nhận, cách chạy model, ngưỡng, mask người, bật task PPE cho từng camera (tự lưu) |
| `/data` | Database: thống kê, biểu đồ vào/ra/lượt người/cảnh báo theo giờ, nhãn PPE theo lượt người, bảng lượt người và sự kiện (lọc camera, thời gian, nhãn PPE), xuất CSV, cấu hình lưu trữ, xóa dữ liệu |
| `/show/` | Màn hình 3D trình chiếu (Greco2026): người đi trong phòng 3D theo vị trí thật, trạng thái PPE từng người, banner thiếu PPE, khung camera trực tiếp ở góc trái dưới (bấm để phóng to, nút thu nhỏ / Esc để trả lại) |
| `/socket` | WebSocket: cấu hình (bật/tắt, tần suất, topic mặc định, token), danh sách client đang kết nối, màn hình theo dõi message trực tiếp, tài liệu message và code mẫu JS/Python |

Tham số URL cho trang Live: `/?kiosk=1&grid=1`, `/?kiosk=1&auto=1`, `/?cam=<camera_id>`.

## WebSocket

Kết nối `ws://<ip-máy>:8000/ws?topics=status,events&cameras=camera_1&token=...` (bỏ `cameras` để nhận mọi camera, `token` chỉ cần khi đã đặt ở trang `/socket`). Muốn máy khác trong LAN kết nối thì chạy với `--host 0.0.0.0` (bản Docker đã mặc định).

| Topic | Message `type` | Nội dung |
|---|---|---|
| `status` | `status` | Định kỳ (`status_hz`, mặc định 2 lần/giây): số người đang thấy (`persons`), đang trong / ngoài vùng (`inside` / `outside`), tổng lượt vào/ra, số nhãn PPE (`ppe`) và số người thiếu PPE (`ppe_alerts`); tổng và từng camera |
| `people` | `people` | Định kỳ (`people_hz`): từng người với `track_id`, khung và điểm chân (tọa độ chuẩn hóa 0–1), phía trong/ngoài, `ppe_zone`, `ppe_alert` và `ppe` (từng nhãn: `label`, `kind`, `conf`, `alert`, `inferred`, `box`) |
| `events` | `crossing`, `ppe_alert`, `camera`, `reset` | Ngay khi xảy ra: vào/ra (`direction` = `in`/`out`, `mode` = `line`/`zone`; ra khỏi vùng kèm `dwell_s`), thiếu PPE trong vùng (`NO-Hardhat` / `NO-Gloves` khi task `ppe`), camera đổi trạng thái, reset bộ đếm |
| `tracks` | `track` | Khi một người rời khung (3 giây không thấy): thời gian xuất hiện, phía đầu/cuối, nhãn PPE đã thấy, có từng thiếu PPE |

Sau khi kết nối, server gửi `hello` rồi một `status`. Client gửi JSON `{"action": ...}`: `ping`, `subscribe` / `unsubscribe` (`topics`), `set_cameras` (`cameras`), `get_status`.

## Database

SQLite ở `data/exhibition.sqlite3` (chế độ WAL, một luồng ghi riêng nên không làm chậm camera). Bảng `events` (mọi sự kiện như WebSocket), `tracks` (mỗi lượt người một dòng) và `samples` (số liệu từng camera mỗi `sample_every_s` giây). Dữ liệu cũ hơn `retention_days` được tự xóa. Khi bật `restore_counts`, bộ đếm vào/ra được khôi phục từ database sau khi khởi động lại (tính từ lần reset gần nhất). API: `/api/db/events`, `/api/db/tracks`, `/api/db/hourly`, `/api/db/top_items`, `/api/db/export/{events|tracks}.csv`.

## Cách hoạt động

Mỗi camera chạy một luồng riêng: đọc RTSP liên tục và chỉ giữ khung hình mới nhất (không bị trễ dồn), tự kết nối lại khi mất tín hiệu. Mỗi khung được xử lý:

1. YOLO pose + ByteTrack cho ra người kèm mã theo dõi (`#id`) và khung xương.
2. Điểm chân (trung điểm hai mắt cá, nếu không thấy thì lấy đáy khung) được so với đoạn A–B. Khi một người đổi phía và điểm cắt nằm trong đoạn A–B thì tính một lượt vào hoặc ra. Có vùng đệm quanh line và thời gian chờ 1 giây để tránh đếm lặp khi đứng trên line.
   Chế độ vùng: người (theo `zone_rule`: phần lớn thân dưới hoặc điểm chân) đi từ ngoài vào trong polygon là VÀO, từ trong ra ngoài là RA (cùng vùng đệm quanh viền và thời gian chờ). Trang Live hiện số người đang thấy, đang trong vùng và ngoài vùng tại thời điểm hiện tại (chế độ line: phía có điểm I là "trong"); người vừa xuất hiện trên vùng đệm chưa được tính vào phía nào. Người xuất hiện sẵn trong vùng (lúc khởi động, hoặc tracker đổi mã người) được tính là đang ở trong nhưng không tính thêm lượt vào. Nên vẽ vùng chừa khoảng trống với mép khung hình ở phía người đi tới.
3. Nếu camera có vùng bật task `ppe`: kiểm tra mũ / găng cho người trong vùng (chi tiết ở dòng `ppe.*` bảng cấu hình).
4. Vẽ overlay, nén JPEG, phát qua MJPEG cho trình duyệt. Người thiếu PPE được tô đỏ và gắn nhãn PPE, trang Live có ô "Thiếu PPE" nhấp nháy.

## Cấu hình

`config/settings.json` được tạo tự động và được giao diện web ghi lại. Có thể sửa tay khi server tắt.

| Khóa | Ý nghĩa |
|---|---|
| `device` | `"0"` = GPU đầu tiên, `"cpu"` |
| `person_model` | Model phát hiện người, mặc định `yolo26n-pose.pt` (dùng model detect thường cũng được, khi đó lấy đáy khung làm điểm chân) |
| `ppe.enabled`, `ppe.conf`, `ppe.imgsz`, `ppe.every_n` | Model PPE (`yolo_ppe.pt`): bật/tắt, ngưỡng (mặc định 0.25; điểm `NO-Hardhat` thường chỉ 0.3–0.6), kích thước suy luận, chạy mỗi N khung. Sửa nóng ở trang `/ppe` (hoặc `PUT /api/ppe`) |
| `ppe.mode` | `"crop"` (mặc định: cắt khung từng người trong vùng, phóng lên `imgsz`, chạy theo batch) hoặc `"frame"` (một lần trên cả khung: nhanh hơn nhưng đầu/tay người ở xa quá nhỏ, gần như không nhận được) |
| `ppe.person_mask`, `ppe.mask_imgsz` | Mask người cho phép xét găng (mặc định bật, 640). Thiếu file `yolo26n-seg.pt` thì tự tắt, PPE vẫn chạy (xét cả vòng tròn tay) |
| `ppe.infer_missing`, `ppe.require`, `ppe.confirm_runs` | Suy ra PPE (mặc định bật, `["hardhat","gloves"]`, 3; `require: []` = không báo thiếu loại nào). Model `yolo_ppe.pt` gần như mù trên camera triển lãm (cả có lẫn thiếu đều < 0.04), nên mỗi lần chạy PPE app tự xét điểm ảnh của bộ phận lấy từ khung xương: **tay** – tỉ lệ pixel màu da trong vòng tròn ngay sau cổ tay theo hướng cẳng tay (tay trần 54–60 %, găng tối 0–2 %); **đầu** – dải trán/đỉnh đầu trên mắt là tóc/da (đầu trần) hay vỏ sáng/màu (mũ). Có mask người (`yolo26n-seg.pt`) thì chỉ đo pixel thuộc người trong vòng tròn tay (tay trần 84–86 % da, găng 0 %); tay nằm ngoài mask (< 20 % vòng tròn, vd tay trước ghế da) không bị kết luận trần. Mỗi tay có bằng chứng riêng. ≥ N trong 5 lần gần nhất "trần" và không lần nào "có" → `NO-*` (trên đúng tay đó); ≥ N lần "có" → `Hardhat` / `Gloves`. Lần chạy không thấy bộ phận (quay lưng, bị che) không xóa kết quả cũ, nên quay người không mất trạng thái. Box vẽ theo khung xương, `inferred: true`. Giới hạn: găng màu da, mũ đen, người tóc sáng / hói có thể bị đọc sai |
| `cameras[].source` | RTSP URL, số webcam (`0`), hoặc đường dẫn file video (tự lặp) |
| `cameras[].conf`, `imgsz`, `process_fps` | Ngưỡng phát hiện người, kích thước suy luận, FPS xử lý tối đa |
| `cameras[].stream_width`, `jpeg_quality` | Độ phân giải / chất lượng luồng xem |
| `cameras[].crossing` | `mode` (`line` / `zone`); `tasks` (`count`, `ppe` — PPE chỉ vùng); line: `points` + `inward_point`; vùng: `zone` (3–32 điểm), `zone_rule` (`body` = ≥ `zone_overlap` (mặc định 0.5) của 40% dưới khung người nằm trong vùng, mặc định; `box` = như trên nhưng cả khung người; `foot` = điểm chân trong vùng). Đổi chế độ không mất hình của chế độ kia |
| `websocket.enabled`, `status_hz`, `people_hz`, `default_topics`, `token` | WebSocket `/ws` |
| `database.enabled`, `sample_every_s`, `retention_days`, `save_tracks`, `restore_counts` | Database |

File này chứa mật khẩu RTSP, không chia sẻ ra ngoài.

## Gợi ý khi triển lãm

- Camera IP: dùng luồng phụ (Hikvision `.../Channels/102`) nếu nhiều camera hoặc máy yếu; `process_fps` 10–15 là đủ.
- PPE tốn ~20 ms cho model + ~20 ms mask mỗi người trong vùng, dùng chung GPU cho mọi camera: nhiều camera / đông người thì tăng `ppe.every_n` hoặc tắt mask.
- Line đặt trên sàn, vuông góc hướng đi, cách mép khung hình; camera nhìn chéo từ trên xuống cho chân rõ.
- Không có camera thật: thêm một file `.mp4` làm nguồn để demo.
