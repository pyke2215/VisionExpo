# Hướng dẫn dùng dashboard

Dashboard mở tại `http://127.0.0.1:8000/` (máy khác trong LAN: `http://<IP-máy>:8000/`). Thanh menu trên cùng có 6 trang:

| Menu | Đường dẫn | Dùng để |
|---|---|---|
| Live | `/` | Xem camera trực tiếp, số người, đồ vật, cảnh báo |
| Cameras | `/cameras` | Thêm / sửa / tắt / xóa camera |
| Line / Vùng | `/calibrate` | Vẽ line hoặc vùng để biết người ở trong hay ngoài, đếm lượt vào/ra |
| YOLO-World | `/world` | Bật nhận diện đồ vật, chỉnh ngưỡng, chọn từ khóa |
| Dữ liệu | `/data` | Lịch sử, biểu đồ, xuất CSV, cấu hình lưu trữ |
| Socket | `/socket` | Cấu hình WebSocket gửi dữ liệu cho app khác, theo dõi client |

**Lưu thay đổi:** trang YOLO-World **tự lưu** ngay khi chỉnh (hiện thông báo "Đã lưu"). Các trang khác có nút **Lưu**. Mọi thay đổi áp dụng ngay, không cần khởi động lại app.

---

## Lần đầu sử dụng

1. **Cameras** → thêm từng camera, bấm **Kiểm tra** thấy ảnh rồi **Thêm**.
2. **Line / Vùng** → chọn camera, vẽ vùng (hoặc line), **Lưu**.
3. **YOLO-World** → bấm **Đang TẮT – bấm để bật**.
4. **Live** → kiểm tra số người, khung, đồ vật.
5. (Nếu có app hiển thị) **Socket** → đặt token, gửi URL cho bên làm app.

---

## Live (`/`)

**Hàng số liệu trên cùng** (tổng các camera đang chạy):

| Ô | Ý nghĩa |
|---|---|
| Đang thấy | Số người đang có trong khung hình lúc này |
| Trong vùng | Số người đang đứng trong vùng (chế độ line: ở phía "trong") |
| Ngoài vùng | Số người đang ở ngoài vùng |
| Đồ vật | Số đồ vật đang hiển thị |
| Cảnh báo vũ khí | Số người đang mang vật thuộc nhóm vũ khí, ô nhấp nháy đỏ khi > 0 |

Người vừa xuất hiện ngay trên mép vùng chưa được tính vào trong hay ngoài, nên "Trong" + "Ngoài" có thể nhỏ hơn "Đang thấy" một chút.

**Danh sách camera bên trái:** trạng thái (chấm xanh = chạy), FPS, số người, trong/ngoài, số cảnh báo. Click để xem camera đó.

**Khung hình:** khung người màu theo phía (xanh = trong, cam = ngoài, xám = chưa xác định), `#id` là mã theo dõi, đồ vật hiện kèm nhãn và độ tin cậy. Người mang vũ khí tô đỏ, nhãn ALERT. Góc trên trái: tên camera, FPS, số người, `INSIDE / OUTSIDE`.

**Cột bên phải:**
- *Người trong khung*: mã, tag TRONG / NGOÀI / ?, đồ vật mang theo (tiếng Việt).
- *Sự kiện vào / ra*: 12 lượt gần nhất.
- *Đồ vật phát hiện*: đếm theo loại.

**Nút và phím tắt:**

| Nút | Phím | Chức năng |
|---|---|---|
| (chọn camera) | `1`–`9` | Xem camera thứ N |
| Lưới | `G` | Xem tất cả camera cùng lúc |
| Tự chuyển | `A` | Tự đổi camera mỗi 10 giây |
| Kiosk | `K` / `Esc` | Ẩn menu và cột phụ (màn hình trình chiếu) |
| Toàn màn hình | `F` | Fullscreen trình duyệt |
| Reset đếm | – | Đưa tổng lượt vào/ra của mọi camera về 0 (không ảnh hưởng số đang thấy / trong / ngoài) |

