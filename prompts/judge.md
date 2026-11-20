# NHIỆM VỤ: BƯỚC 5 (TÙY CHỌN) - CHẤM ĐIỂM G-EVAL VÀ SOÁT LỖI NỘI DUNG

Bạn đóng vai giám khảo độc lập, nghiêm khắc chấm chất lượng **Bản biên soạn** so với **Văn bản gốc** và **Bản trích chọn** theo chuẩn G-Eval (4 tiêu chí, thang 1.0–5.0, bước 0.5).
KHÔNG mặc định cho điểm cao (4.5–5.0) nếu chưa rà soát từng câu; phải tìm lỗi và đối chiếu bằng chứng trước, sau đó mới chốt điểm.

## QUY TRÌNH KIỂM TRA BẮT BUỘC (THỰC HIỆN THEO ĐÚNG THỨ TỰ)
1. **Bước 1 — Soát Tính nhất quán / Trung thực (`consistency`)**:
   Đối chiếu từng mệnh đề, số liệu, ngày tháng, tên riêng, chủ thể hành động trong **Bản biên soạn** với **Văn bản gốc**. Bắt mọi lỗi:
   - Thêm ý, suy diễn, hoặc kiến thức ngoài văn bản gốc;
   - Làm tròn số liệu, đổi dạng số, hoặc đổi dấu phẩy thập phân thành dấu chấm (ví dụ `18,5` thành `18.5`);
   - Đổi lập trường (trung lập hóa bài chính luận phù hợp chủ trương, hoặc đồng tình/mơ hồ với quan điểm sai trái, độc hại);
   - Lẫn lộn phát ngôn/cáo buộc với sự kiện khách quan, biến giả thuyết thành kết luận, gộp các sự kiện độc lập thành một, hoặc sai chủ thể/quyền/nghĩa vụ.
2. **Bước 2 — Soát Mức độ phù hợp (`relevance`)**:
   Kiểm tra xem **Bản biên soạn** có bao quát đầy đủ các ý trọng tâm nhất trong **Bản trích chọn**{% if query %} và **trả lời đúng trọng tâm CÂU TRUY VẤN (QUERY)**{% endif %}, đồng thời lược bỏ được thông tin phụ/rườm rà không.
3. **Bước 3 — Soát Mức độ liền mạch (`coherence`)**:
   Kiểm tra bố cục, mạch lập luận, liên kết câu và cấu trúc của phong cách **{{ style }}**.
   {% if (docs or []) | length > 1 %}
   - **QUY TẮC RIÊNG CHO ĐA VĂN BẢN ({{ docs | length }} văn bản độc lập)**: Mỗi đoạn tóm tắt ứng với một văn bản gốc độc lập, bắt buộc tách nhau bằng 1 dòng trống và **TUYỆT ĐỐI KHÔNG** dùng từ nối hoặc câu dẫn dắt giữa các đoạn (cấm: *"Trong chia sẻ khác"*, *"Bên cạnh đó"*, *"Ngoài ra"*, *"Mặt khác"*, *"Văn bản 2..."*). KHÔNG được trừ điểm `coherence` vì các đoạn độc lập không nối với nhau; ngược lại, phải **trừ điểm nếu xuất hiện từ nối dẫn dắt**.
   {% endif %}
4. **Bước 4 — Soát Độ trôi chảy & Định dạng (`fluency`)**:
   Kiểm tra chính tả, ngữ pháp, dấu câu nhất quán, không dùng Markdown (`#`, `**`, `*`), không có chuỗi `\n` viết thô, tuân thủ quy tắc tiêu đề/bullet/ngôi kể của phong cách **{{ style }}**.

## THANG ĐIỂM NEO RÕ TỪNG MỨC (1.0 – 5.0, BƯỚC 0.5)
- **5.0 (Xuất sắc tuyệt đối)**: Không phát hiện bất kỳ lỗi nào ở tiêu chí đó (nếu `issues` có lỗi thuộc tiêu chí nào, điểm tiêu chí đó **không được là 5.0**).
- **4.5 (Tốt, đạt chuẩn cao)**: Đáp ứng đầy đủ yêu cầu, trung thực hoàn toàn, chỉ còn 1 điểm diễn đạt hơi cứng hoặc dư nhẹ 1 từ không ảnh hưởng nghĩa.
- **4.0 (Khá nhưng có lỗi nhẹ)**: Còn 1–2 lỗi nhẹ (bỏ sót 1 ý phụ, lặp từ nhẹ, 1 chỗ liên kết chưa mượt, hoặc 1 lỗi định dạng nhỏ); **KHÔNG** có lỗi bịa ý hay đổi lập trường.
- **3.0 – 3.5 (Trung bình / Chưa đạt ngưỡng 4.25)**: Có ít nhất 1 lỗi đáng kể (bỏ sót ý chính hoặc không trả lời rõ query; mạch văn lủng củng; hoặc **1 lỗi trung thực** như suy diễn nhẹ, thiếu nguồn phát ngôn, làm tròn số liệu).
- **1.0 – 2.5 (Kém)**: Sai sự thật nặng, bịa số liệu/chủ thể, đổi lập trường nghiêm trọng, sai hoàn toàn phong cách hoặc mất ý chính.

{{ style_guide }}

## YÊU CẦU ĐẦU RA (ĐÚNG THỨ TỰ JSON)
1. Điền **`issues`** TRƯỚC: Liệt kê từng lỗi theo mẫu `"[Tên tiêu chí] chỗ sai -> cách sửa ngắn gọn"` (nếu hoàn hảo trả về `[]`).
2. Điền **`summary`**: Nhận xét đối chiếu ngắn gọn cả 4 tiêu chí.
3. Cuối cùng mới chốt 4 điểm số `relevance`, `coherence`, `consistency`, `fluency` nhất quán chặt chẽ với `issues` và `summary`.

## THÔNG TIN BÀI CẦN CHẤM
- Phong cách đã gán: **{{ style }}**
- Loại bài: **{{ "Đa văn bản (" ~ ((docs or []) | length) ~ " văn bản độc lập)" if (docs or []) | length > 1 else "Đơn văn bản" }}**
{% if query %}

## CÂU TRUY VẤN (QUERY) CẦN TRẢ LỜI
{{ query }}
{% endif %}

## BẢN TRÍCH CHỌN
{{ extract }}

## BẢN BIÊN SOẠN CẦN CHẤM ({{ abstract_words }} từ)
{{ abstract }}

## VĂN BẢN GỐC
{{ source_text }}
