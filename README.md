# Vision Expo – theo dõi người đa camera cho triển lãm

Ứng dụng web chạy trên máy local: xem trực tiếp nhiều camera, thêm camera RTSP, chỉnh thông số, vẽ line hoặc vùng (polygon) đếm người vào/ra, phát hiện người (YOLO pose + ByteTrack) và phát hiện thêm đồ vật người đi đường mang theo bằng model nhận diện theo từ khóa YOLOE (bật/tắt được).

## Tài liệu

| File | Dành cho |
|---|---|
| [docs/SETUP.md](docs/SETUP.md) | Cài đặt sau khi clone: Windows, Docker, gói portable, mở LAN, xử lý sự cố |
| [docs/WEBSOCKET_API.md](docs/WEBSOCKET_API.md) | Bên làm app nhận dữ liệu: kết nối, định dạng từng message, code mẫu |
| [docs/DASHBOARD_GUIDE.md](docs/DASHBOARD_GUIDE.md) | Người vận hành: cách dùng từng trang của dashboard, chỉnh ngưỡng |
| [docs/CODE.md](docs/CODE.md) | Lập trình viên: kiến trúc, luồng xử lý, thuật toán, cách mở rộng |

## Cài đặt (một lần, cần internet)

Chạy trên chính máy mang đi triển lãm (môi trường Python không copy sang máy khác được):

1. Cài Python 3.10–3.12 (tick "Add to PATH") và Git (để cài CLIP).
2. Double-click `setup.bat`. Script tự:
   - tạo `.venv`, cài PyTorch (bản CUDA nếu có GPU NVIDIA, ngược lại bản CPU) và thư viện;
   - tải `yolo26n-pose.pt` (hoặc copy từ `../Production/models`), `yoloe-26l-seg.pt` và bộ mã hóa chữ `mobileclip2_b.ts`;
   - mã hóa sẵn bộ từ vựng đồ vật vào `models/yoloe_exhibition.pt` (sau bước này chạy offline được).

## Đóng gói mang sang máy khác (bản portable, khuyên dùng)

Trên máy dev (có internet), chạy `build_portable.bat`. Script tạo:

- `dist\VisionExpo\`: Python nhúng 3.11 + torch CUDA + toàn bộ thư viện + CLIP + `app/`, `web/`, `models/`, `config/` (khoảng 5 GB);
- `dist\VisionExpo.zip` (khoảng 3 GB).

Máy đích chỉ cần Windows 10/11 64-bit và driver NVIDIA hỗ trợ CUDA 12.6 (không cần cài Python, không cần internet). Giải nén ở đâu cũng được, chạy `start.bat` hoặc `start_kiosk.bat`. Không có GPU thì app tự chuyển sang CPU.

Tham số: `build_portable.bat -Clean` (build lại Python từ đầu), `-Cpu` (gói nhẹ hơn, chỉ CPU). Lần build sau chỉ cập nhật code/model/config, chạy nhanh. Gói chứa `config/settings.json` có mật khẩu RTSP.

## Đóng gói bằng Docker

Chạy trực tiếp trên máy dev: `docker compose up -d --build` rồi mở `http://127.0.0.1:8000/`. `config/` và `data/` được mount từ thư mục này nên cấu hình và database dùng chung với bản chạy bằng `start.bat`.

