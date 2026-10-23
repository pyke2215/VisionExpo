# Hướng dẫn dùng dashboard

Dashboard mở tại `http://127.0.0.1:8000/` (máy khác trong LAN: `http://<IP-máy>:8000/`). Thanh menu trên cùng có 6 trang:

| Menu | Đường dẫn | Dùng để |
|---|---|---|
| Live | `/` | Xem camera trực tiếp, số người, trạng thái PPE |
| Cameras | `/cameras` | Thêm / sửa / tắt / xóa camera |
| Line / Task | `/calibrate` | Vẽ line hoặc vùng; chọn task đếm vào/ra và/hoặc PPE trong vùng |
| PPE | `/ppe` | Bật kiểm tra mũ bảo hộ / găng tay, chọn loại cần kiểm tra, ngưỡng, bật task PPE cho camera |
| Dữ liệu | `/data` | Lịch sử, biểu đồ, xuất CSV, cấu hình lưu trữ |
| Socket | `/socket` | Cấu hình WebSocket gửi dữ liệu cho app khác, theo dõi client |

Màn hình trình chiếu 3D (Greco2026) mở riêng ở `/show/`, không nằm trên thanh menu (xem cuối trang).

Nhận diện đồ vật (trang Đồ vật cũ) đang gỡ tạm.

**Lưu thay đổi:** trang PPE **tự lưu** ngay khi chỉnh (hiện thông báo "Đã lưu"). Các trang khác có nút **Lưu**. Mọi thay đổi áp dụng ngay, không cần khởi động lại app.

---

## Lần đầu sử dụng

1. **Cameras** → thêm từng camera, bấm **Kiểm tra** thấy ảnh rồi **Thêm**.
2. **Line / Task** → chọn camera, vẽ vùng (hoặc line), chọn task đếm/PPE nếu cần, **Lưu**.
3. **PPE** → kiểm tra trạng thái "Sẵn sàng", tick camera cần kiểm tra PPE.
4. **Live** → kiểm tra số người, khung, nhãn PPE.
5. (Nếu có app hiển thị) **Socket** → đặt token, gửi URL cho bên làm app.

---

## Live (`/`)

**Hàng số liệu trên cùng** (tổng các camera đang chạy):

| Ô | Ý nghĩa |
|---|---|
| Đang thấy | Số người đang có trong khung hình lúc này |
| Trong vùng | Số người đang đứng trong vùng (chế độ line: ở phía "trong") |
| Ngoài vùng | Số người đang ở ngoài vùng |
| Thiếu PPE | Số người trong vùng PPE đang thiếu mũ hoặc găng, ô nhấp nháy đỏ khi > 0 |

Người vừa xuất hiện ngay trên mép vùng chưa được tính vào trong hay ngoài, nên "Trong" + "Ngoài" có thể nhỏ hơn "Đang thấy" một chút.

**Danh sách camera bên trái:** trạng thái (chấm xanh = chạy), FPS, số người, trong/ngoài, số người thiếu PPE. Click để xem camera đó.

**Khung hình:** khung người màu theo phía (xanh = trong, cam = ngoài, xám = chưa xác định), `#id` là mã theo dõi. Trong vùng PPE: khung xanh `Hardhat` / `Gloves`, khung đỏ `NO-Hardhat` / `NO-Gloves` (kèm độ tin cậy); người thiếu PPE tô đỏ, nhãn PPE. Góc trên trái: tên camera, FPS, số người, `INSIDE / OUTSIDE`.

**Cột bên phải:**
- *Người trong khung*: mã, tag TRONG / NGOÀI / ?, Có mũ / Thiếu mũ / Có găng / Thiếu găng.
- *PPE trong vùng*: đếm theo nhãn (găng tính từng tay).
- *Sự kiện vào / ra*: 12 lượt gần nhất.

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
| Đếm vào/ra | Dùng line/vùng của camera này | |

Đổi **Nguồn** hoặc **Bật camera** sẽ khởi động lại camera; các thông số khác áp dụng ngay.

---

## Line / vùng & task (`/calibrate`)

Chọn camera ở ô trên cùng, chọn chế độ:

### Chế độ Vùng (polygon) – khuyên dùng

