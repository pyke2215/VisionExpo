# Vision Expo – Dữ liệu gửi qua WebSocket

Tài liệu cho bên làm **app hiển thị / app nhận dữ liệu**. Vision Expo xử lý camera (đếm người vào / ra, kiểm tra PPE: mũ bảo hộ + găng tay trong vùng) và đẩy kết quả qua một WebSocket JSON. Dashboard web của Vision Expo chỉ để vận hành; màn hình chính do app của bạn hiển thị.

- Giao thức: WebSocket, mỗi message là **một object JSON** (UTF-8, text frame).
- Server → client: dữ liệu định kỳ + sự kiện. Client → server: lệnh điều khiển nhỏ (tuỳ chọn).
- Thời gian `ts`: Unix timestamp **giây** (số thực, đồng hồ máy chạy Vision Expo).
- Tọa độ: **chuẩn hóa 0–1** theo khung hình gốc của camera (`frame_size`), gốc ở góc trên trái.
- Phiên bản giao thức hiện tại: `1.0.0` (trường `version` trong `hello`). Trường mới có thể được **thêm** vào sau; client nên bỏ qua trường lạ.

## 1. Kết nối

```
ws://<IP-máy-Vision-Expo>:8000/ws?topics=status,people,events&cameras=camera_1,camera_2&token=XXXX
```

| Tham số | Bắt buộc | Ý nghĩa |
|---|---|---|
| `topics` | không | Các chủ đề muốn nhận, cách nhau dấu phẩy: `status`, `people`, `events`, `tracks`. Bỏ trống = mặc định của server (thường `status,events`). **Màn hình hiển thị người / PPE cần `people`.** |
| `cameras` | không | Chỉ nhận dữ liệu của các camera này (id, cách nhau dấu phẩy). Bỏ trống = mọi camera |
| `token` | khi server có đặt | Token đặt ở trang `/socket` của dashboard. Sai token → server đóng kết nối mã `1008` |

Máy Vision Expo phải chạy với `--host 0.0.0.0` (bản Docker đã mặc định) và mở cổng 8000 trên firewall. Danh sách camera id: xem message `hello`, hoặc trang Cameras của dashboard.

### Trình tự

1. Client kết nối.
2. Server gửi `hello` (một lần).
3. Nếu có đăng ký `status`: server gửi ngay một `status`.
4. Sau đó: `status` và `people` theo chu kỳ, `events` / `tracks` khi xảy ra.

### Mã đóng kết nối

| Mã | Lý do |
|---|---|
| `1008` | Sai token |
| `1013` | WebSocket đang bị tắt trên server (thử lại sau) |
| `1001` | Server tắt / khởi động lại |
| khác / mất kết nối | Mạng, hoặc client nhận quá chậm (một lần gửi quá 2 giây thì server ngắt client đó) |

Client nên **tự kết nối lại** (chờ 1, 2, 4… tối đa 30 giây). Sự kiện xảy ra trong lúc mất kết nối không được gửi lại; nếu cần đủ dữ liệu, lấy bù qua REST (mục 6).

## 2. Chủ đề và tần suất

| Topic | Message `type` | Khi nào gửi | Mặc định |
|---|---|---|---|
| `status` | `status` | Định kỳ | 2 lần/giây (`status_hz`) |
| `people` | `people` | Định kỳ | 5 lần/giây (`people_hz`) |
| `events` | `crossing`, `ppe_alert`, `camera`, `reset` | Ngay khi xảy ra | – |
| `tracks` | `track` | Khi một người rời khung (3 giây không thấy) | – |

Tần suất chỉnh ở trang `/socket` (0–30 lần/giây, 0 = tắt). Vị trí người được cập nhật theo tốc độ xử lý camera (~15 khung/giây), PPE khoảng 7 lần/giây, nên `people_hz` trên 15 không có thêm thông tin.

## 3. Message server → client

### 3.1 `hello`

```json
{"type":"hello","client_id":3,"version":"1.0.0","server_time":1791877285.9,
 "topics":["events","people","status"],
 "available_topics":["status","people","events","tracks"],
 "cameras":[{"id":"camera_1","name":"Cổng chính","enabled":true},
            {"id":"camera_2","name":"Sảnh","enabled":false}]}
```

