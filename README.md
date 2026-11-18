# Công cụ tự động hóa dự án gán nhãn "Tóm tắt – Biên soạn" văn bản tiếng Việt (`tomtat`)

Công cụ chạy local trên Windows giúp tự động hóa quy trình hoàn thiện các file `.docx` của dự án gán nhãn **Tóm tắt – Biên soạn** văn bản tiếng Việt bằng Gemini API, hỗ trợ chạy hàng loạt bằng dòng lệnh và có giao diện web local (`127.0.0.1`) để người gán nhãn duyệt, chỉnh sửa, kiểm tra cơ học thời gian thực trước khi xuất file `.docx` cuối cùng.

---

## 1. Cấu trúc thư mục dự án

```text
D:\TomTatBienSoan\
├─ config.yaml              # Cấu hình model bậc thang, số luồng song song, ngưỡng kiểm tra...
├─ api_keys.example.txt     # File mẫu hướng dẫn dán danh sách API Keys
├─ api_keys.txt             # Chứa 1 đến 40+ API Keys thật (mỗi dòng 1 key, được .gitignore bảo vệ)
├─ requirements.txt         # Danh sách thư viện Python
├─ setup.bat                # Tạo môi trường ảo .venv, cài đặt thư viện và mở api_keys.txt
├─ khoi_dong.bat            # Tự động cập nhật bản mới nhất và mở giao diện web review (127.0.0.1:8765)
├─ tat_server.bat           # Tắt máy chủ web review khi kết thúc ca làm việc
├─ cap_nhat.bat             # Cập nhật mã nguồn và prompt mới nhất từ GitHub
├─ dong_goi_chia_se.bat     # Đóng gói toàn bộ công cụ thành TomTatBienSoan_ChiaSe.zip
├─ tomtat.bat               # Lối tắt chạy CLI: tomtat process | review | export | status | report
├─ README.md                # Tài liệu hướng dẫn chi tiết (file này)
├─ HUONG_DAN_CAI_DAT_NHANH.txt # Hướng dẫn tóm tắt 3 bước cho người dùng mới
├─ input\                   # Nơi chứa file .docx nạp vào (tải trực tiếp qua web hoặc copy vào đây)
├─ output\                  # Nơi lưu file .docx đã duyệt và các gói ZIP xuất theo ngày
├─ work\                    # Dữ liệu làm việc từng file (state.json, units.json, drafts\, logs\)
│   └─ learned_memory.json  # Bộ nhớ tự học tích lũy từ các bài đã duyệt
├─ prompts\                 # Các mẫu prompt gửi cho Gemini (system, label, extract, abstract, judge)
├─ src\tomtat\              # Mã nguồn gói Python
├─ tests\                   # Bộ kiểm thử tự động (pytest)
├─ docs\                    # Tài liệu hướng dẫn gốc của dự án
└─ samples\                 # File mẫu thực tế M54629.docx
```

---

## 2. Hướng dẫn cài đặt cho máy mới (Windows 10/11)

Chỉ cần thực hiện 3 bước đơn giản:

### Bước 1: Chuẩn bị Python 3.11+
Mở **Command Prompt (cmd)** gõ: `python --version`
- Nếu chưa có Python: Tải tại https://www.python.org/downloads/windows/ (chọn bản 64-bit, **NHỚ TICK "Add python.exe to PATH"** khi cài). Hoặc `setup.bat` sẽ tự động tải cài qua Windows Package Manager (`winget`).

### Bước 2: Tạo các khóa Gemini API miễn phí (Free Tier)
1. Truy cập: https://aistudio.google.com/apikey
2. Bấm **Create API key**. Bạn có thể tạo từ 1 key cho đến 20, 30, 40+ keys (mỗi key gắn với 1 Google Cloud Project khác nhau để được cấp hạn ngạch RPM/RPD riêng biệt).

