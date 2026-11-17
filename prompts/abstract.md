# NHIỆM VỤ: BƯỚC 3 - BIÊN SOẠN BẢN TÓM TẮT

Hãy viết bản tóm tắt biên soạn (`abstract`) cho (các) văn bản gốc dưới đây theo đúng phong cách chức năng đã xác định.

## THÔNG SỐ VÀ GIỚI HẠN ĐỘ DÀI (BẮT BUỘC ĐẠT)
- Phong cách chức năng: **{{ style }}**
- Loại bài: **{{ "Đa văn bản (" ~ (docs | length) ~ " văn bản)" if (docs | length) > 1 else "Đơn văn bản" }}**
- Số từ bản trích chọn (T): **{{ extract_words }} từ**
{% if targets.direction == "shorter" %}
- **YÊU CẦU ĐỘ DÀI (ngắn hơn bản trích chọn, B/T ≤ {{ (ratio_limit * 100) | int }}%):**
  - Tối đa không được vượt quá: **{{ targets.short_max }} từ** (kể cả dòng tiêu đề nếu có).
  - **Khoảng số từ mục tiêu cần đạt: từ {{ targets.short_low }} đến {{ targets.short_high }} từ.**
  - Nếu viết xong thấy vượt quá {{ targets.short_max }} từ, hãy cắt bớt từ ngữ rườm rà và chi tiết phụ (giữ nguyên ý chính) trước khi trả lời.
{% else %}
- **YÊU CẦU ĐỘ DÀI (bản trích chọn ngắn nên có thể chọn 1 trong 2 hướng):**
  - Hướng 1 (ngắn hơn trích chọn): tối đa **{{ targets.short_max }} từ** (khoảng {{ targets.short_low }} - {{ targets.short_high }} từ).
  - Hướng 2 (dài hơn trích chọn, bổ sung chi tiết từ văn bản gốc): tối thiểu **{{ targets.long_min }} từ** (nhắm khoảng **{{ targets.long_target }} từ**).
  - Tuyệt đối KHÔNG nằm ở khoảng giữa ({{ targets.short_max + 1 }} đến {{ targets.long_min - 1 }} từ) vì sẽ vi phạm quy tắc 70%.
{% endif %}

{{ style_guide }}

## NGUYÊN TẮC CỐT LÕI KHI BIÊN SOẠN
1. **Trọng tâm là các ý trong bản trích chọn**, tổng hợp và diễn đạt lại cho mạch lạc. Được phép lấy thêm chi tiết từ văn bản gốc để câu văn rõ nghĩa và liền mạch.
2. **Bám sát bản gốc và tối ưu BARTScore:**
   - **Dùng lại từ ngữ, thuật ngữ, cụm từ của bản gốc** ở những chỗ không cần đổi (thay từ đồng nghĩa tùy tiện sẽ làm tụt điểm BARTScore).
   - **Tuyệt đối không bịa thêm ý hay số liệu ngoài bản gốc.**
   - **Mọi con số xuất hiện trong bản biên soạn đều phải có mặt nguyên dạng trong văn bản gốc** (không làm tròn số, không đổi dạng viết, giữ nguyên dấu phẩy thập phân như `18,5`).
3. **Định dạng văn bản thuần cho ô bảng Word:**
   - Đối với đơn văn bản, các đoạn văn cách nhau bằng ký tự xuống dòng (`\n`).
   - KHÔNG dùng ký hiệu Markdown (`**in đậm**`, `# tiêu đề`, `*in nghiêng*`).
   - Không có khoảng trắng thừa trước dấu câu, không có khoảng trắng kép; đóng mở ngoặc đơn `()` và ngoặc kép `""` / `“”` cân bằng.

{% if (docs | length) > 1 %}
4. **QUY TẮC ĐẶC BIỆT BẮT BUỘC CHO ĐA VĂN BẢN ({{ docs | length }} VĂN BẢN ĐỘC LẬP):**
   - **Mỗi bài ở file đa văn bản là độc lập (mỗi bài là một hàng), không dính líu gì tới nhau.** Vì vậy phần biên soạn cho từng bài cũng phải hoàn toàn độc lập.
   - **Giữa các đoạn tóm tắt của từng văn bản BẮT BUỘC để trống 1 dòng** (ngăn cách bằng hai ký tự xuống dòng `\n\n`). Tuyệt đối không viết dính liền nhau.
   - **TUYỆT ĐỐI KHÔNG thêm từ nối hoặc câu dẫn dắt giữa hai đoạn** (CẤM các từ/câu như: *"Trong chia sẻ khác"*, *"Ở một chia sẻ khác"*, *"Bên cạnh đó"*, *"Mặt khác"*, *"Ngoài ra"*, *"Văn bản 2..."*, v.v.).
   - Hãy **đâm thẳng trực tiếp vào tóm tắt nội dung** của từng văn bản (Ví dụ: thay vì viết *"Trong chia sẻ khác, ngành dịch vụ được xem là..."*, hãy viết thẳng: *"Ngành dịch vụ được xem là..."*).
{% endif %}

{% if retry_errors %}
## CÁC LỖI Ở LẦN VIẾT TRƯỚC CẦN SỬA NGAY (BẮT BUỘC KHẮC PHỤC TRIỆT ĐỂ)
Bản nháp trước của bạn bị hệ thống kiểm tra từ chối vì các lỗi sau:
{% for err in retry_errors %}
- {{ err }}
{% endfor %}

Bản nháp trước (để bạn sửa trực tiếp):
"""
{{ previous_abstract }}
"""
{% endif %}

{% if user_note %}
## GHI CHÚ CỦA NGƯỜI DUYỆT (ưu tiên tuân thủ)
{{ user_note }}
{% endif %}

{% if learned_style_memory %}
{{ learned_style_memory }}
{% endif %}

{% if query %}
## CÂU TRUY VẤN (QUERY)
{{ query }}
{% endif %}

## BẢN TRÍCH CHỌN ĐÃ CHỌN ({{ extract_words }} từ - trọng tâm nội dung)
{{ extract }}

## GỢI Ý BIÊN SOẠN CỦA AI TRƯỚC ĐÓ (chỉ tham khảo; cẩn thận vì gợi ý này hay sai phong cách, sai độ dài hoặc trung lập hóa sai quy tắc)
{{ ai_abstract if ai_abstract else "(Trống)" }}

## TOÀN BỘ VĂN BẢN GỐC (nguồn đối chiếu sự thật, số liệu, thuật ngữ và phong cách)
{{ source_text }}