| Trường | Kiểu | Ý nghĩa |
|---|---|---|
| `client_id` | int | Id kết nối (hiện ở trang `/socket`) |
| `server_time` | float | Giờ server, dùng để bù lệch đồng hồ nếu cần |
| `topics` | string[] | Các topic client này đang nhận |
| `cameras` | object[] | Toàn bộ camera đã cấu hình (`enabled` = đang bật) |

### 3.2 `status` – số liệu tổng quan

```json
{"type":"status","ts":1791877285.9,
 "totals":{"persons":3,"inside":2,"outside":1,"entries":42,"exits":38,"ppe_alerts":1},
 "ppe_required":["hardhat","gloves"],
 "cameras":[{"id":"camera_1","name":"Cổng chính","status":"running","fps":14.9,
             "persons":3,"inside":2,"outside":1,"mode":"zone","entries":42,"exits":38,
             "ppe":{"NO-Hardhat":1,"Hardhat":1,"Gloves":3,"NO-Gloves":1},"ppe_alerts":1}]}
```

Mỗi camera:

| Trường | Kiểu | Ý nghĩa |
|---|---|---|
| `id`, `name` | string | Mã và tên camera |
| `status` | string | `running` (đang xử lý bình thường); `starting`, `loading model`, `connecting` (đang khởi động); `reconnecting`, `offline`, `error` (có sự cố); `disabled` (tắt trong cấu hình), `stopped` |
| `fps` | float \| null | Tốc độ xử lý hiện tại |
| `persons` | int | Số người **đang thấy** trong khung |
| `inside` | int \| null | Số người đang ở **trong vùng** (chế độ line: phía có điểm "trong"). `null` khi camera chưa vẽ line/vùng |
| `outside` | int \| null | Số người đang ở **ngoài vùng**. `null` như trên |
| `mode` | string | `"zone"` (vùng polygon) hoặc `"line"` |
| `entries`, `exits` | int | Tổng lượt vào / ra kể từ lần reset bộ đếm gần nhất |
| `ppe` | object | Số nhãn PPE đang gắn người trong vùng: `{nhãn: số lượng}`. Găng tay tính **theo từng tay** (một người có thể góp 2) |
| `ppe_alerts` | int | Số người đang thiếu PPE (có ít nhất một nhãn `NO-*`) |

`persons − inside − outside` = người vừa xuất hiện, chưa xác định được phía. `totals` là tổng các camera đang chạy (cùng ý nghĩa).
`ppe_required` = các loại PPE server đang kiểm tra (`hardhat`, `gloves`; rỗng khi PPE tắt). Một loại có trong danh sách mà người trong vùng chưa có nhãn nào nghĩa là "chưa rõ" (chưa thấy rõ bộ phận đó đủ số lần).

### 3.3 `people` – từng người và PPE (dữ liệu chính để hiển thị)

```json
{"type":"people","ts":1791877286.1,
 "cameras":[{"id":"camera_1","frame_size":[1920,1080],
   "people":[{"track_id":27,"side":"in","ppe_zone":true,"ppe_alert":true,
              "ppe":[{"label":"NO-Hardhat","kind":"hardhat","conf":0.8,"alert":true,"inferred":true,
                      "box":[0.42,0.28,0.50,0.36]},
                     {"label":"Gloves","kind":"gloves","conf":1.0,"alert":false,"inferred":true,
                      "box":[0.44,0.55,0.49,0.62]},
                     {"label":"NO-Gloves","kind":"gloves","conf":0.6,"alert":true,"inferred":true,
                      "box":[0.52,0.56,0.56,0.63]}],
              "conf":0.88,"box":[0.41,0.30,0.52,0.86],"foot":[0.47,0.85]}]}]}
```

Chỉ gồm camera đang chạy. Mỗi camera: `id`, `frame_size` (`[rộng, cao]` pixel của khung gốc), `people` (có thể rỗng).

Mỗi người:

