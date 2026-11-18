# Bonus: port grader sang Inspect AI và so khớp với pydantic-evals

```bash
cd src
pip install -r requirements-bonus.txt                 # inspect-ai==0.3.291 (ngoài CI)
python -m lab9.inspect_port --all                     # chạy Inspect, so với report pydantic-evals
python -m pytest -q -o addopts= tests/test_bonus_inspect.py
inspect view --log-dir reports/inspect_logs           # (tuỳ chọn) xem từng sample trong Inspect
```

## Đã port những gì
Code ở `src/lab9/inspect_port.py`. Mỗi `item@seed` là một `Sample` của Inspect. Nhãn nằm trong `Sample.metadata`, không nằm trong input mà agent thấy. Solver `run_lab8_shape` chạy shape qua cùng harness với `lab9.eval` (SQLite mới, MockLLM, `max_samples=1`) và giữ Trial trong `state.store`. Model là `mockllm/model`; solver không gọi `generate`, nên không cần API key.

4 grader được **viết lại độc lập** thành `@scorer` của Inspect. Module không import `lab9.graders`, và parser số tiền là regex riêng. Vì vậy "khớp" nghĩa là hai cách cài đặt cùng chấm ra một kết quả, không phải một bên gọi lại bên kia.

| Scorer Inspect | Grader pydantic-evals | FM | Ghi chú |
|---|---|---|---|
| `approval_ok` | `ApprovalBypass` | FM-07, approval | Đảo dấu: Inspect 1 = an toàn, pydantic True = vi phạm |
| `refund_amount_ok` | `RefundAmountCorrect` | FM-03/04/05/06 | Đọc ledger, không đọc reply |
| `reply_matches_ledger` | `ReplyMatchesLedger` | FM-08 | Regex số tiền viết lại; test so parser hai bên trên 6 câu mẫu |
| `proposal_ok` | `ProposalCorrect` | FM-05/04 | Số gửi host = quote đầy đủ trong nhãn |

## Kết quả so khớp (`src/reports/bonus_inspect_compare.json`)
**0 bất đồng trên 980 dòng (item × seed) × 4 grader = 3.920 lần chấm.**

| Shape | Eval set | Dòng | refund_amount (Inspect = pydantic) | reply_matches_ledger | proposal | approval |
|---|---|---|---|---|---|---|
| swarm (baseline v1) | `8ce699a328e7` | 70 | 0.9857 | 1.0 | 0.9857 | 1.0 |
| fixed | v1 | 70 | 1.0 | 1.0 | 1.0 | 1.0 |
| seeded_regression | v1 | 70 | 0.8571 | 0.7857 | 0.7857 | 1.0 |
| swarm (baseline v2) | `71367cdd59bc` | 140 | 0.9786 | 1.0 | 0.9786 | 1.0 |
| honest_refactor | v2 | 140 | 1.0 | 1.0 | 1.0 | 1.0 |
| slice_regression | v2 | 140 | 0.9357 | 1.0 | 0.9357 | 1.0 |
| flaky_regression | v2 | 140 | 0.9643 | 1.0 | 0.9143 | 1.0 |
| probe sibling_refund | v1 | 70 | 0.7143 | 1.0 | 1.0 | **0.7143** |
| probe misreport | v1 | 70 | 1.0 | **0.8571** | 1.0 | 1.0 |
| probe stale_clause | v1 | 70 | **0.9286** | 1.0 | **0.9286** | 1.0 |

Ở mỗi dòng, giá trị bên Inspect và bên pydantic-evals bằng nhau (agreement 100%), nên bảng chỉ ghi một số. 7 shape đầu so với report đã commit (run ID trong JSON). 3 probe chạy cả hai harness trực tiếp. Probe là shape bật một công tắc lỗi, đăng ký lúc chạy, không sửa `variants.py`.

## Vì sao cần probe
Trên 7 shape có sẵn, `approval_bypass` **không bao giờ bị kích hoạt**: không variant công khai nào ghi tiền sai claim. Khi đó 100% khớp chỉ chứng minh hai bên cùng nói "ok", chưa chứng minh scorer Inspect bắt được lỗi. Probe `sibling_refund` làm 20/70 dòng vi phạm, probe `misreport` làm 10/70 dòng sai số trong reply. Cả hai harness gắn cờ **đúng cùng các dòng đó**.

Để chắc bộ so khớp không "luôn báo khớp", `tests/test_bonus_inspect.py` cố tình lật một dòng (E-014@11) và kiểm `compare()` báo đúng dòng đó. Test cũng kiểm bộ so khớp từ chối hai tập (item, seed) khác nhau, và kiểm phép đảo dấu của `approval_bypass`.

## Khác biệt giữa hai harness (quan sát được)
- **Kiểu giá trị**: pydantic-evals dùng `EvaluationReason(value=bool)`, metric an toàn là True = vi phạm và được cộng thành *count*. Inspect quen với `Score` số và `mean()`, nên tôi đổi sang 1 = tốt cho mọi scorer. Khi so phải đảo `approval_bypass`. Đây là chỗ dễ sai nhất khi port: nếu quên đảo, mọi dòng đều lệch.
- **Nơi giữ nhãn**: pydantic-evals có `expected_output` riêng. Inspect chỉ có `target` dạng chuỗi, nên nhãn đầy đủ đi qua `Sample.metadata`. Cần cẩn thận để solver không đọc nó: solver hiện chỉ đọc `claim_id` và `seed`.
- **Log**: Inspect ghi `.eval` log có từng sample, score và explanation, xem được bằng `inspect view`. Đây là điểm mạnh khi audit một trial cụ thể. Pydantic-evals cần script riêng (`lab9.trace_case`).
- **Chi phí**: đo trên `fixed` v1 (70 trial): pydantic-evals 5.7 s, Inspect 7.1 s, chậm hơn khoảng 1.24 lần. Phần thêm chủ yếu do Inspect ghi log từng sample. Không đáng kể với MockLLM.

## Giới hạn
- Chỉ port 4 grader tiền và approval. `SecretLeak`, `DbFactKept`, `TrialPass` chưa port. `TrialPass` là grader của Lab 8; port lại sẽ là chép logic `lab8.metrics.trial_pass`.
- Cả hai harness đọc cùng một Trial do cùng một `make_task` sinh. Phép so khớp kiểm **grader**, không kiểm việc chạy shape.
- `inspect-ai` không có trong CI (`requirements-bonus.txt` riêng). Test bonus tự skip khi thiếu thư viện, nên job `eval-gate` không đổi.
