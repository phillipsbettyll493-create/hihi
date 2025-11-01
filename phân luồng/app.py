import os
import random
import urllib.parse
import uuid
import threading
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Flask, request, render_template, jsonify, redirect, url_for
import requests

app = Flask(__name__)

# === CONFIG ===
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_URL = os.getenv("OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions")
MODEL = os.getenv("OPENROUTER_MODEL", "gpt-4o-mini")
MAX_CONCURRENCY = int(os.getenv("MAX_CONCURRENCY", "3"))  # số luồng tối đa khi gọi API
JOB_TTL_MINUTES = int(os.getenv("JOB_TTL_MINUTES", "30")) # cleanup sau X phút

# === JOB STORE (in-memory) ===
JOBS = {}   # job_id -> { created_at, total, articles:[], done, error, progress, main_kw, main_link }
JOBS_LOCK = threading.Lock()

# === CLEANUP DAEMON ===
def _cleanup_loop():
    while True:
        time.sleep(60)
        expire_before = datetime.utcnow() - timedelta(minutes=JOB_TTL_MINUTES)
        with JOBS_LOCK:
            for jid in list(JOBS.keys()):
                st = JOBS.get(jid)
                if st and st.get("created_at") < expire_before:
                    JOBS.pop(jid, None)

threading.Thread(target=_cleanup_loop, daemon=True).start()

# === AI content function (from your original, but key lấy từ ENV) ===
def generate_ai_content(main_kw, main_link, sub1="", sub2=""):
    styles = [
        "以旅行笔记的方式书写，语气轻快自然。",
        "以文化观察的角度表达，句式独特，有深度。",
        "以生活体验的语气叙述，细腻而真实。",
        "以专业解读的风格呈现，逻辑清晰但不生硬。",
        "以故事叙述的节奏展开，句式灵活，有画面感。",
        "以感受性语言表达主题，带有情绪起伏。",
        "以轻松科普的语气介绍，信息自然融入内容。",
        "以讨论式语气撰写，带一点思考感。",
    ]
    tone_variations = [
        "语言不拘一格，句式有长有短，节奏感自然变化。",
        "不要使用逻辑连接词，如：首先、其次、最后、因此、总之。",
        "句子之间保持意境流动，不强调结构逻辑。",
        "用不规则的句式表达内容，让语感更自由。",
        "避免标准化句子，让文字带一点不确定的呼吸感。",
    ]
    style = random.choice(styles)
    tone = random.choice(tone_variations)
    temperature = round(random.uniform(0.65, 0.95), 2)

    prompt = f"""
你是一位中文SEO原创作者，请根据以下要求撰写一段自然内容。

主题：{main_kw}

写作要求：
1. 直接进入主题，不要有任何开场或引入语；
2. 全文长度约100字左右；
3. 自然地包含“{main_kw}”3至4次；
4. 同时自然提及“{sub1}”与“{sub2}”；
5. 每篇文字的句式、词汇、语气、结构都应不同；
6. 语言自然，不使用模板化句式；
7. {style}
8. {tone}
9. 只生成正文内容，不加标题、总结或符号。
"""

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    data = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "You are a professional Chinese SEO content writer."},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 400,
        "temperature": temperature,
    }
    try:
        if not OPENROUTER_API_KEY:
            raise RuntimeError("Thiếu OPENROUTER_API_KEY (chưa đặt biến môi trường).")
        r = requests.post(OPENROUTER_URL, json=data, headers=headers, timeout=30)
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"].strip()
        html_block = f'<a href="{main_link}" target="_blank">{main_kw} -【网址：{main_link}】- {text}</a>'
        return html_block
    except Exception as e:
        return f'<a href="{main_link}" target="_blank">{main_kw} -【网址：{main_link}】- AI生成失败：{e}</a>'