| Trường | Kiểu | Ý nghĩa |
|---|---|---|
| `track_id` | int \| null | Mã theo dõi, giữ nguyên khi người đó còn trong khung. Duy nhất **trong một camera**, không liên kết giữa các camera; có thể đổi khi bị che khuất lâu; đánh lại từ đầu khi server khởi động lại. `null` hiếm gặp (chưa kịp gán) |
| `side` | string | `"in"` (trong vùng), `"out"` (ngoài vùng), `"unknown"` (chưa xác định / camera chưa vẽ line-vùng) |
| `ppe_zone` | bool | Đang được kiểm tra PPE: chân trong vùng, hoặc thân trong vùng, hoặc vừa ra khỏi vùng dưới 2 giây |
| `ppe_alert` | bool | Đang thiếu PPE (có nhãn `NO-Hardhat` / `NO-Gloves`) |
| `ppe` | object[] | Kết quả PPE của người này (bảng dưới; rỗng khi không ở vùng có task `ppe`) |
| `conf` | float | Độ tin cậy phát hiện người (0–1) |
| `box` | float[4] | Khung người `[x1, y1, x2, y2]`, chuẩn hóa 0–1 |
| `foot` | float[2] | Điểm chân `[x, y]` (dùng để xét trong/ngoài vùng) |

Mỗi phần tử trong `ppe`:

| Trường | Kiểu | Ý nghĩa |
|---|---|---|
| `label` | string | `Hardhat`, `NO-Hardhat`, `Gloves`, `NO-Gloves` |
| `kind` | string | `"hardhat"` hoặc `"gloves"` |
| `conf` | float | Box của model: độ tin cậy model. Suy ra: tỉ lệ số lần thấy trong 5 lần kiểm tra gần nhất (0–1) |
| `alert` | bool | `true` với nhãn `NO-*` (thiếu đồ bảo hộ) |
| `inferred` | bool | `true` = suy ra từ khung xương + màu điểm ảnh của đầu / tay qua nhiều lần kiểm tra, `box` lấy từ khung xương; `false` = box của model |
| `box` | float[4] | Khung `[x1,y1,x2,y2]` chuẩn hóa 0–1 |

Găng tay được xét **từng tay**: một người có thể có hai mục `gloves` (ví dụ một `Gloves` + một `NO-Gloves`). Một bộ phận chỉ được báo sau khi trông như vậy ở đủ số lần kiểm tra (`ppe.confirm_runs` trên 5), nên người vừa vào vùng có thể có `ppe` rỗng trong 1–2 giây. Dương và âm đều gửi; chỉ âm đặt `ppe_alert` / sự kiện `ppe_alert`.

Đổi sang pixel: `x_px = x * frame_size[0]`, `y_px = y * frame_size[1]`.

### 3.4 Sự kiện (`events`)

Mọi sự kiện có `type`, `ts`, `camera_id`.

**`crossing` – một lượt vào hoặc ra**

```json
{"type":"crossing","ts":1791877290.4,"camera_id":"camera_1","track_id":27,"direction":"in","mode":"zone"}
{"type":"crossing","ts":1791877332.9,"camera_id":"camera_1","track_id":27,"direction":"out","mode":"zone","dwell_s":42.5}
```

| Trường | Ý nghĩa |
|---|---|
| `direction` | `"in"` hoặc `"out"` |
| `mode` | `"line"` (qua đoạn thẳng) hoặc `"zone"` (vào/ra polygon) |
| `dwell_s` | Chỉ có khi ra khỏi vùng: số giây đã ở trong vùng |

Người đã đứng sẵn trong vùng lúc xuất hiện (mới khởi động, hoặc bị đổi `track_id`) được tính là đang ở trong (`side = "in"`) nhưng không phát sinh sự kiện vào.

**`ppe_alert` – thiếu PPE trong vùng**

```json
{"type":"ppe_alert","ts":1791877296.0,"camera_id":"camera_1","track_id":27,
 "items":["NO-Hardhat"],"all_items":["NO-Hardhat","Gloves"]}
```

Chỉ khi vùng polygon bật task `ppe`. `items` = các nhãn âm hiện có (`NO-Hardhat`, `NO-Gloves`); `all_items` = mọi nhãn PPE đang gắn người đó. Lặp tối đa khoảng **8 giây một lần** cho cùng tổ hợp / người. Trạng thái tức thời: `people[].ppe_alert` và `people[].ppe`.

