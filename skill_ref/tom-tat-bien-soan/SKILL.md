---
name: tom-tat-bien-soan
description: Hoàn thiện file .docx của dự án gán nhãn "tóm tắt - biên soạn" văn bản tiếng Việt - gán nhãn phong cách chức năng (sinh hoạt hằng ngày / hành chính - công vụ / khoa học / báo chí - công luận / chính luận), tạo bản trích chọn (10%-50% số từ gốc), viết bản biên soạn tóm tắt, rồi điền 3 hàng cuối của bảng. Hãy dùng skill này mỗi khi người dùng đưa file .docx có bảng 1 cột gồm query, các văn bản gốc, "tóm tắt trích chọn của AI", "tóm tắt biên soạn của AI", hoặc nhắc tới gán nhãn, trích chọn, biên soạn, tóm tắt đơn văn bản / đa văn bản, phong cách chức năng, ROUGE-2, BARTScore, G-Eval - kể cả khi không nói tên skill.
---

# Tóm tắt - biên soạn văn bản tiếng Việt (dự án gán nhãn)

Skill này biến một file `.docx` của dự án thành bản đã làm xong: gán nhãn phong cách chức năng, trích chọn nội dung quan trọng, biên soạn bản tóm tắt, rồi điền đúng 3 hàng cuối của bảng. Bài làm được chấm tự động (ROUGE-2, BARTScore, G-Eval, Edit, Compress, P) nên vừa phải làm đúng nội dung, vừa phải đúng các con số độ dài. Các script trong `scripts/` lo phần đếm từ, ghép, kiểm tra và ghi file để bạn tập trung vào việc đọc hiểu và viết.

Các file tham chiếu (đọc khi cần, không cần đọc hết ngay):
- `references/phong-cach.md` - cách nhận biết 5 phong cách, cách biên soạn theo từng phong cách, các lỗi cần tránh, xử lý nội dung nhạy cảm. **Đọc trước khi gán nhãn và trước khi viết bản biên soạn.**
- `references/cham-diem.md` - các chỉ tiêu chấm điểm và lý do đằng sau từng quy tắc.

## Cấu trúc file đầu vào

Mỗi file có đúng 1 bảng, 1 cột. Các hàng theo thứ tự:

1. query (câu truy vấn, có thể trống)
2. một hoặc nhiều văn bản gốc (một văn bản = đơn văn bản, nhiều văn bản = đa văn bản)
3. tóm tắt trích chọn do AI gợi ý
4. tóm tắt biên soạn do AI gợi ý
5. ba hàng cần điền: **phong cách chức năng**, **trích chọn**, **biên soạn** (có thể đã có sẵn nhưng trống, hoặc chưa có - script tự nhận biết)

Hai hàng gợi ý của AI chỉ để tham khảo. Chúng thường vi phạm quy tắc (sai thứ tự, quá dài hoặc quá ngắn, trung tính hóa lập trường, sai phong cách), nên đọc để lấy ý rồi tự làm lại, không sao chép nguyên.

## Quy trình

Đặt thư mục làm việc cho mỗi file, ví dụ `W=/home/claude/work/<tên-file>`. Nếu người dùng đưa nhiều file, làm lần lượt từng file với thư mục riêng.

### Bước 0 - Kiểm tra cấu trúc và đọc văn bản

```bash
mkdir -p $W
python scripts/inspect_docx.py "/mnt/user-data/uploads/<file>.docx" --workdir $W
```

Script in ra: cách nhận diện các hàng (query, số văn bản, hàng gợi ý AI, có sẵn 3 hàng nhãn hay chưa), tổng số từ gốc và ngân sách số từ cho bản trích chọn. Nó cũng ghi `$W/units.txt` (văn bản gốc chia thành các câu/đề mục đánh số `[id]` kèm số từ) và `$W/units.json`.

**Đọc kỹ phần nhận diện cấu trúc.** Nếu số văn bản gốc hoặc vị trí các hàng có vẻ sai, hỏi người dùng trước khi làm tiếp, vì mọi con số tỉ lệ phụ thuộc vào việc xác định đúng đâu là văn bản gốc.

Sau đó đọc toàn bộ `$W/units.txt` (dùng `sed -n 'a,bp'` đọc từng đoạn nếu dài). Đọc cả query và hai bản gợi ý của AI.

