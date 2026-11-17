# Công cụ tự động hóa dự án gán nhãn "Tóm tắt – Biên soạn" văn bản tiếng Việt (`tomtat`)

Công cụ chạy local trên Windows giúp tự động hóa quy trình hoàn thiện các file `.docx` của dự án gán nhãn **Tóm tắt – Biên soạn** văn bản tiếng Việt bằng Gemini API, hỗ trợ chạy hàng loạt bằng dòng lệnh và có giao diện web local (`127.0.0.1`) để người gán nhãn duyệt, chỉnh sửa, kiểm tra cơ học thời gian thực trước khi xuất file `.docx` cuối cùng.

---

## 1. Cấu trúc thư mục dự án

```text
D:\TomTatBienSoan\
├─ config.yaml              # Toàn bộ ngưỡng chấm, hằng số, tên model Gemini, số lần thử lại...
├─ .env.example             # Mẫu cấu hình GEMINI_API_KEY
├─ .env                     # Chứa GEMINI_API_KEY thật (đã được .gitignore bảo vệ)
├─ requirements.txt         # Danh sách thư viện Python
├─ setup.bat                # Tạo môi trường ảo .venv và cài đặt thư viện
├─ tomtat.bat               # Lối tắt chạy lệnh: tomtat process | review | export | status | report
├─ README.md                # Tài liệu hướng dẫn sử dụng (file này)
├─ input\                   # Đặt các file .docx đầu vào tại đây (chỉ đọc, KHÔNG BAO GIỜ bị sửa)
├─ output\                  # Chứa các file .docx đã hoàn thiện (giữ nguyên tên gốc) + baocao.xlsx / baocao.csv
├─ work\                    # Dữ liệu làm việc theo từng file (work\<tên-file>\)
│   ├─ state.json           # Trạng thái hiện tại, kết quả từng bước, danh sách cảnh báo
│   ├─ units.json           # Các câu/đề mục đã tách có đánh số [id] và đếm từ
│   ├─ drafts\v001.json...  # Lịch sử các phiên bản nháp để khôi phục bất kỳ lúc nào
│   └─ logs\                # Log chi tiết từng lần gọi Gemini (prompt, phản hồi, token, thời gian)
├─ prompts\                 # Các mẫu prompt gửi cho Gemini (dễ dàng chỉnh sửa bằng Notepad/VS Code)
│   ├─ system.md            # Nguyên tắc hệ thống cốt lõi
│   ├─ label.md             # Prompt bước 1: Gán nhãn phong cách chức năng
│   ├─ extract.md           # Prompt bước 2: Chọn ID đơn vị câu/đề mục cho bản trích chọn
│   ├─ abstract.md          # Prompt bước 3: Viết bản tóm tắt biên soạn
│   ├─ judge.md             # Prompt bước 5: Tự chấm G-Eval 4 tiêu chí
│   └─ styles\              # Quy tắc riêng của 5 phong cách chức năng
├─ src\tomtat\              # Mã nguồn gói Python
├─ tests\                   # Bộ kiểm thử tự động (pytest)
├─ docs\                    # Tài liệu hướng dẫn gốc của dự án (giữ nguyên)
├─ skill_ref\               # Skill tham chiếu gốc (giữ nguyên)
└─ samples\                 # File mẫu M54629.docx (giữ nguyên)
```

---

## 2. Hướng dẫn cài đặt từ đầu (Windows 10/11)

### Bước 1: Kiểm tra Python 3.11+
Mở **Command Prompt (cmd)** hoặc **PowerShell**, gõ:
```bat
python --version
```
- Nếu máy đã hiện `Python 3.11.x` hoặc `Python 3.12.x` trở lên: chuyển sang Bước 2.
- Nếu chưa có Python: tải bộ cài chính thức tại https://www.python.org/downloads/windows/ (chọn bản **Windows installer (64-bit)**), khi cài nhớ **tick vào ô "Add python.exe to PATH"**, sau đó mở lại cửa sổ cmd.