**`camera` – camera đổi trạng thái**

```json
{"type":"camera","ts":1791877300.0,"camera_id":"camera_2","status":"offline","error":"stream stalled"}
```

`status`: `online`, `offline`, `reconnecting`, `error`. `error` là mô tả lỗi hoặc `null`.

**`reset` – bộ đếm vào/ra của camera vừa được reset**

```json
{"type":"reset","ts":1791877400.0,"camera_id":"camera_1"}
```

### 3.5 `track` – tóm tắt một lượt người (topic `tracks`)

Gửi khi một người không còn thấy trong 3 giây. Lượt quá ngắn (dưới 5 khung) bị bỏ.

```json
{"type":"track","ts":1791877312.4,"camera_id":"camera_1","track_id":27,
 "first_ts":1791877300.0,"last_ts":1791877312.4,"duration":12.4,"frames":180,
 "items":{"NO-Hardhat":120,"Gloves":64},"alert":true,"first_side":"out","last_side":"in"}
```

| Trường | Ý nghĩa |
|---|---|
| `first_ts`, `last_ts`, `duration` | Lần đầu / cuối thấy, thời lượng (giây) |
| `frames` | Số khung hình đã thấy người này |
| `items` | `{nhãn PPE: số khung có nhãn đó}`, chỉ nhãn thấy ≥ 3 khung |
| `alert` | Có lúc nào thiếu PPE (`NO-*`) |
| `first_side`, `last_side` | Phía lúc đầu / lúc cuối (`in`, `out` hoặc `null`) |

### 3.6 Trả lời lệnh của client

| `type` | Khi nào |
|---|---|
| `pong` | Trả lời `ping`: `{"type":"pong","ts":...}` |
| `subscribed` | Sau `subscribe` / `unsubscribe`: `{"type":"subscribed","topics":[...]}` |
| `cameras_set` | Sau `set_cameras`: `{"type":"cameras_set","cameras":[...] \| null}` |
| `status` | Trả lời `get_status` (cùng định dạng 3.2) |
| `error` | Lệnh sai: `{"type":"error","error":"unknown action: ..."}` |

## 4. Lệnh client → server (tuỳ chọn)

```json
{"action":"ping"}
{"action":"subscribe","topics":["people","tracks"]}
{"action":"unsubscribe","topics":["status"]}
{"action":"set_cameras","cameras":["camera_1"]}
{"action":"set_cameras","cameras":null}
{"action":"get_status"}
```

`set_cameras` với `null` hoặc `[]` = nhận mọi camera.

## 5. Thay đổi so với bản có nhận diện đồ vật

Nhận diện đồ vật (YOLOE + CLIP) đã được gỡ tạm. Client viết cho bản cũ cần biết:

- `people[]` không còn `alert`, `items`, `objects`; `status` không còn `objects`, `alerts` (cả trong `totals`).
- Sự kiện `alert` (vũ khí) không còn được gửi; dùng `ppe_alert`.
- Sự kiện `track`: `items` / `alert` nay là nhãn PPE / thiếu PPE.
- Trường mới: `people[].ppe_zone`, `status.ppe_required`.

## 6. REST API dùng kèm

Cùng host/cổng, trả JSON (không cần token).

| Đường dẫn | Dùng để |
|---|---|
| `GET /api/status` | Ảnh chụp trạng thái hiện tại (giống `status` nhưng chi tiết hơn) |
| `GET /api/cameras` | Danh sách camera (có cả URL nguồn, **chứa mật khẩu RTSP**) |
| `GET /api/cameras/{id}/stream.mjpg` | Video MJPEG đã vẽ khung người / PPE / vùng (dùng trực tiếp trong `<img src>`) |
| `GET /api/cameras/{id}/frame.jpg` | Ảnh JPEG mới nhất đã vẽ khung |
| `GET /api/cameras/{id}/snapshot.jpg` | Ảnh JPEG gốc mới nhất (không vẽ) |
| `GET /api/db/events?from=&to=&camera_id=&types=crossing,ppe_alert&limit=&offset=` | Sự kiện đã lưu (lấy bù khi mất kết nối). `from`/`to` là Unix giây |
| `GET /api/db/tracks?from=&to=&camera_id=&item=&alert_only=` | Lượt người đã lưu |
| `GET /api/db/hourly`, `GET /api/db/top_items` | Thống kê theo giờ, nhãn PPE theo lượt người |
| `GET /api/ppe` | Cấu hình + trạng thái model PPE (`required`, `status`, `last_infer_ms`…) |

