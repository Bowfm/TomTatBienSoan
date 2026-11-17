# NHIỆM VỤ: BƯỚC 1 - GÁN NHÃN PHONG CÁCH CHỨC NĂNG

Hãy đọc kỹ (các) văn bản gốc dưới đây và xác định phong cách chức năng của từng văn bản cũng như nhãn chung cho toàn bài.

## 5 NHÃN HỢP LỆ (phải chọn đúng một chuỗi, viết thường, đúng dấu và khoảng trắng quanh gạch nối)
1. `sinh hoạt hằng ngày`
   - Loại văn bản: tâm sự, chia sẻ kinh nghiệm, quan điểm cá nhân (kể cả khi bàn về chính trị, xã hội, nghề nghiệp).
   - Dấu hiệu: ngôn ngữ tự nhiên đời thường, hầu như không có khuôn mẫu nghiêm ngặt, có thể xưng hô cá nhân ("tôi", "mình", "bạn"...), mang tính cá nhân hơn là đại diện tư tưởng.
2. `hành chính - công vụ`
   - Loại văn bản: nội quy, hướng dẫn, điều lệ, thủ tục, quyết định, thông tư, chỉ thị, văn bản pháp luật.
   - Dấu hiệu: có đề mục khuôn mẫu rõ ràng (căn cứ, điều, khoản, mục, quyền và nghĩa vụ, trách nhiệm thi hành).
3. `khoa học`
   - Loại văn bản: bài giảng, giáo trình, công trình nghiên cứu, bài viết phổ cập kiến thức (có thể về tự nhiên hoặc chính trị, xã hội).
   - Dấu hiệu: từ ngữ chuyên ngành rõ ràng, trình bày mạch lạc, giải thích khái niệm/phương pháp/kết quả. Bài phổ cập khoa học ngôn từ sinh động, gần gũi hơn nhưng mục đích chính vẫn là phổ biến kiến thức chứ không phải đưa tin thời sự.
4. `báo chí - công luận`
   - Loại văn bản: tin tức, phóng sự, bài phản ánh, quảng cáo (bao gồm cả tin tức thời sự về chính trị hoặc khoa học).
   - Dấu hiệu: có tính thời sự, nêu bật tình hình, sự kiện mới, mốc thời gian, địa điểm, nhân vật, trích dẫn phát ngôn.
5. `chính luận`
   - Loại văn bản: diễn văn, bài phát biểu của lãnh đạo Đảng, Nhà nước; xã luận; bài tuyên truyền chính sách, chủ trương, quan điểm, đường lối; bài phân tích, phản biện xã hội; bài chống diễn biến hòa bình, bảo vệ nền tảng tư tưởng của Đảng.
   - Dấu hiệu: ngôn từ trang trọng, có sức biểu cảm và tác động thuyết phục, lập luận khẳng định chủ trương/quan điểm, kêu gọi hành động.

## QUY TẮC PHÂN BIỆT CÁC CẶP DỄ NHẦM
- **báo chí - công luận vs chính luận**: Báo chí tường thuật sự kiện, có mốc thời gian, địa điểm, nhân vật, trích lời. Chính luận lập luận để thuyết phục, khẳng định chủ trương/quan điểm, giọng đại diện, trang trọng.
- **khoa học vs báo chí - công luận**: Khoa học giải thích khái niệm, thuật ngữ, phương pháp, số liệu có hệ thống. Báo chí đưa tin sự kiện thời sự.
- **sinh hoạt hằng ngày vs chính luận**: Cùng bàn chuyện xã hội/nghề nghiệp, nhưng sinh hoạt là tiếng nói và trải nghiệm cá nhân, lời lẽ đời thường; chính luận mang giọng đại diện cho quan điểm, đường lối.
- **hành chính - công vụ vs khoa học**: Hành chính có tính quy phạm, thủ tục, điều khoản; khoa học là luận giải tri thức.

## QUY TẮC ĐA VĂN BẢN
- Phân loại phong cách cho từng văn bản gốc (`per_doc`).
- Nếu các văn bản cùng phong cách: chọn phong cách chung đó.
- Nếu khác phong cách: cộng tổng số từ theo từng phong cách và chọn phong cách chiếm đa số (nhiều từ nhất).
- Nếu một văn bản không thật rõ ràng, hãy gán phong cách **gần nhất**, không bỏ trống.
- Đánh giá mức độ chắc chắn (`confidence`): `cao` (rõ ràng), `trung_binh` (hơi giao thoa giữa 2 phong cách), hoặc `thap` (rất mơ hồ, cần người duyệt kiểm tra kỹ).

{% if user_note %}
## GHI CHÚ CỦA NGƯỜI DUYỆT (ưu tiên tuân thủ)
{{ user_note }}
{% endif %}

{% if learned_label_memory %}
{{ learned_label_memory }}
{% endif %}

## DỮ LIỆU ĐẦU VÀO
Số lượng văn bản gốc: {{ docs | length }} ({{ "đa văn bản" if (docs | length) > 1 else "đơn văn bản" }})
{% for d in docs %}
- Văn bản {{ d.n }}: {{ d.words }} từ
{% endfor %}

{{ source_text }}