Dùng khi muốn biết người đang **ở trong một khu vực** (khu trưng bày, quầy, sân khấu), và/hoặc kiểm tra PPE trong vùng đó.

1. Click lần lượt các góc của vùng trên ảnh (ít nhất 3 điểm, tối đa 32).
2. Chỉnh: **kéo** điểm để di chuyển, **click lên cạnh** để chèn thêm điểm, **chuột phải** vào điểm để xóa.
3. Chọn task: **Đếm vào/ra** và/hoặc **PPE (mũ + găng)**.
4. **Lưu**.

Mục **Tính là TRONG vùng khi** chọn cách xét: *phần lớn thân dưới nằm trong vùng* (mặc định: ≥ N% của 40% dưới khung người nằm trong vùng; hợp với người ngồi, bị khung hình cắt chân, hoặc chỉ thò một phần ra ngoài) *phần lớn cả người nằm trong vùng* (≥ N% khung người; hợp khi vùng phủ gần hết khung hình, không chỉ sàn) hoặc *điểm chân nằm trong vùng* (giữa hai mắt cá; chính xác hơn khi luôn thấy rõ chân). Đi từ ngoài vào trong vùng = một lượt VÀO (nếu bật đếm), ra ngoài = một lượt RA (kèm thời gian đã ở trong).

**PPE:** model `yolo_ppe.pt` nhận `Hardhat` / `NO-Hardhat` / `Gloves` / `NO-Gloves` trên người đang **trong vùng**. Dương và âm đều hiện trên Live; chỉ `NO-*` tô đỏ và gửi sự kiện `ppe_alert`. Có thể bật PPE mà không bật đếm (vẫn tính trong/ngoài để lọc). Người "trong vùng" xét theo điểm chân (hoặc tâm thân), nên mũ trên đầu nằm ngoài polygon vẫn được tính. Model chạy trên ảnh cắt của từng người trong vùng (`ppe.mode = "crop"`); người cao dưới 48 px bị bỏ qua.

### Chế độ Line

Dùng cho cửa ra vào hẹp, lối đi. Chỉ task **Đếm vào/ra** (không có PPE).

1. Click điểm **A**, click điểm **B** (line nằm trên sàn, cắt ngang lối đi).
2. Click vào phía được coi là **bên trong** (điểm I).
3. **Lưu**. Nút **Đảo chiều VÀO/RA** đổi phía trong/ngoài.

### Nút khác

| Nút | Chức năng |
|---|---|
| Chụp lại khung hình | Lấy ảnh mới để vẽ (khi có người che) |
| Ảnh live | Xem ảnh đang chạy với overlay |
| Vẽ lại | Xóa hình đang vẽ |
| Đếm vào/ra | Task `count`: phát sự kiện vào/ra |
| PPE (mũ + găng) | Task `ppe` (chỉ vùng): kiểm tra Hardhat/Gloves trong polygon |
| Reset đếm | Đưa lượt vào/ra của camera này về 0 |

Khung **"Bộ đếm camera này"** hiện số đang thấy / trong / ngoài theo thời gian thực, dùng để kiểm tra ngay sau khi lưu. Đổi chế độ line ↔ vùng không mất hình đã vẽ của chế độ kia.

**Mẹo vẽ:** camera nhìn chéo từ trên xuống để thấy rõ chân; vùng nên cách mép khung hình một khoảng ở phía người đi tới (để người được nhận ra trước khi bước vào vùng); tránh để vùng sát chỗ người hay bị che khuất (sau bàn, quầy).

---

## PPE (`/ppe`) – mũ bảo hộ và găng tay

Các thay đổi trên trang này **tự lưu**. Chỉ người đang ở trong vùng polygon có bật task PPE mới được kiểm tra.