**Video cho app hiển thị:** muốn tự vẽ khung lên video, lấy video trực tiếp từ camera (RTSP) rồi vẽ `people[].box` / `people[].ppe[].box`. Dữ liệu trễ hơn hình khoảng 100–300 ms tuỳ máy. Muốn đơn giản thì nhúng `stream.mjpg` (đã vẽ sẵn).

## 7. Gợi ý dùng dữ liệu

| Cần hiển thị | Lấy từ |
|---|---|
| Đang thấy / trong vùng / ngoài vùng | `status.cameras[].persons / inside / outside` (hoặc `totals`) |
| Khung người | `people` |
| PPE từng người (mũ / găng từng tay) | `people[].ppe`; "chưa rõ" = loại trong `status.ppe_required` chưa có nhãn |
| Cảnh báo thiếu PPE | Tức thời: `people[].ppe_alert`; thông báo/âm thanh: sự kiện `ppe_alert` |
| Tổng mũ / găng trong vùng | `status.cameras[].ppe`, `ppe_alerts` |
| Bảng "vừa vào / vừa ra" | Sự kiện `crossing` |
| Thống kê sau khi người rời đi | Sự kiện `track` (topic `tracks`) |
| Camera mất tín hiệu | Sự kiện `camera`, hoặc `status.cameras[].status` |

## 8. Code mẫu

### JavaScript (trình duyệt hoặc Node 22+)

```js
const URL = "ws://192.168.1.50:8000/ws?topics=status,people,events";
let retry = 1000;

function connect() {
  const ws = new WebSocket(URL);
  ws.onopen = () => { retry = 1000; };
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    switch (m.type) {
      case "status":
        for (const c of m.cameras) console.log(c.id, "thấy", c.persons, "trong", c.inside, "ngoài", c.outside);
        break;
      case "people":
        for (const cam of m.cameras)
          for (const p of cam.people)
            console.log(cam.id, `#${p.track_id}`, p.side, p.ppe.map((f) => f.label).join(", "));
        break;
      case "crossing":
        console.log(m.camera_id, `#${m.track_id}`, m.direction === "in" ? "VÀO" : "RA");
        break;
      case "ppe_alert":
        console.warn("THIẾU PPE", m.camera_id, `#${m.track_id}`, m.items);
        break;
    }
  };
  ws.onclose = () => { setTimeout(connect, retry); retry = Math.min(retry * 2, 30000); };
}
connect();
```

### Python (`pip install websockets`)

```python
import asyncio, json, websockets

URL = "ws://192.168.1.50:8000/ws?topics=status,people,events"

async def main():
    retry = 1
    while True:
        try:
            async with websockets.connect(URL) as ws:
                retry = 1
                async for raw in ws:
                    m = json.loads(raw)
                    if m["type"] == "status":
                        t = m["totals"]
                        print("thấy", t["persons"], "trong", t["inside"], "ngoài", t["outside"])
                    elif m["type"] == "people":
                        for cam in m["cameras"]:
                            for p in cam["people"]:
                                print(cam["id"], p["track_id"], p["side"], [f["label"] for f in p["ppe"]])
                    elif m["type"] in ("crossing", "ppe_alert"):
                        print(m)
        except (OSError, websockets.ConnectionClosed):
            await asyncio.sleep(retry)
            retry = min(retry * 2, 30)

asyncio.run(main())
```

## 9. Kiểm tra nhanh

Trang `http://<IP>:8000/socket` của dashboard có khung **"Xem message trực tiếp"**: chọn topic, bấm **Kết nối** để xem đúng message app sẽ nhận, click một dòng để xem JSON đầy đủ. Trang này cũng liệt kê các client đang kết nối (app của bạn sẽ hiện ở đó).