### Bước 1 - Gán nhãn phong cách chức năng

Đọc `references/phong-cach.md` mục "Nhận biết phong cách". Đầu ra là **đúng một** trong năm chuỗi sau (viết thường, đúng dấu, đúng gạch nối có khoảng trắng):

```
sinh hoạt hằng ngày
hành chính - công vụ
khoa học
báo chí - công luận
chính luận
```

- Văn bản không rõ ràng thì gán phong cách gần nhất, đừng bỏ trống.
- Đa văn bản: nếu cùng phong cách thì lấy phong cách chung; nếu khác nhau thì phân loại từng văn bản, cộng tổng số từ theo từng phong cách, chọn phong cách chiếm nhiều từ nhất.
- Chỉ tiêu P=1 nghĩa là mọi nhãn phải đúng, nên với ca khó (ví dụ bài phổ cập khoa học giọng báo chí, bài phân tích xã hội giọng chính luận) hãy cân nhắc kỹ dấu hiệu nhận biết và nói rõ mức độ chắc chắn khi báo cáo cho người dùng.

### Bước 2 - Trích chọn nội dung quan trọng

Yêu cầu: bản trích chọn gồm **nguyên văn** các câu/đề mục của văn bản gốc, **giữ đúng thứ tự gốc**, tỉ lệ số từ so với tổng số từ các văn bản gốc nằm trong **[10%, 50%]**. Mục tiêu nên nhắm tới là khoảng **30%** (an toàn trong khoảng 20%-40%), vì cách đếm từ của hệ thống chấm có thể lệch nhẹ so với script, cần chừa biên.

Cách làm:
1. Có query: chọn trước các câu/đề mục trả lời hoặc liên quan trực tiếp tới query, rồi bổ sung các câu có ý nghĩa nhất cho tới khi đủ độ dài. Không có query: chọn dần các câu/đề mục có ý nghĩa nhất.
2. Ưu tiên chọn nguyên câu hoặc nguyên đề mục. Bỏ các thành phần phụ dẫn dắt, ý lặp, ý nhấn mạnh thêm, ví dụ, chú giải. Nếu thật cần rút gọn thì chỉ cắt thành phần phụ ở đầu câu.
3. Đa văn bản: gộp trích chọn của tất cả văn bản thành một, theo đúng thứ tự văn bản đã cho. Mỗi văn bản nên có phần đại diện, không để một văn bản chiếm gần hết hoặc bị bỏ hẳn.
4. Ghi lựa chọn vào file spec, mỗi dòng là `<số văn bản>: <danh sách id>`, ví dụ:
   ```
   1: 1-3, 6, 9-11
   2: 2, 4-5
   ```
   rồi ghép:
   ```bash
   python scripts/build_extract.py $W/units.json $W/spec.txt -o $W/extract.txt
   ```
   Script ghép đúng thứ tự gốc, giữ nguyên văn, in số từ và tỉ lệ. Chỉnh spec cho tới khi tỉ lệ nằm trong khoảng mục tiêu. Nếu cần cắt thành phần phụ ở đầu câu, sửa trực tiếp `extract.txt` (chỉ được xóa chữ, không viết thêm), bước kiểm tra sẽ xác nhận câu vẫn là nguyên văn.

### Bước 3 - Biên soạn bản tóm tắt

Đọc lại `references/phong-cach.md` mục phong cách đã chọn (cách trình bày, bullet, tiêu đề, các lỗi cần tránh) rồi viết bản biên soạn vào `$W/abstract.txt` (mỗi đoạn một dòng, các đoạn cách nhau bằng xuống dòng).