| Mục | Ý nghĩa | Gợi ý |
|---|---|---|
| Nút Đang BẬT / TẮT | Bật kiểm tra PPE cho toàn hệ thống | |
| Mũ bảo hộ / Găng tay | Loại nào được báo "Thiếu" (găng xét từng tay) | Cả hai |
| Báo thiếu (NO-*) | Tắt: chỉ hiện những gì model tìm được, không bao giờ báo thiếu | Bật (model gần như không thấy PPE trên camera triển lãm) |
| Xác nhận khi ≥ N / 5 lần | Bộ phận phải trông như vậy ở ≥ N trong 5 lần kiểm tra gần nhất mới báo Có / Thiếu. Cao = ít nhấp nháy, chậm hơn | 3 |
| Cách chạy | **Crop từng người** (chính xác với người ở xa) hoặc **Cả khung hình** (nhanh khi đông người) | Crop |
| Ngưỡng tin cậy, imgsz | Của model `yolo_ppe.pt` | 0.25, 640 |
| Chạy mỗi N khung hình | Kiểm tra PPE mỗi N khung; giữa hai lần kết quả cũ được giữ | 2. Máy yếu: 3–4 |
| Chỉ đo điểm ảnh của người | Dùng mask người (`yolo26n-seg.pt`) để ghế / bàn màu da sau tay không bị tính là tay trần | Bật |
| Camera có task PPE | Bật / tắt task PPE trên vùng đã vẽ; camera chưa có vùng thì bấm **Vẽ vùng** | |

| Hiện tượng | Thử |
|---|---|
| Nhãn nhảy Có ↔ Thiếu | Tăng "Xác nhận khi ≥ N" lên 4 |
| Người vào vùng lâu mới có nhãn | Giảm "Xác nhận khi ≥ N" xuống 2, giảm "Chạy mỗi N khung" |
| Tay trước ghế / bàn màu nâu bị báo thiếu găng | Bật "Chỉ đo điểm ảnh của người" |
| Người đứng sát camera không được kiểm tra | Vùng chưa phủ tới mép dưới khung hình: vẽ rộng vùng ở trang Line / Task |
| FPS thấp | Tăng "Chạy mỗi N khung", chuyển sang "Cả khung hình" |

Giới hạn đã biết: găng màu da, mũ đen, người tóc sáng / hói có thể bị đọc sai; ánh sáng yếu làm khó phân biệt da.

---

## Dữ liệu (`/data`)

- **Bộ lọc**: camera, khoảng thời gian (Từ / Đến) hoặc nút nhanh **Hôm nay / 24 giờ / 7 ngày**, bấm **Xem**.
- **Thẻ tổng quan** (toàn bộ database): dung lượng, số lượt người và lượt thiếu PPE đã lưu, số sự kiện vào/ra và cảnh báo PPE, bản ghi đầu tiên, hàng đợi ghi.
- **Theo giờ** (theo bộ lọc): tổng Vào / Ra / Lượt người / Cảnh báo và biểu đồ theo từng giờ.
- **PPE theo lượt người**: số lượt người có mỗi nhãn PPE. Click một nhãn để lọc bảng lượt người theo nhãn đó.
- **Bảng Lượt người**: mỗi người một dòng (thời gian, thời lượng, phía đầu/cuối, nhãn PPE, THIẾU PPE). Lọc "Chỉ người thiếu PPE", "Nhãn PPE …". 50 dòng/trang.
- **Bảng Sự kiện**: vào/ra, cảnh báo PPE, camera mất tín hiệu, reset (cảnh báo vũ khí cũ vẫn xem được).
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

---

## Màn hình 3D (`/show/`)

Màn hình trình chiếu cho khách xem (project `Greco2026DashBoard`, build bằng `build_show.bat`, mở toàn màn hình bằng `start_show.bat`).

- **Người trong khung** (trái trên): mỗi người một dòng với Có mũ / Thiếu mũ / Có găng 2 tay / Thiếu găng 1 tay…; "chưa rõ" = đang trong vùng nhưng chưa đủ số lần xác nhận. Click để xem chi tiết từng nhãn (model hay suy ra từ hình, độ tin cậy).
- **Tổng quan** (phải): đang thấy, trong / ngoài vùng, số người đang kiểm tra PPE, số người thiếu PPE, đếm từng nhãn.
- **Camera** (trái dưới): video trực tiếp của camera đang chọn. Bấm vào hình hoặc nút ⤢ để phóng to giữa màn hình; nút thu nhỏ, phím `Esc` hoặc bấm ra ngoài để trả về góc.
- Banner đỏ + tiếng bíp khi có người thiếu PPE (nút loa để tắt tiếng). Nút **Test** chạy dữ liệu giả, không cần server (không có hình camera).