Đóng gói mang đi: chạy `docker_export.bat` (thêm `-Zip` để nén, `-Cpu` cho image không cần GPU). Kết quả ở `dist\VisionExpo-docker\`:

| File | Nội dung |
|---|---|
| `vision-expo-image.tar` | Image (Python 3.11 + torch CUDA 12.6 + model + CLIP cache, chạy offline) |
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

## Các trang

| Đường dẫn | Chức năng |
|---|---|
| `/` | Live: số người, vào/ra, đồ vật; chuyển camera (phím 1–9), lưới (G), tự chuyển (A), kiosk (K), toàn màn hình (F) |
| `/cameras` | Thêm camera (RTSP / webcam / file video), nút Kiểm tra xem trước, bật/tắt, sửa thông số, khởi động lại, xóa |
| `/calibrate/<camera_id>` | Chọn chế độ **Line** (click A, click B, click phía bên trong) hoặc **Vùng** (click lần lượt các góc, ≥ 3 điểm; click lên cạnh để chèn điểm, chuột phải để xóa điểm), kéo để chỉnh, Lưu (áp dụng ngay, không cần khởi động lại) |
| `/world` | Bật/tắt nhận diện đồ vật, ngưỡng tin cậy, tần suất chạy, bật/tắt từng nhóm/từng vật, thêm từ mới |
| `/data` | Database: thống kê, biểu đồ vào/ra/lượt người/cảnh báo theo giờ, đồ vật phổ biến, bảng lượt người và sự kiện (lọc camera, thời gian, vật mang theo), xuất CSV, cấu hình lưu trữ, xóa dữ liệu |
| `/socket` | WebSocket: cấu hình (bật/tắt, tần suất, topic mặc định, token), danh sách client đang kết nối, màn hình theo dõi message trực tiếp, tài liệu message và code mẫu JS/Python |

Tham số URL cho trang Live: `/?kiosk=1&grid=1`, `/?kiosk=1&auto=1`, `/?cam=<camera_id>`.

## WebSocket

Kết nối `ws://<ip-máy>:8000/ws?topics=status,events&cameras=camera_1&token=...` (bỏ `cameras` để nhận mọi camera, `token` chỉ cần khi đã đặt ở trang `/socket`). Muốn máy khác trong LAN kết nối thì chạy với `--host 0.0.0.0` (bản Docker đã mặc định).

| Topic | Message `type` | Nội dung |
|---|---|---|
| `status` | `status` | Định kỳ (`status_hz`, mặc định 2 lần/giây): số người đang thấy (`persons`), đang trong / ngoài vùng (`inside` / `outside`), tổng lượt vào/ra, đồ vật, cảnh báo; tổng và từng camera |
| `people` | `people` | Định kỳ (`people_hz`): từng người với `track_id`, khung và điểm chân (tọa độ chuẩn hóa 0–1), phía trong/ngoài, cờ cảnh báo, `items` (danh sách nhãn) và `objects` (từng đồ vật: `label`, `vi`, `category`, `conf`, `alert`, `box`) |
| `events` | `crossing`, `alert`, `ppe_alert`, `camera`, `reset` | Ngay khi xảy ra: vào/ra (`direction` = `in`/`out`, `mode` = `line`/`zone`; ra khỏi vùng kèm `dwell_s`), phát hiện vũ khí, thiếu PPE trong vùng (`NO-Hardhat` / `NO-Gloves` khi task `ppe`), camera đổi trạng thái, reset bộ đếm |
| `tracks` | `track` | Khi một người rời khung (3 giây không thấy): thời gian xuất hiện, phía đầu/cuối, đồ vật mang theo |

Sau khi kết nối, server gửi `hello` rồi một `status`. Client gửi JSON `{"action": ...}`: `ping`, `subscribe` / `unsubscribe` (`topics`), `set_cameras` (`cameras`), `get_status`.

## Database

SQLite ở `data/exhibition.sqlite3` (chế độ WAL, một luồng ghi riêng nên không làm chậm camera). Bảng `events` (mọi sự kiện như WebSocket), `tracks` (mỗi lượt người một dòng) và `samples` (số liệu từng camera mỗi `sample_every_s` giây). Dữ liệu cũ hơn `retention_days` được tự xóa. Khi bật `restore_counts`, bộ đếm vào/ra được khôi phục từ database sau khi khởi động lại (tính từ lần reset gần nhất). API: `/api/db/events`, `/api/db/tracks`, `/api/db/hourly`, `/api/db/top_items`, `/api/db/export/{events|tracks}.csv`.

## Cách hoạt động

Mỗi camera chạy một luồng riêng: đọc RTSP liên tục và chỉ giữ khung hình mới nhất (không bị trễ dồn), tự kết nối lại khi mất tín hiệu. Mỗi khung được xử lý:

