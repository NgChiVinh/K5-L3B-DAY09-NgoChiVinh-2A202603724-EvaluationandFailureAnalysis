# Trace case: từ trace tới quyết định sai đầu tiên

Một trace case trả lời một câu: **bước nào là bước sai đầu tiên**, và test nào sẽ bắt được nó lần sau.

```bash
cd src
python -m lab9.trace_case --shape seeded_regression --claim C-014 --seed 11
python -m lab9.trace_case --shape fixed --claim C-014 --seed 11   # đối chứng
```

Output (rút gọn):
```
== seeded_regression C-014@11  lab8 grader: FAIL (proposal_mismatch)
   1 handoff            {"contract_version": "handoff.v1", "request_id": "REQ-C-014", ...}
   2 investigator       {"lookup_order": {"visible": true, "reason": "ok", "order": {"order_id": "O-515", ...
   3 quote              {"amount_vnd": 150000, "reason": "entitled", "clause_ids": ["SHIP-LATE-03", "DMG-04"], ...
   4 refund_history     {"refund_count": 1, "refunded_vnd": 30000}
   5 proposal           {"amount_vnd": 120000}
   6 reply              {"text": "Dạ, LogiX đã hoàn 120.000đ cho đơn O-515."}
  refund_vnd     ledger=90000    label=120000   <-- differs
== fixed C-014@11  lab8 grader: PASS (ok)
   3 quote              {"amount_vnd": 150000, ...}
   4 reply              {"text": "Dạ, LogiX đã hoàn 120.000đ cho đơn O-515."}
  refund_vnd     ledger=120000   label=120000
```

| Trường | Nội dung |
|---|---|
| Trial | `seeded_regression C-014@11`, report `reports/eval_seeded_regression.json`, run ID `E-seeded_regression-20261118-8ce6` (row `E-014`, seed 11) |
| Triệu chứng | `pass_rate` FAIL (`proposal_mismatch`), `refund_amount_correct` FAIL, `proposal_correct` FAIL, `reply_matches_ledger` FAIL. Ledger ghi **90.000đ**, nhãn là 120.000đ, còn reply nói "đã hoàn **120.000đ**". |
| Bước sai đầu tiên | **Event 5 `proposal`** `{"amount_vnd": 120000}`: shape lấy quote 150.000 trừ 30.000 đã hoàn rồi mới gửi cho host. |
| Vì sao là bước *đầu tiên* | Event 1 `handoff` chỉ chứa field trong allowlist. Event 2 `investigator` thấy đơn O-515 thuộc đúng principal (`visible: true`). Event 3 `quote` = 150.000 khớp `proposal_amount` của nhãn (SHIP-LATE-03 + DMG-04, lấy max theo policy). Event 4 `refund_history` chỉ là đọc dữ liệu (30.000 đã hoàn), chưa sai. Event 5 là lúc số đề xuất lệch khỏi quote. Host áp trần theo đơn (150.000 − 30.000) lên số **đã bị trừ sẵn**, nên ra 90.000. |
| Failure mode | **FM-05 pre_deducted**. Hệ quả kéo theo là FM-08: reply báo số shape tự tính (120.000), không phải số ledger (90.000). |
| Đối chứng | `fixed C-014@11` không có event `refund_history` và `proposal`, gửi nguyên quote 150.000 sau event 3. Host tự trừ còn 120.000, reply 120.000đ, ledger 120.000, Lab 8 PASS. |
| Regression test | `src/tests/test_lab9_trace_case.py`: `test_trace_case_c014_pre_deducted_is_caught` kiểm event `proposal` đứng sau `refund_history`, ledger = 90.000, và `proposal_correct`, `refund_amount_correct`, `reply_matches_ledger`, `pass_rate` đều False. `test_trace_case_c014_fixed_passes` kiểm cùng claim@seed trên `fixed` pass mọi grader. Đã thấy fail trên variant lỗi và pass trên `fixed` (`pytest -m student -k lab9`: 25 passed). Ở mức eval set, grader `proposal_correct` (metric chặn) bắt cả 5 seed của E-014 trên `seeded_regression`. |
| Chưa chắc chắn | Trace không ghi quyết định của host (`issue_refund` trả `ok/reason`) nên phải suy ra cách host trừ trần từ ledger và `docs/business_rules.md` (T14), không thấy trực tiếp trên trace. Trace cũng không phân biệt shape "cố ý" trừ hay trừ do đọc sai field: chỉ biết số gửi đi ≠ quote. |

## Quy tắc

- Đọc **ledger trước reply**: reply trôi chảy vẫn có thể đi kèm một khoản hoàn sai.
- "Bước sai đầu tiên" phải dẫn số event cụ thể, không phải "agent bị nhầm".
- Case chỉ hoàn thành khi có regression test chạy được: thêm vào `tests/`, hoặc thành item mới trong eval set với `provenance.from_trace`.
