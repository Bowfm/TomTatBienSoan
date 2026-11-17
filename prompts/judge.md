# NHIỆM VỤ: BƯỚC 5 (TÙY CHỌN) - CHẤM ĐIỂM G-EVAL VÀ SOÁT LỖI NỘI DUNG

Bạn đóng vai giám khảo độc lập chấm chất lượng **Bản tóm tắt biên soạn** so với **Văn bản gốc** và **Bản trích chọn** theo đúng 4 tiêu chí của G-Eval (mỗi tiêu chí cho điểm từ 1 đến 5, có thể lẻ 0.5) và chỉ ra các lỗi nội dung/phong cách mà bộ kiểm tra cơ học chưa bắt được.

## 4 TIÊU CHÍ G-EVAL (1 = rất kém, 3 = đạt mức cơ bản, 4 = tốt, 5 = xuất sắc)
1. `relevance` (Mức độ phù hợp): Bao quát đầy đủ các thông tin quan trọng nhất (nhất là các ý trong bản trích chọn và câu trả lời cho query nếu có), đồng thời lược bỏ được thông tin dư thừa.
2. `coherence` (Mức độ liền mạch): Tổ chức ý logic, mạch lạc, liên kết câu/đoạn tự nhiên, đúng cấu trúc của phong cách **{{ style }}**.
3. `consistency` (Tính nhất quán / Trung thực): Hoàn toàn đúng sự thật so với văn bản gốc. Trừ điểm nặng nếu:
   - Thêm ý hoặc suy diễn ngoài văn bản gốc;
   - Làm tròn số liệu, đổi số liệu, hoặc viết dấu phẩy thập phân thành dấu chấm;
   - Đổi lập trường (trung tính hóa văn bản chính luận, hoặc đồng tình với quan điểm sai trái);
   - Lẫn lộn phát ngôn với sự kiện khách quan, gộp các sự kiện khác nhau thành một, biến giả thuyết thành kết luận, hoặc sai chủ thể/quyền/nghĩa vụ.
4. `fluency` (Độ trôi chảy): Đúng chính tả, ngữ pháp chuẩn mực, dấu câu nhất quán, đúng quy tắc định dạng của phong cách **{{ style }}** (ví dụ: báo chí/sinh hoạt/chính luận không dùng bullet; sinh hoạt dùng ngôi thứ ba trung tính và không có tiêu đề).

{{ style_guide }}

## YÊU CẦU ĐẦU RA
- Cho điểm 4 tiêu chí.
- Liệt kê cụ thể từng vấn đề phát hiện được vào danh sách `issues` (nếu bản biên soạn đã tốt và trung thực, trả về danh sách rỗng `[]`). Mỗi vấn đề cần chỉ rõ chỗ sai và cách sửa ngắn gọn.

## PHONG CÁCH ĐÃ GÁN: {{ style }}

## BẢN TRÍCH CHỌN
{{ extract }}

## BẢN BIÊN SOẠN CẦN CHẤM ({{ abstract_words }} từ)
{{ abstract }}

## VĂN BẢN GỐC
{{ source_text }}