1. YOLO pose + ByteTrack cho ra người kèm mã theo dõi (`#id`) và khung xương.
2. Điểm chân (trung điểm hai mắt cá, nếu không thấy thì lấy đáy khung) được so với đoạn A–B. Khi một người đổi phía và điểm cắt nằm trong đoạn A–B thì tính một lượt vào hoặc ra. Có vùng đệm quanh line và thời gian chờ 1 giây để tránh đếm lặp khi đứng trên line.
   Chế độ vùng: điểm chân đi từ ngoài vào trong polygon là VÀO, từ trong ra ngoài là RA (cùng vùng đệm quanh viền và thời gian chờ). Trang Live hiện số người đang thấy, đang trong vùng và ngoài vùng tại thời điểm hiện tại (chế độ line: phía có điểm I là "trong"); người vừa xuất hiện trên vùng đệm chưa được tính vào phía nào. Người xuất hiện sẵn trong vùng (lúc khởi động, hoặc tracker đổi mã người) được tính là đang ở trong nhưng không tính thêm lượt vào. Nên vẽ vùng chừa khoảng trống với mép khung hình ở phía người đi tới.
3. Model đồ vật YOLOE (nếu bật) chạy mỗi N khung trên cả khung hình (model tự thu ảnh về `world.imgsz`), dùng chung một model cho mọi camera. Mỗi camera có một ByteTrack riêng cho đồ vật nên mỗi vật giữ mã theo dõi qua các lần chạy. Sau khi track, vật được gán cho người có khung (nới rộng `person_margin`) bao phủ nó nhiều nhất và di chuyển theo người đó giữa các lần chạy; vật không nằm trên ai thì vẫn được track nhưng không hiện. Có thể chuyển sang hiện mọi vật trong khung (không track) ở trang `/world`.
4. Vẽ overlay, nén JPEG, phát qua MJPEG cho trình duyệt. Người mang vũ khí được tô đỏ và gắn nhãn ALERT, trang Live có ô "Cảnh báo vũ khí" nhấp nháy.

Ổn định nhãn (chế độ đồ vật người mang): mỗi vật được track theo mã của ByteTrack; nếu tracker mất dấu rồi cấp mã mới (vật nhỏ bị nhấc lên và di chuyển nhanh), vật được nối lại với vật cũ cùng vị trí trên người đó. Vật chỉ hiện khi thấy ít nhất `confirm_hits` trong 5 lần chạy gần nhất và chỉ ẩn khi `drop_misses` lần liên tiếp không thấy. Nhãn chọn bằng bỏ phiếu theo độ tin cậy qua nhiều lần chạy, chỉ đổi khi nhãn mới vượt hẳn (không nhảy jacket ↔ blazer), khung được làm mượt. Vũ khí cần độ tin cậy `alert_conf` và thấy ít nhất `alert_hits` trong 5 lần mới cảnh báo. Vật nằm yên khi không ai mang (balo dưới sàn, túi trên bàn) hoặc đứng yên trên ảnh trong khi người di chuyển bị coi là đồ của khung cảnh và không hiện, kể cả khi sau đó có người đứng cạnh; khi vật được nhấc lên và di chuyển thì hiện lại. Vật đã nằm sẵn cạnh người ngồi yên từ đầu thì vẫn không phân biệt được.

Nhiều cách đọc trên một vùng: các box chồng nhau trong cùng một lần chạy (ví dụ "hat" và "hair" trên đầu, "knife" và "cell phone" trong tay) được coi là các cách đọc của cùng một vật và cùng bỏ phiếu theo nhóm. Vật chỉ hiện khi nhóm dẫn đầu có tổng phiếu gấp ít nhất `label_margin` lần nhóm đứng sau (mặc định 1.5×); chưa rõ ràng thì chưa hiện. Nếu nhóm dẫn đầu là nhóm ẩn (tóc, quần áo) thì vùng đó không hiện gì. Các nhãn cùng nhóm không cạnh tranh nhau.