**Mở sẵn chế độ qua URL** (dùng cho màn hình treo tường, `start_kiosk.bat` dùng cách này):
`/?kiosk=1&grid=1` (kiosk + lưới), `/?kiosk=1&auto=1` (kiosk + tự chuyển), `/?cam=camera_1` (mở camera cụ thể).

---

## Cameras (`/cameras`)

### Thêm camera

1. **Tên hiển thị**: tên tuỳ ý (ví dụ "Cổng chính").
2. **Loại nguồn** và **Nguồn**:
   - RTSP: `rtsp://user:matkhau@192.168.1.10:554/Streaming/Channels/101` (Hikvision; `102` là luồng phụ, nhẹ hơn). Dahua: `rtsp://user:matkhau@IP:554/cam/realmonitor?channel=1&subtype=0`.
   - Webcam: số thứ tự `0`, `1`…
   - File video (demo): đường dẫn `.mp4`, tự phát lặp.
3. **Kiểm tra**: hiện ảnh xem trước. Không thấy ảnh thì sai URL / mật khẩu / mạng.
4. **Thêm**: camera chạy ngay.

### Danh sách camera

Mỗi camera có nút **Đang bật / Đang tắt**, **Sửa**, **Line** (mở trang vẽ line/vùng), **Khởi động lại**, **Xóa**.

### Sửa camera

| Thông số | Ý nghĩa | Gợi ý |
|---|---|---|
| Ngưỡng tin cậy người (conf) | Độ chắc chắn tối thiểu để tính là một người | 0.25–0.4. Thấp: bắt người ở xa/bị che tốt hơn nhưng dễ nhận nhầm |
| Kích thước suy luận (imgsz) | Ảnh đưa vào model | 640. Camera rộng, người nhỏ: 960 (chậm hơn) |
| FPS xử lý tối đa | Số khung xử lý mỗi giây | 10–15 |
| Độ rộng luồng xem (px) | Độ phân giải video xem trên web | 1280 |
| Chất lượng JPEG | Chất lượng video xem trên web | 70–80 |
| Bật camera | Tắt = ngừng đọc camera | |
| Phát hiện người | Tắt = chỉ xem hình | |
| Vẽ khung / Vẽ khung xương | Hiển thị trên video | |
| YOLO-World | Nhận diện đồ vật trên camera này | |
| Đếm vào/ra | Dùng line/vùng của camera này | |

Đổi **Nguồn** hoặc **Bật camera** sẽ khởi động lại camera; các thông số khác áp dụng ngay.

---

## Line / Vùng (`/calibrate`)

Chọn camera ở ô trên cùng, chọn chế độ:

### Chế độ Vùng (polygon) – khuyên dùng

Dùng khi muốn biết người đang **ở trong một khu vực** (khu trưng bày, quầy, sân khấu).

1. Click lần lượt các góc của vùng trên ảnh (ít nhất 3 điểm, tối đa 32).
2. Chỉnh: **kéo** điểm để di chuyển, **click lên cạnh** để chèn thêm điểm, **chuột phải** vào điểm để xóa.
3. **Lưu**.

Người được xét theo **điểm chân** (giữa hai mắt cá). Đi từ ngoài vào trong vùng = một lượt VÀO, ra ngoài = một lượt RA (kèm thời gian đã ở trong).

### Chế độ Line

Dùng cho cửa ra vào hẹp, lối đi.

1. Click điểm **A**, click điểm **B** (line nằm trên sàn, cắt ngang lối đi).
2. Click vào phía được coi là **bên trong** (điểm I).
3. **Lưu**. Nút **Đảo chiều VÀO/RA** đổi phía trong/ngoài.

### Nút khác

| Nút | Chức năng |
|---|---|
| Chụp lại khung hình | Lấy ảnh mới để vẽ (khi có người che) |
| Ảnh live | Xem ảnh đang chạy với overlay |
| Vẽ lại | Xóa hình đang vẽ |
| Bật đếm | Tắt = không xét trong/ngoài, không đếm |
| Reset đếm | Đưa lượt vào/ra của camera này về 0 |

Khung **"Bộ đếm camera này"** hiện số đang thấy / trong / ngoài theo thời gian thực, dùng để kiểm tra ngay sau khi lưu. Đổi chế độ line ↔ vùng không mất hình đã vẽ của chế độ kia.

