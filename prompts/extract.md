# NHIỆM VỤ: BƯỚC 2 - TRÍCH CHỌN NỘI DUNG QUAN TRỌNG

Hãy chọn các đơn vị câu/đề mục quan trọng nhất từ (các) văn bản gốc đã được đánh số `[id]` dưới đây để tạo bản tóm tắt trích chọn.

## QUY TẮC BẮT BUỘC
1. **CHỈ TRẢ VỀ DANH SÁCH SỐ ID (`ids`)** theo từng văn bản (`doc`). Tuyệt đối không tự gõ lại câu văn; hệ thống sẽ tự động ghép nguyên văn từ danh sách `ids` bạn chọn theo đúng thứ tự gốc.
2. **NGÂN SÁCH SỐ TỪ:**
   - Tổng số từ văn bản gốc: **{{ budget.total_words }} từ**.
   - Giới hạn bắt buộc (10% - 50%): từ **{{ budget.extract_min }}** đến **{{ budget.extract_max }} từ**.
   - **Khoảng mục tiêu an toàn ({{ budget.target_low_pct }}% - {{ budget.target_high_pct }}%): từ {{ budget.extract_target_low }} đến {{ budget.extract_target_high }} từ (lý tưởng khoảng {{ budget.extract_target_mid }} từ ~ {{ budget.target_mid_pct }}%).**
   - Hãy cộng nhẩm số từ ghi trong ngoặc `(... từ)` của các đơn vị `[id]` bạn chọn để tổng số từ nằm gọn trong khoảng **{{ budget.extract_target_low }} - {{ budget.extract_target_high }} từ**.
3. **CÁCH CHỌN NỘI DUNG:**
   {% if query %}
   - **Có câu truy vấn (query):** Ưu tiên chọn các câu/đề mục trả lời hoặc liên quan trực tiếp đến truy vấn trước, sau đó chọn thêm các câu quan trọng nhất của văn bản cho đến khi đạt khoảng số từ mục tiêu.
   {% else %}
   - **Không có câu truy vấn:** Chọn các câu luận đề, câu nêu sự kiện chính, số liệu chính, đề mục chính có ý nghĩa bao quát nhất.
   {% endif %}
   - Bỏ qua các câu dẫn dắt rườm rà, ý lặp lại, ý nhấn mạnh phụ, câu cảm thán vụn vặt, ví dụ minh họa dài, chú giải, hashtag.
   {% if (docs | length) > 1 %}
   - **Đa văn bản ({{ docs | length }} văn bản):** MỖI văn bản gốc đều phải có các đơn vị được chọn đại diện tương xứng với độ dài và tầm quan trọng của văn bản đó; không bỏ trống văn bản nào.
   {% endif %}

{% if retry_message %}
## YÊU CẦU ĐIỀU CHỈNH TỪ LẦN THỬ TRƯỚC (BẮT BUỘC KHẮC PHỤC)
{{ retry_message }}
{% endif %}

## CÂU TRUY VẤN (QUERY)
{{ query if query else "(Không có câu truy vấn)" }}

## GỢI Ý CỦA AI TRƯỚC ĐÓ (chỉ tham khảo ý chính, KHÔNG bắt chước nếu quá dài/ngắn)
{{ ai_extract if ai_extract else "(Trống)" }}

## CÁC ĐƠN VỊ VĂN BẢN GỐC (chỉ chọn các số `id` hợp lệ có trong danh sách của từng văn bản)
{{ units_listing }}
