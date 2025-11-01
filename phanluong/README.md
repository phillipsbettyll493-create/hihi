# JinShuaiGe Pro Max Premium — Queued with ORIGINAL UI
- UI giữ nguyên 100% như bản bạn gửi (form/result).
- Thêm trang `/progress/<job_id>` để xem tiến độ, backend chạy nền với giới hạn luồng (`MAX_CONCURRENCY`).

## Run
```bash
pip install -r requirements.txt
export OPENROUTER_API_KEY="sk-or-xxxxx"   # Windows PowerShell: $Env:OPENROUTER_API_KEY="sk-or-xxxxx"
python app.py
# -> http://localhost:5000
```
