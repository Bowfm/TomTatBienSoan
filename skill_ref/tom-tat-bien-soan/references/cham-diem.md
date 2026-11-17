# Chỉ tiêu chấm điểm và ý nghĩa đối với cách làm

Bài được chấm bằng AI. Chỉ tiêu đạt:

| Chỉ tiêu | Ngưỡng | Áp dụng cho |
|---|---|---|
| ROUGE-2 | ≥ 33,00% | bản trích chọn (so với bản trích chọn chuẩn) |
| BARTScore | ≥ -2,56 | bản biên soạn (so với văn bản đầu vào) |
| G-Eval | ≥ 4,25 | bản tóm tắt (trung bình 4 tiêu chí, mỗi tiêu chí 1-5) |
| Edit | ≥ 0,1 | độ chênh độ dài giữa bản biên soạn và bản trích chọn |
| Compress | 0,1 ≤ Compress ≤ 0,5 | bản trích chọn so với văn bản đầu vào (bước 2) |
| P | = 1 | tỉ lệ nhãn phong cách đúng |

## Từng chỉ tiêu nói lên điều gì và nên làm gì

**ROUGE-2** đo độ phủ các cặp hai từ liền nhau giữa bản trích chọn của mình và bản chuẩn. Vì vậy:
- Chọn nguyên câu, nguyên văn, đúng thứ tự gốc; cặp từ liền kề bị phá vỡ khi sửa chữ hoặc cắt giữa câu.
- Chọn những câu một người đọc kỹ sẽ cho là quan trọng nhất (câu luận đề, câu nêu sự kiện chính, số liệu chính, đề mục chính), và các câu trả lời query nếu có query.
- Độ dài khoảng 30% là điểm cân bằng: quá ngắn thì độ phủ thấp, quá dài thì vượt ngưỡng 50%.

**BARTScore** là log-likelihood của bản tóm tắt khi sinh từ văn bản đầu vào, luôn nhỏ hơn 0, càng gần 0 càng tốt. Bản biên soạn càng bám từ ngữ, thuật ngữ, cấu trúc câu và sự kiện của bản gốc thì điểm càng cao. Vì vậy:
- Diễn đạt lại để mạch lạc, nhưng dùng lại từ ngữ gốc ở mọi chỗ có thể.
- Không thêm ý hay chi tiết ngoài bản gốc (kể cả đúng), không suy diễn, không khái quát bằng lời sáo rỗng.
- Giữ đúng phong cách và lập trường của bản gốc.

**G-Eval** do mô hình ngôn ngữ chấm 4 tiêu chí rồi lấy trung bình: mức độ phù hợp (có thông tin quan trọng, bỏ thông tin dư), mức độ liền mạch (logic, tổ chức), tính nhất quán (đúng sự thật so với đầu vào), độ trôi chảy (ngữ pháp, dễ đọc). Cần cả bốn: đủ ý chính, có mạch, không sai sự thật, văn trôi chảy.

**Edit** được tính từ số từ bản biên soạn (B) và bản trích chọn (T):
- nếu B < T: Edit = 1 - B/T
- nếu B ≥ T: Edit = 1 - T/B

Edit ≥ 0,1 tức hai bản lệch nhau về độ dài ít nhất 10%.

**Quy tắc 70% trong hướng dẫn** (bản biên soạn ngắn hơn thì B/T ≤ 70%, dài hơn thì T/B ≤ 70%) **chặt hơn** ngưỡng Edit ≥ 0,1: thỏa quy tắc 70% thì Edit ≥ 0,3. Skill này tuân theo quy tắc 70% (cách đọc nghiêm nhất) nên Edit luôn đạt. Nếu người phụ trách dự án xác nhận quy tắc 70% có nghĩa khác, chỉnh lại ở `scripts/check.py` (hằng số `RATIO_LIMIT`).

**Compress** = số từ bản trích chọn / số từ văn bản đầu vào, phải trong [0,1; 0,5]. Với đa văn bản, văn bản đầu vào là tổng số từ của tất cả văn bản gốc (không tính query và các hàng gợi ý của AI).

**P** = số nhãn đúng / tổng số nhãn đã gán. P=1 nghĩa là sai một nhãn là không đạt, nên ca mơ hồ cần cân nhắc kỹ.

## Về cách đếm từ

Hệ thống chấm không nêu rõ cách đếm từ (theo âm tiết hay theo từ ghép). Script trong skill đếm theo các đơn vị tách bằng khoảng trắng và có ít nhất một chữ hoặc số (bỏ qua dấu gạch ngang đứng riêng). Cách đếm này gần với đếm âm tiết. Tỉ lệ giữa các bản thường ít bị ảnh hưởng, nhưng vẫn nên nhắm vào giữa khoảng cho phép (Compress ~0,3; B/T khoảng 0,55-0,68) chứ không sát biên.