Nguyên tắc cốt lõi:
- **Trọng tâm là các ý trong bản trích chọn**, tổng hợp và diễn đạt lại cho mạch lạc. Được bổ sung chi tiết ngoài bản trích chọn nếu chi tiết đó có trong văn bản gốc.
- **Bám sát bản gốc**: không suy diễn, không thêm ý, không thêm chi tiết ngoài bản gốc (kể cả điều đúng). Giữ nguyên tên riêng, số liệu, ngày tháng, địa điểm, thuật ngữ, cụm từ chỉ đạo. Không làm tròn số; dùng dấu phẩy thập phân đúng như bản gốc.
- **Cùng phong cách với bản gốc**, đủ để bản tóm tắt đại diện cho bản gốc. Giữ lập trường, sắc thái, giọng văn và các diễn đạt trang trọng mang tính thuyết phục.
- **Dùng lại từ ngữ của bản gốc** ở những chỗ không cần đổi. BARTScore đo mức độ bản tóm tắt "sinh ra được" từ văn bản gốc, nên thay từ đồng nghĩa không cần thiết chỉ làm điểm xuống mà không làm bài hay hơn.
- Đưa vào các nội dung chính của bản gốc kể cả thông tin chưa được kiểm chứng; chỉ gắn rõ nguồn phát ngôn hoặc dùng cách nói thận trọng ("theo...", "cho rằng...", "bị cáo buộc..."). Xem mục nhạy cảm trong `references/phong-cach.md`.
- Sửa lỗi chính tả có trong bản gốc; không viết tắt tùy tiện; ngữ pháp và dấu câu nhất quán.
- Không cần phần kết luận, chỉ thêm kết luận chung khi giúp mạch lạc hơn (nhất là đa văn bản).
- Có thể điều chỉnh lại bản trích chọn nếu khó đạt độ dài phù hợp.

**Độ dài bản biên soạn so với bản trích chọn** (cần thỏa mãn một trong hai):
- ngắn hơn: số từ biên soạn / số từ trích chọn **≤ 0,70**; hoặc
- dài hơn: số từ trích chọn / số từ biên soạn **≤ 0,70**.

Mặc định chọn hướng ngắn hơn (khoảng 55%-68% bản trích chọn). Giới hạn này khá chặt vì bản trích chọn vốn đã là bản rút gọn: dòng tiêu đề cũng tính vào số từ, nên viết xong hãy đếm rồi cắt chi tiết phụ (không cắt ý chính) cho tới khi đạt. Chọn hướng dài hơn khi bản trích chọn đã rất ngắn hoặc cần thêm chi tiết từ văn bản gốc để bản tóm tắt đủ ý. Khoảng cách này cũng bảo đảm chỉ tiêu Edit ≥ 0,1.

### Bước 4 - Kiểm tra

```bash
python scripts/check.py $W/units.json --style "<nhãn>" --extract $W/extract.txt --abstract $W/abstract.txt
```

Script kiểm tra: tỉ lệ nén (0,1-0,5), tỉ lệ biên soạn/trích chọn và Edit, bản trích chọn có đúng nguyên văn và đúng thứ tự không, định dạng theo phong cách (bullet, tiêu đề, dấu hai chấm xuống dòng), dấu thập phân, số liệu trong bản biên soạn có xuất hiện trong bản gốc không, mức độ bám bản gốc. Sửa mọi mục `LỖI`, xem xét từng mục `CẢNH BÁO` (có thể là báo động giả, ví dụ số phiên bản bị nhận nhầm là số thập phân). Lặp lại cho tới khi sạch lỗi.

Script chỉ kiểm tra được phần cơ học. Sau khi sạch lỗi, tự đọc lại bản biên soạn một lần với văn bản gốc bên cạnh để soát những thứ máy không thấy: đúng chủ thể, đúng sắc thái, không biến giả thuyết thành kết luận, không trộn lẫn các sự kiện, không bỏ lời kêu gọi hay đối tượng bị phê phán.

### Bước 5 - Điền vào file và giao

```bash
python scripts/fill_docx.py "/mnt/user-data/uploads/<file>.docx" "/mnt/user-data/outputs/<file>.docx" \
  --style "<nhãn>" --extract $W/extract.txt --abstract $W/abstract.txt
```

Script điền 3 hàng cuối (tạo mới và tô vàng nếu file chưa có), giữ nguyên các hàng còn lại, rồi mở lại file để xác nhận. Giữ nguyên tên file gốc cho file đầu ra. Gọi `present_files` với (các) file đầu ra.

### Báo cáo cho người dùng

Ngắn gọn, gồm: nhãn phong cách và mức độ chắc chắn (nêu lý do nếu là ca khó), số từ gốc / trích chọn / biên soạn cùng các tỉ lệ, và những điểm cần người dùng xem lại (cảnh báo còn lại, chỗ phải đoán, nội dung nhạy cảm đã xử lý ra sao). Không dán lại toàn bộ bản tóm tắt vào khung chat, người dùng sẽ mở file.
