# TomTatBienSoan — Quy tắc Chống Tái Diễn Lỗi (Regression Guard)

Mọi phiên làm việc, sửa lỗi (debug), hoặc nâng cấp trong repo `d:\TomTatBienSoan` PHẢI tuân thủ quy trình khóa lỗi dưới đây để lỗi đã sửa không bao giờ tái diễn.

## 1. Tài liệu & Bộ kiểm tra Bắt buộc
- **Sổ lỗi & Invariant**: [docs/KNOWN_BUGS.md](docs/KNOWN_BUGS.md) — **Bắt buộc đọc** các mục liên quan trước khi sửa code ở module tương ứng.
- **Bộ test hồi quy chuyên biệt**: [tests/test_regressions.py](tests/test_regressions.py) (cùng với `tests/test_*.py`).
- **Script kiểm tra nhanh**: `.agents/verify_guard.py` (kiểm tra cú pháp Python, ES module imports/syntax JS, `config.yaml`, tham chiếu tĩnh HTML).

## 2. Quy trình Bắt buộc Khi Fix Một Lỗi
1. **Tìm nguyên nhân gốc (Root Cause) & Xác định Invariant**: Không vá triệu chứng tạm thời; phải xác định điều kiện bất biến cần giữ vĩnh viễn.
2. **Viết Test Hồi Quy (Regression Test)**:
   - Thêm test tái hiện đúng điều kiện gây lỗi vào `tests/test_regressions.py` (hoặc file test module tương ứng nếu phù hợp).
   - Nếu lỗi nằm ở tầng config (`config.yaml`), batch (`.bat`), HTML/JS (`web/static`), hoặc `updater.py`, phải viết test hợp đồng / cú pháp / mô phỏng I/O thay vì bỏ qua test.
3. **Cập nhật `docs/KNOWN_BUGS.md`**:
   - Ghi nhận mục `BUG-XXX` mới gồm: Ngày, File/Symbol, Triệu chứng, Nguyên nhân gốc, Bất biến cần bảo vệ (Invariant), và tên hàm test bảo vệ.
4. **Chạy Kiểm Chứng Toàn Bộ Sau Lần Sửa Cuối**:
   - Chạy guard nhanh: `.venv\Scripts\python.exe .agents\verify_guard.py`
   - Chạy toàn bộ test suite: `.venv\Scripts\pytest.exe -q`
   - Tuyệt đối không xóa, skip, hoặc nới lỏng assertion của test cũ nếu người dùng không yêu cầu đổi nghiệp vụ.

## 3. Các Bất Biến Cốt Lõi Của Dự Án (Tóm tắt)
- `api_keys.txt` luôn ưu tiên hơn biến môi trường `.env` (`GEMINI_API_KEY`).
- Chu kỳ reset quota ngày tính theo **00:00 Pacific Time (`15:00` giờ VN)**.
- Lỗi `429` RPM chỉ đặt cooldown; cần đủ 3 lần `429` trên cùng `(key, model)` mới đánh dấu cạn RPD trong ngày; lỗi `401/403` loại key khỏi toàn bộ `models_chain`.
- `updater.py` không bao giờ báo có bản cập nhật mới khi commit local mới hơn `origin/main` (`_is_git_ancestor`), và ZIP update tuyệt đối bảo vệ `PROTECTED_ENTRIES` (`input`, `output`, `work`, `.venv`, `.env`, `api_keys.txt`).
- `setup.bat` tránh bọc khối lệnh chứa biến đường dẫn trong `if (...)` dễ văng `cmd.exe` khi gặp dấu ngoặc; `khoi_dong.bat` không chặn khởi động bằng auto-update đồng bộ.
- `B/T <= 70%` (`abstract.ratio_limit = 0.70`, `allow_longer_below_words = 0`) luôn là bất biến ở tầng kiểm tra độ dài biên soạn.
- Xử lý song song tối đa `max_workers` (hiện là 2) file, chặn ở cả config/UI/API/CLI; pacing `(key, model)` đặt chỗ dưới `_key_lock` đảm bảo `<= 60/min_interval_s < rpm_limit` RPM/key bất kể số luồng (BUG-014).