Bộ từ vựng nằm ở `config/vocabulary.json`, từ khóa bằng tiếng Anh (cột tiếng Việt chỉ để hiển thị). Mỗi loại vật chỉ một nhãn (`hat` cũng bắt được mũ lưỡi trai, mũ len…): mỗi nhãn gần nghĩa thêm vào là thêm một cách để model khớp nhầm. Mặc định có 17 nhãn phụ kiện và vũ khí (hat, helmet, glasses, face mask, backpack, handbag, shopping bag, suitcase, watch, headphones, lanyard, cell phone, knife, gun, baseball bat, axe, scissors) và 5 nhãn ẩn (jacket, shirt, trousers, shoes, hair). Mỗi nhãn có danh sách `prompts` (mặc định là chính nhãn đó); nhiều prompt thì vector chữ được lấy trung bình. Đo trên camera thật, danh từ thông dụng nhất một mình thường tốt nhất ("glasses" một mình bắt được kính, trộn thêm "eyeglasses" / "sunglasses" thì mất), nên chỉ thêm prompt khi đã thử thấy tốt hơn. File cũ (mỗi từ đồng nghĩa một nhãn) tự chuyển sang bộ mới khi khởi động, giữ lại các từ tự thêm. Nhóm có `"alert": true` (vũ khí) được coi là cảnh báo. Nhóm có `"suppress": true` vẫn được model nhận diện nhưng không bao giờ hiển thị, ghi database hay gửi socket: chúng chỉ dùng để đè các nhãn phụ kiện nhận nhầm cùng vị trí (thân áo bị đoán là "backpack", tóc nhìn từ trên xuống bị đoán là "helmet"). Tắt nhóm này trên trang `/world` thì mất tác dụng lọc đó. Bật/tắt từ chỉ là lọc kết quả nên áp dụng ngay. Thêm từ mới thì cần mã hóa lại (vài giây, dùng `models/mobileclip2_b.ts` đã tải lúc setup).

Model: `yoloe-26l-seg.pt` (YOLOE, thế hệ sau của YOLO-World). Đo trên 30 khung hình văn phòng, so với YOLO-World s 68 từ ở imgsz 640 (bản trước): nhận đúng 169 vật so với 38, nhãn sai 4 so với 2, bắt được kính, tai nghe, điện thoại mà bản trước gần như không thấy; đổi lại mỗi lần chạy mất ~50 ms (imgsz 960) thay vì ~21 ms. Muốn quay lại YOLO-World thì đổi `WORLD_BASE_MODEL` trong `app/paths.py` thành `yolov8s-worldv2.pt` (code hỗ trợ cả hai).

## Cấu hình

`config/settings.json` được tạo tự động và được giao diện web ghi lại. Có thể sửa tay khi server tắt.