### Bước 2: Tạo khóa Gemini API (`GEMINI_API_KEY`)
1. Truy cập Google AI Studio: https://aistudio.google.com/apikey
2. Đăng nhập tài khoản Google và bấm **Create API key**.
3. Sao chép chuỗi API key vừa tạo.
   - *Lưu ý về gói cước và model:* Mặc định trong `config.yaml` đang để model mạnh nhất là `gemini-4-carbon` (yêu cầu tài khoản đã bật thanh toán / Paid tier). Nếu muốn chạy nhanh và tiết kiệm chi phí hơn, bạn có thể mở `config.yaml` và đổi `model: gemini-4-argon` hoặc `model: gemini-3.8-flash`.

### Bước 3: Chạy `setup.bat` và điền API key
Tại thư mục `D:\TomTatBienSoan\`, chạy:
```bat
cd /d D:\TomTatBienSoan
setup.bat
```
Script sẽ tự động tạo môi trường ảo `.venv`, cài đặt mọi thư viện cần thiết, tạo sẵn các thư mục `input\`, `output\`, `work\` và tạo file `.env`.

Mở file `D:\TomTatBienSoan\.env` bằng Notepad và dán API key của bạn vào:
```ini
GEMINI_API_KEY=AIzaSy...khóa_của_bạn...
```
*(Hoặc bạn có thể đặt biến môi trường `set GEMINI_API_KEY=...` trong cửa sổ dòng lệnh).*

---

## 3. Quy trình sử dụng hằng ngày

### Bước 1: Bỏ file `.docx` cần làm vào thư mục `input\`
Sao chép một hoặc nhiều file `.docx` (ví dụ `M54629.docx`, `S10234.docx`...) vào `D:\TomTatBienSoan\input\`.
- Quy ước tên file: bắt đầu bằng **`M`** là **đa văn bản** ($\ge 2$ văn bản gốc), bắt đầu bằng **`S`** là **đơn văn bản** ($1$ văn bản gốc). Nếu số văn bản nhận diện được trong bảng lệch với tên file, công cụ sẽ cảnh báo và dừng lại ở trạng thái `cần xử lý tay` để bạn kiểm tra, không tự đoán lặng lẽ.

### Bước 2: Chạy xử lý hàng loạt bằng dòng lệnh
```bat
tomtat.bat process
```
Các tùy chọn hữu ích:
- Xử lý một file hoặc thư mục bất kỳ:
  ```bat
  tomtat.bat process input\M54629.docx
  ```
- Chỉnh số luồng chạy song song (mặc định là 2):
  ```bat
  tomtat.bat process --workers 3
  ```
- Chạy lại các file đã làm rồi:
  ```bat
  tomtat.bat process --force
  ```
- Chỉ chạy lại đúng một bước (`inspect`, `label`, `extract`, `abstract`, `judge`):
  ```bat
  tomtat.bat process input\M54629.docx --only abstract
  ```
- Chạy thử ở chế độ giả lập (không cần API key, không tốn tiền):
  ```bat
  tomtat.bat process --mock --force
  ```

### Bước 3: Xem nhanh bảng trạng thái và báo cáo tổng hợp
```bat
tomtat.bat status
tomtat.bat report
```
- Lệnh `status` in bảng tổng hợp ngay trên màn hình console.
- Lệnh `report` (cũng được gọi tự động sau `process` và `export`) xuất ra 2 file báo cáo tại:
  - `D:\TomTatBienSoan\output\baocao.xlsx` (mở trực tiếp bằng Excel, định dạng cột rõ ràng)
  - `D:\TomTatBienSoan\output\baocao.csv` (UTF-8 có BOM)

### Bước 4: Mở giao diện web để duyệt và chỉnh sửa
```bat
tomtat.bat review
```
Trình duyệt sẽ tự động mở địa chỉ `http://127.0.0.1:8765` (nếu đang chạy thử không có API key, dùng `tomtat.bat review --mock`).