**Mẹo vẽ:** camera nhìn chéo từ trên xuống để thấy rõ chân; vùng nên cách mép khung hình một khoảng ở phía người đi tới (để người được nhận ra trước khi bước vào vùng); tránh để vùng sát chỗ người hay bị che khuất (sau bàn, quầy).

---

## YOLO-World (`/world`) – nhận diện đồ vật

Các thay đổi trên trang này **tự lưu**.

### Bật / tắt và hiệu năng

| Mục | Ý nghĩa | Gợi ý |
|---|---|---|
| Nút Đang BẬT / TẮT | Bật nhận diện đồ vật cho toàn hệ thống | |
| Chạy mỗi N khung hình | Quét đồ vật mỗi N khung (khung người vẫn cập nhật mỗi khung) | 3. Máy yếu / nhiều camera: 4–6 |
| imgsz | Kích thước ảnh cho model đồ vật | 640 |
| Phạm vi tìm | **Trong người** (chỉ tìm trong khung từng người – chính xác hơn, khuyên dùng) hoặc **Toàn khung** | Trong người |
| Nới rộng crop (%) | Mở rộng khung người khi tìm để bắt vật cầm tay | 10 |
| Tối đa người / khung | Giới hạn số người được quét mỗi lần | 8 |
| Camera dùng YOLO-World | Bật/tắt riêng từng camera | |

### Ngưỡng

| Mục | Ý nghĩa | Mặc định |
|---|---|---|
| Ngưỡng tin cậy đồ vật | Model phải chắc ít nhất mức này mới tính là đã thấy vật | 0.3 |
| Ngưỡng vũ khí | Như trên, riêng nhóm vũ khí | 0.45 |
| Ngưỡng hiển thị | Vật đã theo dõi nhưng độ tin cậy dưới mức này thì không vẽ, không gửi đi | 0.6 |
| Ô "ngưỡng" cạnh từng nhóm | Ngưỡng riêng cho nhóm đó (để trống = dùng ngưỡng chung) | Phụ kiện 0.6 |

### Làm mượt và chống nhận nhầm

| Mục | Ý nghĩa | Mặc định |
|---|---|---|
| Hiện đồ vật khi thấy ≥ N / 5 lần chạy | Vật phải xuất hiện đủ số lần mới hiện (chống nhấp nháy) | 2 |
| Cảnh báo vũ khí khi thấy ≥ N / 5 lần | Như trên, riêng vũ khí (chặt hơn để tránh báo động giả) | 3 |
| Ẩn sau N lần liên tiếp không thấy | Vật biến mất bao lâu thì ẩn | 3 |
| Độ chênh tối thiểu (×) | Một vùng có nhiều cách đọc (nón / tóc, dao / điện thoại): cách đọc dẫn đầu phải hơn cách đọc thứ hai N lần mới hiện. 1 = tắt | 1.5 |
| Bỏ vật nhỏ hơn (px) | Vật quá nhỏ trên ảnh thường là nhận nhầm | 14 |
| Bỏ vật đứng yên khi người di chuyển | Loại đồ trên bàn, kệ, móc treo bị gán nhầm cho người đi ngang | Bật |

### Từ vựng

- Click một từ để bật/tắt (từ tắt hiển thị mờ). Tick ô nhóm để bật/tắt cả nhóm. **Bật tất cả / Tắt tất cả**. Ô tìm kiếm để lọc.
- Nhóm **Quần áo & tóc** không bao giờ hiển thị: model vẫn nhận diện để loại các nhận nhầm (thân áo bị đoán là ba lô, tóc bị đoán là nón). Không nên tắt nhóm này.
- **Thêm từ mới**: mỗi dòng một từ tiếng Anh, có thể kèm tiếng Việt sau dấu `|`, ví dụ `umbrella|ô dù`. Bấm **Thêm & build lại** (vài giây). Từ khóa nên là danh từ tiếng Anh ngắn, cụ thể. Từ tự thêm nằm trong nhóm "Tự thêm", có nút × để xóa.