def build_article(idx, main_kw, main_link, sub_kw_list, sub_links, titles, pair_pointer, max_pairs):
    n = len(sub_kw_list)
    sub1 = sub_kw_list[idx % n]
    sub2 = sub_kw_list[(idx + 1) % n] if n > 1 else sub_kw_list[idx % n]
    title = f"{main_kw}-【网址：{main_link}】-{sub1}-{sub2}"
    ai_html = generate_ai_content(main_kw, main_link, sub1, sub2)

    embedded = []
    used_titles = set()
    for j in range(4):
        if not titles and not sub_links:
            break
        p_idx = (pair_pointer + j) % max_pairs
        t = titles[p_idx % len(titles)] if titles else f"Tiêu đề {p_idx+1}"
        l = sub_links[p_idx % len(sub_links)] if sub_links else "#"
        if t in used_titles:
            for scan in range(1, max_pairs):
                cand_idx = (p_idx + scan) % max_pairs
                cand_t = titles[cand_idx % len(titles)] if titles else f"Tiêu đề {cand_idx+1}"
                cand_l = sub_links[cand_idx % len(sub_links)] if sub_links else "#"
                if cand_t not in used_titles:
                    t, l = cand_t, cand_l
                    break
        used_titles.add(t)
        embedded.append({"html": f'<a href="{l}" target="_blank">{t}</a>'})
    bing_link = f"https://www.bing.com/search?q={urllib.parse.quote_plus(title)}"
    return {
        "no": idx + 1,
        "title": title,
        "ai_html": ai_html,
        "main_link": main_link,
        "embedded": embedded,
        "bing_link": bing_link
    }

def process_job(job_id, main_kw, sub_kw, main_link, sub_links, titles):
    try:
        n = len(sub_kw)
        pair_pointer = 0
        max_pairs = max(1, max(len(titles), len(sub_links)))

        with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as pool:
            futures = {}
            for idx, kw in enumerate(sub_kw):
                pp = pair_pointer
                fut = pool.submit(build_article, idx, main_kw, main_link, sub_kw, sub_links, titles, pp, max_pairs)
                futures[fut] = idx
                pair_pointer = (pair_pointer + 4) % max_pairs

            partial = [None] * len(sub_kw)
            for fut in as_completed(futures):
                idx = futures[fut]
                art = fut.result()
                partial[idx] = art
                with JOBS_LOCK:
                    st = JOBS.get(job_id)
                    if st:
                        st["articles"] = [a for a in partial if a]
                        total = st["total"] or 1
                        st["progress"] = len(st["articles"]) / total

        with JOBS_LOCK:
            st = JOBS.get(job_id)
            if st:
                st["articles"] = sorted(st["articles"], key=lambda a: a["no"])
                st["done"] = True
    except Exception as e:
        with JOBS_LOCK:
            st = JOBS.get(job_id)
            if st:
                st["error"] = str(e)
                st["done"] = True

# === ROUTES ===
@app.route("/", methods=["GET"])
def form():
    return render_template("form.html")

@app.route("/", methods=["POST"])
def start_job():
    main_kw = request.form.get("main_kw", "").strip()
    sub_kw = [x.strip() for x in request.form.get("sub_kw", "").splitlines() if x.strip()]
    main_link = request.form.get("main_link", "").strip()
    sub_links = [x.strip() for x in request.form.get("sub_links", "").splitlines() if x.strip()]
    titles = [x.strip() for x in request.form.get("titles", "").splitlines() if x.strip()]

    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {
            "created_at": datetime.utcnow(),
            "total": len(sub_kw),
            "articles": [],
            "done": False,
            "error": None,
            "progress": 0.0,
            "main_kw": main_kw,
            "main_link": main_link,
        }

    t = threading.Thread(target=process_job, args=(job_id, main_kw, sub_kw, main_link, sub_links, titles), daemon=True)
    t.start()

    return redirect(url_for("progress", job_id=job_id))

@app.route("/progress/<job_id>")
def progress(job_id):
    return render_template("progress.html", job_id=job_id)

@app.route("/api/result/<job_id>")
def api_result(job_id):
    with JOBS_LOCK:
        st = JOBS.get(job_id)
        if not st:
            return jsonify({"error": "job not found"}), 404
        payload = {k: v for k, v in st.items() if k != "created_at"}
    return jsonify(payload)

@app.route("/full/<job_id>")
def full(job_id):
    with JOBS_LOCK:
        st = JOBS.get(job_id)
        if not st:
            return "Job not found", 404
        arts = st.get("articles", [])
    return render_template("result.html", articles=arts)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