| Khóa | Ý nghĩa |
|---|---|
| `device` | `"0"` = GPU đầu tiên, `"cpu"` |
| `person_model` | Model phát hiện người, mặc định `yolo26n-pose.pt` (dùng model detect thường cũng được, khi đó lấy đáy khung làm điểm chân) |
| `world.enabled`, `world.conf`, `world.every_n`, `world.disabled_classes` | Nhận diện đồ vật: bật/tắt, ngưỡng chung, chạy mỗi N khung, nhãn tắt |
| `world.scope` | `"person"` (mặc định: track vật trên toàn khung rồi chỉ giữ vật người mang) hoặc `"frame"` (hiện mọi vật trong khung, không track) |
| `world.person_margin`, `world.min_person_px` | Nới rộng khung người khi gán vật (mặc định 0.1), người có khung thấp hơn N px (mặc định 60) không được gán vật |
| `world.imgsz` | Kích thước ảnh đưa vào model đồ vật (mặc định 640). Ảnh cả khung 1920×1080 bị thu nhỏ khoảng 3 lần, vật nhỏ (kính, điện thoại) dễ bị bỏ sót: nên dùng 960 nếu có GPU (~50 ms/lần với YOLOE-26l) |
| `world.alert_conf` | Ngưỡng tin cậy riêng cho nhóm cảnh báo (vũ khí), mặc định 0.45; `world.conf` (0.3) cho các đồ vật khác |
| `world.category_conf` | Ngưỡng riêng theo nhóm, ví dụ `{"accessories": 0.6}` (mặc định: phụ kiện như đồng hồ, tai nghe, thẻ đeo cần 0.6); `null` = dùng ngưỡng chung. Chỉnh ở ô "ngưỡng" của từng nhóm trên trang `/world` |
| `world.show_conf` | Ngưỡng hiển thị (mặc định 0.6): vật có độ tin cậy (đã làm mượt) dưới mức này vẫn được theo dõi nhưng không vẽ lên khung hình, không tính vào thống kê / cảnh báo. 0 = hiện tất cả |
| `world.label_margin` | Độ chênh tối thiểu giữa các cách đọc của cùng một vùng (mặc định 1.5): nhóm dẫn đầu phải có phiếu ≥ N × nhóm đứng sau mới hiện; 1 = tắt |
| `world.min_obj_px` | Bỏ box đồ vật có cạnh ngắn nhỏ hơn N px (mặc định 14): vật quá nhỏ trên ảnh thường là nhận nhầm |
| `world.confirm_hits`, `world.alert_hits`, `world.drop_misses` | Hiện đồ vật khi thấy ≥ N/5 lần chạy (2), cảnh báo vũ khí khi ≥ N/5 (3), ẩn sau N lần liên tiếp không thấy (3) |
| `world.reject_static` | Bỏ vật nằm yên khi không ai mang, hoặc đứng yên khi người di chuyển |
| `ppe.enabled`, `ppe.conf`, `ppe.imgsz`, `ppe.every_n` | Model PPE (`yolo_ppe.pt`): bật/tắt, ngưỡng (mặc định 0.25; điểm `NO-Hardhat` thường chỉ 0.3–0.6), kích thước suy luận, chạy mỗi N khung. Sửa nóng qua `PUT /api/ppe` |
| `ppe.mode` | `"crop"` (mặc định: cắt khung từng người trong vùng, phóng lên `imgsz`, chạy theo batch) hoặc `"frame"` (một lần trên cả khung: nhanh hơn nhưng đầu/tay người ở xa quá nhỏ, gần như không nhận được) |
| `ppe.infer_missing`, `ppe.require`, `ppe.confirm_runs` | Suy ra PPE (mặc định bật, `["hardhat","gloves"]`, 3). Model `yolo_ppe.pt` gần như mù trên camera triển lãm (cả có lẫn thiếu đều < 0.04), nên mỗi lần chạy PPE app tự xét điểm ảnh của bộ phận lấy từ khung xương: **tay** – tỉ lệ pixel màu da (tay trần 25–30 %, găng tối 0–7 %); **đầu** – dải trán/đỉnh đầu trên mắt là tóc/da (đầu trần) hay vỏ sáng/màu (mũ). ≥ N trong 5 lần gần nhất "trần" và không lần nào "có" → `NO-*`; ≥ N lần "có" → `Hardhat` / `Gloves`. Box vẽ theo khung xương, `inferred: true`. Giới hạn: găng màu da, mũ đen, người tóc sáng / hói có thể bị đọc sai |
| `cameras[].source` | RTSP URL, số webcam (`0`), hoặc đường dẫn file video (tự lặp) |
| `cameras[].conf`, `imgsz`, `process_fps` | Ngưỡng phát hiện người, kích thước suy luận, FPS xử lý tối đa |
| `cameras[].stream_width`, `jpeg_quality` | Độ phân giải / chất lượng luồng xem |
| `cameras[].crossing` | `mode` (`line` / `zone`); `tasks` (`count`, `ppe` — PPE chỉ vùng); line: `points` + `inward_point`; vùng: `zone` (3–32 điểm). Đổi chế độ không mất hình của chế độ kia |
| `websocket.enabled`, `status_hz`, `people_hz`, `default_topics`, `token` | WebSocket `/ws` |
| `database.enabled`, `sample_every_s`, `retention_days`, `save_tracks`, `restore_counts` | Database |

File này chứa mật khẩu RTSP, không chia sẻ ra ngoài.

## Gợi ý khi triển lãm

- Camera IP: dùng luồng phụ (Hikvision `.../Channels/102`) nếu nhiều camera hoặc máy yếu; `process_fps` 10–15 là đủ.
- Model đồ vật YOLOE-26l tốn ~40–50 ms mỗi lần chạy, dùng chung GPU cho mọi camera: nhiều camera thì tăng `every_n` (4–6) hoặc giảm `imgsz`. Máy không có GPU thì YOLOE-26l rất chậm; đổi `WORLD_BASE_MODEL` sang `yoloe-26s-seg.pt`.
- Line đặt trên sàn, vuông góc hướng đi, cách mép khung hình; camera nhìn chéo từ trên xuống cho chân rõ.
- Không có camera thật: thêm một file `.mp4` làm nguồn để demo.
