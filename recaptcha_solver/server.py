"""HTTP API + Web UI.

GET /solve?type=recaptcha&url=<site>&sitekey=<key>[&timeout=60]
GET /health  GET /stats  GET /  (dashboard)

Concurrency: semaphore + one browser per solve (thread pool).
Tokens are returned in responses but never logged server-side.
"""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, jsonify, request

from .solver import RecaptchaError, RecaptchaSolver

PORT = int(os.environ.get("CAPTCHA_PORT", "6768"))
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT_SOLVES", "3"))
DEFAULT_TIMEOUT = float(os.environ.get("SOLVE_TIMEOUT", "120"))
HEADLESS = os.environ.get("HEADLESS", "true").lower() not in ("0", "false", "no")

app = Flask(__name__)
_sema = threading.Semaphore(MAX_CONCURRENT)
_pool = ThreadPoolExecutor(max_workers=MAX_CONCURRENT if MAX_CONCURRENT > 0 else 4)

_stats = {"total": 0, "ok": 0, "total_time": 0.0}
_stats_lock = threading.Lock()

INDEX_HTML = """<!doctype html><meta charset=utf-8><title>reCAPTCHA v2 audio solver</title>
<style>body{font-family:system-ui;max-width:640px;margin:40px auto;padding:0 16px}
input,select{width:100%;padding:8px;margin:6px 0}button{padding:10px 24px}
pre{background:#111;color:#0f0;padding:12px;white-space:pre-wrap}</style>
<h1>reCAPTCHA v2 audio solver</h1>
<p>Local only. Solves the audio challenge with free speech recognition.</p>
<label>URL<input id=url placeholder="https://..."></label>
<label>sitekey<input id=key placeholder="6Le..."></label>
<label>timeout (s)<input id=to value=120></label>
<button onclick="run()">RUN</button>
<pre id=out>-</pre>
<script>async function run(){
 out.textContent='solving...';
 const u='/solve?type=recaptcha&url='+encodeURIComponent(url.value)
  +'&sitekey='+encodeURIComponent(key.value)+'&timeout='+encodeURIComponent(to.value);
 const t0=Date.now();
 const r=await fetch(u); const j=await r.json();
 j.elapsed_client=((Date.now()-t0)/1000).toFixed(2)+'s';
 out.textContent=JSON.stringify({...j,token:j.token?j.token.slice(0,24)+'...':null},null,2);
}</script>"""


@app.get("/")
def index():
    return INDEX_HTML


@app.get("/health")
def health():
    return jsonify(ok=True, max_concurrent=MAX_CONCURRENT)


@app.get("/stats")
def stats():
    with _stats_lock:
        s = dict(_stats)
    avg = s["total_time"] / s["ok"] if s["ok"] else 0
    rate = s["ok"] / s["total"] if s["total"] else 0
    return jsonify(total=s["total"], ok=s["ok"], avg_time=round(avg, 2),
                  success_rate=round(rate, 3))


@app.get("/solve")
def solve():
    ctype = request.args.get("type", "recaptcha")
    url = request.args.get("url", "")
    sitekey = request.args.get("sitekey", "")
    try:
        timeout = float(request.args.get("timeout", DEFAULT_TIMEOUT))
    except ValueError:
        timeout = DEFAULT_TIMEOUT
    if ctype not in ("recaptcha", "recaptcha-v2", "recaptcha-invisible"):
        return jsonify(success=False, err="unsupported type (v2 only)",
                       type=ctype), 400
    if not url:
        return jsonify(success=False, err="missing url"), 400
    if not sitekey:
        return jsonify(success=False, err="missing sitekey"), 400

    t0 = time.time()
    fut = _pool.submit(_do_solve, url, sitekey, timeout)
    try:
        token = fut.result(timeout=timeout + 60)
    except Exception as e:  # timeout / solver error
        el = round(time.time() - t0, 2)
        with _stats_lock:
            _stats["total"] += 1
        app.logger.warning("solve failed url=%s err=%s", url[:80], str(e)[:200])
        return jsonify(success=False, err=str(e)[:300], type=ctype, time=el)
    el = round(time.time() - t0, 2)
    with _stats_lock:
        _stats["total"] += 1
        _stats["ok"] += 1
        _stats["total_time"] += el
    return jsonify(success=True, token=token, time=el, type=ctype)


def _do_solve(url: str, sitekey: str, timeout: float) -> str:
    if not _sema.acquire(blocking=True, timeout=timeout + 60):
        raise RecaptchaError("server busy")
    try:
        solver = RecaptchaSolver(headless=HEADLESS, timeout=timeout)
        return solver.solve(url, sitekey)
    finally:
        _sema.release()


def main():
    app.run(host="0.0.0.0", port=PORT, threaded=True)


if __name__ == "__main__":
    main()
