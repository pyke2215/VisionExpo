# Cài đặt sau khi clone

Tài liệu này dành cho người vừa clone repo và cần cho app chạy được. Có 3 cách, chọn một:

| Cách | Khi nào dùng | Máy cần có |
|---|---|---|
| **A. Chạy trực tiếp trên Windows** | Máy dev, sửa code, máy triển lãm có cài Python | Windows 10/11, Python 3.10–3.12, Git, (khuyên) GPU NVIDIA |
| **B. Docker** | Máy chủ cố định, muốn cài một lệnh, tự khởi động lại | Docker Desktop (WSL2) hoặc Docker trên Linux, (khuyên) GPU NVIDIA |
| **C. Gói portable** | Mang sang máy triển lãm không có internet, không cài gì | Chỉ Windows 10/11 + driver NVIDIA (bản build từ cách A) |

Mọi lệnh bên dưới chạy trong thư mục `Exhibition/`.

## Những thứ KHÔNG có trong git

`.gitignore` loại các file nặng hoặc có thông tin riêng, nên sau khi clone sẽ thiếu:

| Đường dẫn | Là gì | Được tạo bởi |
|---|---|---|
| `models/*.pt` | Model người `yolo26n-pose.pt` (8 MB), model đồ vật gốc `yoloe-26l-seg.pt` (~70 MB), từ vựng đã mã hóa `yoloe_exhibition.pt` + chữ ký `yoloe_exhibition.json`, PPE `yolo_ppe.pt` (Hardhat/Gloves) | `setup.bat` / `docker compose build` tải YOLO*; `yolo_ppe.pt` copy tay vào `models/` |
| `models/mobileclip2_b.ts` | Bộ mã hóa chữ MobileCLIP (~240 MB), chỉ cần khi thêm / đổi từ vựng | Như trên |
| `config/settings.json` | Danh sách camera (có mật khẩu RTSP), line/vùng, mọi thông số | App tự tạo lần chạy đầu, sau đó chỉnh trên web |
| `data/` | Database SQLite | App tự tạo |
| `.venv/`, `dist/` | Môi trường Python, bản đóng gói | `setup.bat`, `build_portable.bat` |

`config/vocabulary.json` (bộ từ khóa đồ vật) có trong git.

---

## A. Chạy trực tiếp trên Windows

### 1. Cài phần mềm nền (một lần)

1. **Python 3.11** (3.10–3.12 đều được) từ https://www.python.org/downloads/ – tick **"Add python.exe to PATH"**.
2. **Git** từ https://git-scm.com/ (để cài CLIP).
3. Có GPU NVIDIA: cài driver mới (hỗ trợ CUDA 12.6 trở lên). Kiểm tra bằng `nvidia-smi` trong terminal.

### 2. Cài app (một lần, cần internet, 5–15 phút)

Double-click `setup.bat`. Script sẽ:

1. Tạo `.venv`.
2. Cài PyTorch: bản CUDA 12.6 nếu `nvidia-smi` chạy được, ngược lại bản CPU.
3. Cài thư viện trong `requirements.txt`.
4. Cài CLIP (nếu lỗi do thiếu Git thì app vẫn chạy, chỉ không thêm được từ mới).
5. Chạy `tools/prepare_models.py`: tải model vào `models/`, mã hóa bộ từ vựng thành `models/yoloe_exhibition.pt`.
6. Tạo `config/settings.json` mặc định (máy không có GPU thì đặt `device = "cpu"`).

Sau bước này app chạy được offline.

### 3. Chạy

```bat
start.bat                      :: mở http://127.0.0.1:8000/ trong trình duyệt
start.bat --host 0.0.0.0       :: cho máy khác trong LAN (tablet, app hiển thị) truy cập
start.bat --port 8080          :: đổi cổng
start.bat --device cpu         :: ép chạy CPU
start_kiosk.bat                :: màn hình trình chiếu (Edge toàn màn hình, lưới camera)
```

Hoặc từ terminal: `.venv\Scripts\python.exe -m app --host 0.0.0.0 --port 8000`.