### Bước 3: Chạy `setup.bat` và dán API Keys
1. Nhấp đúp chuột vào file: `setup.bat`.
2. Script sẽ tự động tạo `.venv`, cài đặt thư viện và mở file `api_keys.txt` bằng Notepad.
3. Dán các API keys vừa lấy vào (mỗi dòng 1 key, ví dụ `AIzaSy...`), bấm `Ctrl + S` lưu lại rồi đóng Notepad.

---

## 3. Quy trình làm việc hằng ngày trên giao diện Web

### 1. Khởi động
- Nhấp đúp chuột vào: `khoi_dong.bat`.
- Trình duyệt sẽ tự động mở giao diện tại: **http://127.0.0.1:8765**.

### 2. Các tính năng nổi bật & Quy tắc biên soạn
- **Nạp file & Chạy AI song song:**
  - Bấm nút `+ Thêm file .docx` hoặc kéo thả trực tiếp danh sách file vào trang web.
  - **Sắp xếp tự động:** Mặc định nút `🔻 Cuối lên đầu` đang bật (file ở cuối lên đầu, file đầu về cuối danh sách). Bạn có thể bấm để chuyển sang `🔺 Đầu lên trước`.
  - **Chạy cuốn chiếu từ trên xuống dưới:** Khi bật `Tự động chạy AI khi thêm file`, hệ thống sẽ bốc các file trên cùng của danh sách để chạy trước, hoàn thành đến đâu bốc tiếp đến đó.
  - **Tùy chỉnh số luồng:** Menu chọn luồng hỗ trợ `4`, `6`, `8 (mặc định)`, `10`, `12` luồng cùng lúc để khai thác tối đa sức mạnh của pool API keys.
- **Nhận diện file lỗi (`⚠️ Nghi hủy`):**
  - Tự động phát hiện các file lỗi cơ học nặng: sao kê chuyển tiền ngân hàng, lặp từ, phân đoạn vô nghĩa/cắt ngang câu, danh bạ điện thoại, từ điển bộ thủ, lịch trình...
  - Hiển thị badge màu đỏ `⚠️ Nghi hủy` và thông báo lý do rõ ràng để người dùng cân nhắc hủy file.
- **Biên soạn đa văn bản độc lập:**
  - Giữa các đoạn tóm tắt của từng bài trong file đa văn bản (`M...`) luôn tự động để trống 1 dòng (`\n\n`) để phân tách rõ ràng.
  - Tuyệt đối nghiêm cấm các từ nối dẫn dắt ("Trong chia sẻ khác...", "Ở chia sẻ khác..."). Mỗi văn bản được tóm tắt độc lập, trực tiếp đi thẳng vào nội dung.
- **Tải file hoàn thành & Dọn dẹp ca mới:**
  - Nút `⬇ Tải ZIP hôm nay`: Tải về toàn bộ file `.docx` đã duyệt, tên file tự động đặt theo ngày (ví dụ `18-11.zip`).
  - Nút `🧹 Xóa hết` và `🗑 Xóa danh sách chờ duyệt`: Xóa nhanh file trên giao diện để chuẩn bị cho ca làm việc mới mà không cần vào thư mục thủ công.

### 3. Tắt máy chủ
- Khi nghỉ ca hoặc tắt máy, nhấp đúp chuột vào file: `tat_server.bat`.

---

## 4. Xử lý bằng dòng lệnh (CLI - Tùy chọn nâng cao)

Ngoài giao diện web, công cụ hỗ trợ đầy đủ các lệnh CLI qua `tomtat.bat`:

- **Chạy xử lý toàn bộ file trong thư mục input\:**
  ```bat
  tomtat.bat process --workers 8
  ```
- **Chạy xử lý 1 file cụ thể:**
  ```bat
  tomtat.bat process input\M54629.docx
  ```
- **Xem bảng trạng thái tổng quan:**
  ```bat
  tomtat.bat status
  ```
- **Xuất báo cáo Excel / CSV:**
  ```bat
  tomtat.bat report
  ```
- **Xuất hàng loạt file đã duyệt ra thư mục output\:**
  ```bat
  tomtat.bat export --all-approved
  ```
- **Chạy kiểm thử tự động:**
  ```bat
  .venv\Scripts\pytest -v
  ```