### Khi nào chỉnh gì

| Hiện tượng | Thử |
|---|---|
| Vật nhỏ bị nhận nhầm (vòng tay, tai nghe…) | Tăng ô ngưỡng của nhóm Phụ kiện, tăng "Bỏ vật nhỏ hơn" |
| Tóc bị nhận là nón / mũ | Tăng "Độ chênh tối thiểu" lên 2, hoặc tăng ngưỡng nhóm Nón & mũ lên 0.5 |
| Đồ vật thật không hiện | Giảm "Ngưỡng hiển thị", giảm "Hiện khi thấy ≥ N" xuống 1 |
| Nhãn nhảy liên tục | Tăng "Hiện khi thấy ≥ N", tăng "Độ chênh tối thiểu" |
| Báo động vũ khí giả | Tăng "Ngưỡng vũ khí" và "Cảnh báo khi thấy ≥ N"; tắt từ hay bị nhầm (scissors, hammer…) |
| Đồ trên bàn bị gán cho người ngồi cạnh | Giới hạn đã biết: người ngồi yên thì không phân biệt được; giảm "Nới rộng crop" |
| FPS thấp | Tăng "Chạy mỗi N khung", giảm imgsz, giảm "Tối đa người / khung" |

---

## Dữ liệu (`/data`)

- **Bộ lọc**: camera, khoảng thời gian (Từ / Đến) hoặc nút nhanh **Hôm nay / 24 giờ / 7 ngày**, bấm **Xem**.
- **Thẻ tổng quan** (toàn bộ database): dung lượng, số lượt người và lượt có cảnh báo đã lưu, số sự kiện vào/ra và cảnh báo, bản ghi đầu tiên, hàng đợi ghi.
- **Theo giờ** (theo bộ lọc): tổng Vào / Ra / Lượt người / Cảnh báo và biểu đồ theo từng giờ.
- **Đồ vật xuất hiện nhiều nhất**: số lượt người mang mỗi loại. Click một vật để lọc bảng lượt người theo vật đó.
- **Bảng Lượt người**: mỗi người một dòng (thời gian, thời lượng, phía đầu/cuối, đồ vật). Lọc "Chỉ người có cảnh báo", "Mang theo …". 50 dòng/trang.
- **Bảng Sự kiện**: vào/ra, cảnh báo, camera mất tín hiệu, reset.
- **Xuất CSV**: tải bảng đang lọc (mở được bằng Excel, tiếng Việt hiển thị đúng).
- **Cấu hình lưu trữ** (bấm **Lưu**): Ghi dữ liệu; Lưu lượt người; Khôi phục bộ đếm khi khởi động lại; Lấy mẫu số liệu mỗi N giây; Giữ dữ liệu N ngày (0 = mãi mãi, dữ liệu cũ hơn tự xóa).
- **Xóa toàn bộ dữ liệu**: phải gõ `DELETE` để xác nhận, không khôi phục được.

---

## Socket (`/socket`)

Trang cho bên vận hành kết nối app hiển thị. Định dạng dữ liệu chi tiết: [WEBSOCKET_API.md](WEBSOCKET_API.md).

- **Cấu hình WebSocket** (bấm **Lưu cấu hình**): Bật WebSocket; tần suất gửi `status` và `people` (lần/giây); chủ đề mặc định cho client không chỉ định; **Token** (để trống = không cần; nút **Tạo ngẫu nhiên**).
- **Địa chỉ kết nối**: URL để copy gửi cho bên làm app. Máy khác trong LAN thay `127.0.0.1` bằng IP máy này (app phải chạy với `--host 0.0.0.0`).
- **Đã gửi theo chủ đề**: số message đã gửi.
- **Client đang kết nối**: địa chỉ, chủ đề, camera, thời gian kết nối, số message gửi/nhận.
- **Xem message trực tiếp**: chọn chủ đề và camera, **Kết nối** để xem đúng dữ liệu app nhận; **Tạm dừng**, **Xóa**; click một dòng để xem JSON đầy đủ.
- Cuối trang: định dạng message và code mẫu JavaScript / Python.