### 4. Cập nhật code

```bat
git pull
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Nếu `config/vocabulary.json` thay đổi, app tự mã hóa lại khi khởi động (cần CLIP đã cài và `models/mobileclip2_b.ts`). File `vocabulary.json` kiểu cũ (mỗi từ đồng nghĩa một nhãn) tự chuyển sang bộ nhãn gộp, giữ các từ tự thêm.

---

## B. Docker

### 1. Chuẩn bị máy

**Windows:**

1. Bật ảo hóa: mở PowerShell **Run as Administrator**:
   ```powershell
   wsl --install --no-distribution
   ```
   rồi **khởi động lại máy**. (Nếu Docker báo `HCS_E_HYPERV_NOT_INSTALLED` hoặc "Virtual Machine Platform" chưa bật thì là thiếu bước này. Cũng cần bật Virtualization/VT-x trong BIOS.)
2. Cài **Docker Desktop** (https://www.docker.com/products/docker-desktop/), chọn backend WSL2.
3. Có GPU NVIDIA: cài driver NVIDIA bản mới cho Windows. GPU tự đi vào container qua WSL2, không cần cài CUDA riêng. Kiểm tra:
   ```powershell
   docker run --rm --gpus all nvidia/cuda:12.6.0-base-ubuntu22.04 nvidia-smi
   ```

**Linux:** cài Docker Engine + Compose plugin, và (nếu có GPU) driver NVIDIA + NVIDIA Container Toolkit.

### 2. Build và chạy (lần đầu cần internet, 10–20 phút)

```bash
docker compose up -d --build
```

Lần build đầu tiên tự tải PyTorch CUDA (~2.5 GB), model và CLIP, rồi mã hóa bộ từ vựng ngay trong image. Mở http://127.0.0.1:8000/ khi `docker compose ps` báo `healthy`.

- `config/` và `data/` của thư mục này được mount vào container, nên cấu hình và database không mất khi build lại image, và dùng chung với cách A.
- Lần chạy đầu, file nào trong `config/` còn thiếu sẽ được copy từ bản mặc định trong image.
- Container lắng nghe `0.0.0.0:8000`, máy khác trong LAN truy cập được ngay.

Lệnh thường dùng:

```bash
docker compose logs -f            # xem log
docker compose restart            # khởi động lại
docker compose down               # tắt
git pull && docker compose up -d --build   # cập nhật code
```

**Không có GPU:** xóa khối `deploy:` trong `docker-compose.yml` (app tự chuyển sang CPU), hoặc build image CPU nhẹ hơn:

```bash
docker build --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cpu -t vision-expo:latest .
docker compose up -d
```

**Webcam USB:** Docker Desktop trên Windows không truy cập được webcam USB, hãy dùng camera RTSP. Trên Linux bỏ chú thích khối `devices:` trong `docker-compose.yml`.

### 3. Đóng gói image mang sang máy khác (không cần internet ở máy đích)

```bat
docker_export.bat          :: build + xuất ra dist\VisionExpo-docker\
docker_export.bat -Zip     :: thêm file .zip
docker_export.bat -Cpu     :: image chỉ CPU
docker_export.bat -NoBuild :: dùng image đã build sẵn
```

Copy thư mục `dist\VisionExpo-docker\` sang máy đích (đã cài Docker Desktop), chạy `install.bat` một lần. Sau đó dùng `start.bat`, `stop.bat`, `logs.bat`.

Lưu ý: image chứa bản `config/` lúc build (kể cả `settings.json` có mật khẩu RTSP nếu máy build có file này).

---

## C. Gói portable (Windows, không cần cài gì ở máy đích)

Trên máy đã làm xong cách A và có internet:

```bat
build_portable.bat          :: tạo dist\VisionExpo\ (~5 GB) và dist\VisionExpo.zip (~3 GB)
build_portable.bat -Clean   :: build lại Python nhúng từ đầu
build_portable.bat -Cpu     :: gói nhẹ, chỉ CPU
```

Giải nén `VisionExpo.zip` ở máy đích, chạy `start.bat` hoặc `start_kiosk.bat`. Máy đích chỉ cần driver NVIDIA (CUDA 12.6), không có GPU thì tự chạy CPU.

---

## Sau khi app chạy lần đầu

Cấu hình mặc định chỉ có một camera "Webcam" và nhận diện đồ vật đang **tắt**. Làm lần lượt (chi tiết ở [DASHBOARD_GUIDE.md](DASHBOARD_GUIDE.md)):

1. **Cameras** → thêm camera RTSP (ví dụ Hikvision `rtsp://user:pass@192.168.1.10:554/Streaming/Channels/101`), bấm **Kiểm tra** để xem trước rồi **Thêm**.
2. **Line / Vùng** → vẽ line hoặc vùng đếm cho từng camera, **Lưu**.
3. **Đồ vật** → bấm nút bật, chỉnh ngưỡng nếu cần (có GPU thì đặt imgsz 960).
4. **Socket** → nếu có app khác nhận dữ liệu: đặt token (tuỳ chọn), copy URL. Định dạng dữ liệu: [WEBSOCKET_API.md](WEBSOCKET_API.md).

