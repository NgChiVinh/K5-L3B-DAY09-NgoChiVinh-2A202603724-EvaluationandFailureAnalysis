# Judge audit

Chạy: `python -m lab9.judge --csv <file> --audit 5`

```
$ python -m lab9.judge --csv data/eval/judge_tone_public.csv --audit 10
n=100 P=0.857 R=0.8 kappa=0.757 {'tp': 24, 'fp': 4, 'fn': 6, 'tn': 66}
fail rate observed 0.28 -> corrected 0.3
PASS floors {'kappa': 0.6, 'precision': 0.8, 'recall': 0.7}

$ python -m lab9.judge --csv data/eval/judge_helpful_public.csv --audit 10
n=100 P=1.0 R=0.3 kappa=0.375 {'tp': 9, 'fp': 0, 'fn': 21, 'tn': 70}
fail rate observed 0.09 -> corrected 0.3
FAIL floors {'kappa': 0.6, 'precision': 0.8, 'recall': 0.7}
```
(FAIL là lớp dương: judge có nhiệm vụ *tìm* lỗi.)

| Tiêu chí | n | κ | Precision | Recall | Đạt FLOORS (κ≥0.6, P≥0.8, R≥0.7)? | Quyết định |
|---|---|---|---|---|---|---|
| tone_ok | 100 | 0.757 | 0.857 | 0.800 | **Đạt** | **dùng** (chỉ cho giọng văn, không chặn release một mình) |
| helpful | 100 | 0.375 | 1.000 | 0.300 | **Không đạt** (κ, R) | **loại** |

Rogan-Gladen: judge tone thấy 28% reply FAIL, sau hiệu chỉnh theo sens 0.8 và spec 66/70 thì tỉ lệ thật là **30%**, khớp tỉ lệ người gán (30/100). Judge helpful chỉ thấy 9%, tỉ lệ thật là **30%**: nó bỏ sót 2/3 số reply hỏng.

## ≥5 case audit thủ công
| # | Case | Judge nói | Người nói | Ai đúng? | Ghi chú / sửa rubric |
|---|---|---|---|---|---|
| 1 | H-008 "Em đã hoàn tiền cho anh rồi nha…" | PASS | FAIL | **Người** | Xưng "em" + "nha" là quá suồng sã. Rubric thêm: *xưng hô phải là "LogiX" / "anh/chị"; "em", "bạn", "nha", "nhé" là FAIL*. Kèm 2 ví dụ few-shot. |
| 2 | H-017 "Dạ, bạn ơi LogiX đã hoàn 25.000đ…" | PASS | FAIL | **Người** | Cùng lỗi xưng hô ("bạn ơi"). Áp chung quy tắc ở #1. Judge đang coi "thân thiện" là tốt; rubric phải nói rõ "lịch sự trang trọng > thân mật". |
| 3 | H-029 "Dạ, vui lòng chờ… không cần nhắn thêm." | PASS | FAIL | **Người** | Judge bị đánh lừa bởi "Dạ, vui lòng" ở đầu câu. Rubric thêm: *chấm cả câu; mệnh lệnh cấm khách liên hệ ("không cần nhắn thêm") là FAIL dù mở đầu lịch sự.* |
| 4 | H-042 "Dạ, đơn O-808 đã hoàn 120.000đ. Tiền về trong 3–5 ngày ạ." | FAIL | PASS | **Người** | Judge phạt "thiếu xin lỗi". Rubric bỏ yêu cầu ngầm "phải xin lỗi": *ngắn, đủ ý, có kính ngữ là PASS*. |
| 5 | H-051 "Dạ, LogiX đã refund 90.000đ…" | FAIL | PASS | **Người** | CS LogiX cho phép dùng "refund". Rubric thêm whitelist thuật ngữ được phép: refund, ship, COD. |
| 6 | H-081 "Dạ, đơn O-107 chưa đủ điều kiện hoàn…" | FAIL | PASS | **Judge** | Ghi chú nhãn: *người đọc nhầm ticket*. **Sửa nhãn người** sang FAIL, không sửa rubric. Sau khi sửa, tp tăng 1 và fp giảm 1 (P ≈ 0.893). Không sửa CSV công khai: ghi nhận ở đây và đưa vào vòng gán nhãn mới. |
| 7 | H-063 "…đã hoàn 20.000đ và sẽ hoàn thêm…" | PASS | FAIL | **Người** | Hứa ngoài kết quả hệ thống. Đây là lỗi **nội dung**, không phải giọng văn: chuyển sang grader code (reply chỉ được nêu số có trong ledger; mở rộng `ReplyMatchesLedger` để bắt cụm "hoàn thêm"). |
| 8 | H-070 "…ạ 😂😂" | PASS | FAIL | **Người** | Rubric thêm: *emoji trong ngữ cảnh khiếu nại là FAIL*. Có thể kiểm bằng code (regex emoji) rẻ hơn judge. |

## Vì sao `helpful` bị loại (dẫn ma trận nhầm lẫn)
- tp 9, fp 0, **fn 21**, tn 70: judge gần như không bao giờ nói FAIL. P = 1.0 chỉ vì nó hiếm khi lên tiếng, còn R = 0.3 nghĩa là **70% reply hỏng lọt qua**. κ = 0.375 < 0.6.
- Trong 21 ca bỏ sót có **18 ca `human_failure = "sai tiền"`**, ví dụ HL-015 "đã hoàn 55.000đ" trong khi ledger ghi 40.000đ, hay HL-043 nói 35.000đ trong khi ledger ghi 20.000đ. Judge không thấy ledger, nên **về cấu trúc không thể** chấm đúng tiền. Đây chính là FM-08, và nó được chấm bằng code (`reply_matches_ledger`, metric chặn), đúng nguyên tắc "tiền và approval luôn chấm bằng code".
- 3 ca còn lại là "sai xưng hô" ("Ok bạn, … nhé"), thuộc phạm vi của judge `tone`.
- Nếu dùng `helpful` làm gate, một PR làm hỏng số tiền trong reply sẽ được báo "tỉ lệ lỗi 9%" và **merge**. Gate chỉ chặn được những gì grader của nó nhìn thấy.

## Kết luận
- **Loại `helpful`** (κ 0.375, R 0.3 dưới sàn). Thay bằng: (a) code `reply_matches_ledger` cho phần tiền, (b) judge `tone` cho xưng hô.
- **Dùng `tone_ok`** để theo dõi chất lượng giọng văn, sau khi sửa rubric theo các ca #1–#5 và #8. Không cho nó chặn release một mình: R = 0.8 nghĩa là vẫn lọt 20% reply giọng văn kém.
- Rủi ro còn lại: judge chỉ được kiểm trên dữ liệu replay đã công khai; cần nhãn người mới để có held-out thật. Nhãn người cũng có lỗi (H-081), nên mỗi vòng gán nhãn cần người thứ hai đối chiếu các ca bất đồng.