Trên giao diện duyệt:
1. **Cột trái**: Danh sách file kèm trạng thái (`chưa xử lý`, `đã có nháp`, `cần xử lý tay`, `đã duyệt`, `đã xuất`, `lỗi`). Có bộ lọc trạng thái và phím tắt `Alt+↑` / `Alt+↓` để chuyển nhanh giữa các file.
2. **Sửa nhãn phong cách**: Chọn trong dropdown 5 giá trị chuẩn; xem mức độ chắc chắn, lý do của AI và phân loại từng văn bản (với bài đa văn bản).
3. **Sửa bản trích chọn theo 2 cách**:
   - **Cách 1 (Tick câu)**: Tick hoặc bỏ tick từng đơn vị câu `[id]` ở cột văn bản gốc bên trái; các câu được chọn sẽ được tô vàng nổi bật và tự động ghép nguyên văn theo đúng thứ tự gốc.
   - **Cách 2 (Sửa chữ trực tiếp)**: Xóa bớt thành phần phụ ở đầu câu ngay trong ô văn bản trích chọn; thanh trạng thái bên dưới sẽ kiểm tra nguyên văn và thứ tự tức thì.
4. **Sửa bản biên soạn**: Gõ sửa trực tiếp; mọi thay đổi đều tự động tính lại số từ, tỉ lệ `Compress`, tỉ lệ `B/T` (hoặc `T/B`), chỉ số `Edit`, độ bám từ ngữ gốc và danh sách `LỖI` / `CẢNH BÁO` (dùng chung một hàm kiểm tra với CLI).
5. **Chạy lại bằng AI kèm ghi chú**: Nhập yêu cầu vào ô ghi chú (ví dụ: *"giữ giọng trang trọng hơn, cắt bớt 15 từ"*) rồi bấm `AI: Biên soạn lại` (hoặc `AI: Gán nhãn lại`, `AI: Trích chọn lại`, `AI: Chấm G-Eval`).
6. **Lịch sử phiên bản**: Mọi lần AI chạy hoặc bạn bấm `Lưu nháp (Ctrl+S)` đều được lưu thành `v001`, `v002`... Có thể bấm **Khôi phục** bất kỳ phiên bản nào.
7. **Xem trước & Duyệt ghi file**:
   - Bấm **Xem trước bảng cuối** để kiểm tra toàn bộ bảng 1 cột trước khi xuất.
   - Bấm **Duyệt & Ghi .docx**: Hệ thống sẽ chặn lại nếu còn `LỖI` cơ học, hỏi xác nhận nếu còn `CẢNH BÁO`, sau đó ghi file vào `output\<tên-file>.docx` và tự mở lại file `.docx` để xác minh các hàng gốc phía trên được giữ nguyên 100%.

### Bước 5: Xuất hàng loạt bằng dòng lệnh (tùy chọn)
Ngoài việc bấm **Duyệt & Ghi .docx** từng bài trên giao diện web, bạn có thể xuất hàng loạt bằng lệnh:
```bat
tomtat.bat export --all-approved
```
Hoặc xuất cả những bài đã có nháp sạch lỗi cơ học:
```bat
tomtat.bat export --include-drafts
```

---

## 4. Tuỳ chỉnh quy tắc và prompt

- **Chỉnh ngưỡng độ dài, số lần thử lại, tên model Gemini**: Mở `D:\TomTatBienSoan\config.yaml`.
- **Chỉnh lời dặn cho Gemini**: Mở các file `.md` trong `D:\TomTatBienSoan\prompts\` và `D:\TomTatBienSoan\prompts\styles\`. Không cần sửa code Python.
- **Chạy bộ kiểm thử tự động**:
  ```bat
  .venv\Scripts\pytest -v
  ```