## Cho máy khác trong LAN truy cập

1. Chạy với `--host 0.0.0.0` (Docker đã mặc định).
2. Mở cổng trên Windows Firewall (PowerShell Administrator):
   ```powershell
   New-NetFirewallRule -DisplayName "Vision Expo 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
   ```
3. Máy khác dùng `http://<IP-máy-chủ>:8000/` và `ws://<IP-máy-chủ>:8000/ws`. Xem IP bằng `ipconfig`.

## Sao lưu

Chỉ cần giữ `config/` (camera, line, thông số, từ vựng) và `data/exhibition.sqlite3` (lịch sử). Copy hai thư mục này sang bản cài mới là có lại toàn bộ.

## Xử lý sự cố

| Hiện tượng | Nguyên nhân / cách xử lý |
|---|---|
| `start.bat` báo "Chua cai dat" | Chưa chạy `setup.bat`, hoặc `.venv` bị xóa |
| Log `CUDA not available, falling back ... to cpu` | Cài nhầm PyTorch CPU hoặc driver cũ. Chạy `.venv\Scripts\python.exe -c "import torch; print(torch.cuda.is_available())"`; nếu `False` thì cài lại torch: `pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126 --force-reinstall` |
| Camera "offline" / "reconnecting" | Sai URL RTSP, sai mật khẩu, khác mạng. Thử URL bằng VLC. Nhiều camera hoặc máy yếu thì dùng luồng phụ (`.../Channels/102`) |
| Đồ vật "error" | Thiếu `models/yoloe_exhibition.pt` (hoặc từ vựng đã đổi) và không có `yoloe-26l-seg.pt` / `mobileclip2_b.ts` / internet để tạo lại. Chạy `.venv\Scripts\python.exe tools\prepare_models.py` |
| Thêm từ mới báo lỗi rebuild | Chưa cài CLIP (thiếu Git lúc setup). Cài Git rồi `.venv\Scripts\python.exe -m pip install git+https://github.com/ultralytics/CLIP.git` |
| `[Errno 10048]` / cổng đang dùng | Một server khác đang chạy cổng 8000. Tắt nó hoặc dùng `--port 8080` |
| Docker: `HCS_E_HYPERV_NOT_INSTALLED`, lệnh `docker` treo | Chưa bật Virtual Machine Platform: làm bước B.1 rồi khởi động lại máy |
| Docker: `could not select device driver "nvidia"` | Máy không có GPU / chưa có NVIDIA Container Toolkit. Xóa khối `deploy:` trong `docker-compose.yml` |
| Cấu hình trên web không giữ lại | Server đang chạy code cũ không hiểu khóa mới: tắt và mở lại server sau khi cập nhật code |
| Sửa tay `config/settings.json` bị ghi đè | Server ghi lại file mỗi khi lưu trên web. Tắt server trước khi sửa tay |
