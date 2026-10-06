#!/usr/bin/env python3
"""CLI: solve / verify / detect."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import requests

from recaptcha_solver import RecaptchaError, RecaptchaSolver


def cmd_solve(a: argparse.Namespace) -> int:
    s = RecaptchaSolver(headless=not a.headed, timeout=a.timeout,
                        audio_rounds=a.rounds)
    try:
        token = s.solve_with_retries(a.url, a.sitekey, retries=a.retries)
    except RecaptchaError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    if a.out:
        Path(a.out).write_text(token, encoding="utf-8")
        print(f"saved to {a.out} (len={len(token)})")
    else:
        print(token)
    return 0


def cmd_verify(a: argparse.Namespace) -> int:
    s = RecaptchaSolver(headless=not a.headed, timeout=a.timeout)
    for i in range(1, a.retries + 1):
        print(f"[verify] attempt {i}/{a.retries}", flush=True)
        try:
            ok, token = s.verify_demo()
            print(f"[verify] token len={len(token)} ok={ok}")
            if ok:
                print("OK - Google accepted the token")
                return 0
        except Exception as e:  # noqa: BLE001
            print(f"[verify] FAIL {type(e).__name__}: {e}")
    return 1


def cmd_detect(a: argparse.Namespace) -> int:
    r = requests.get(a.url, headers={"User-Agent": "Mozilla/5.0 Chrome/131"},
                     timeout=30)
    r.raise_for_status()
    html = r.text
    m = re.search(r'data-sitekey=["\']([A-Za-z0-9_-]{20,})["\']', html)
    sitekey = m.group(1) if m else None
    kind = "v2"
    if re.search(r"grecaptcha\.execute\(|api\.js\?render=", html):
        kind = "v3?"
    if re.search(r'data-size=["\']invisible["\']', html, re.I):
        kind = "v2_invisible"
    print(json.dumps({"url": r.url, "sitekey": sitekey, "type": kind}, indent=2))
    return 0 if sitekey else 1


def build() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="local reCAPTCHA v2 audio solver")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("solve", help="open URL, solve, print token")
    s.add_argument("--url", required=True)
    s.add_argument("--sitekey", default=None)
    s.add_argument("--timeout", type=float, default=120)
    s.add_argument("--retries", type=int, default=2)
    s.add_argument("--rounds", type=int, default=5)
    s.add_argument("--headed", action="store_true", help="show browser window")
    s.add_argument("--out", default=None)
    s.set_defaults(func=cmd_solve)
    v = sub.add_parser("verify", help="end-to-end check on official demo")
    v.add_argument("--timeout", type=float, default=120)
    v.add_argument("--retries", type=int, default=3)
    v.add_argument("--headed", action="store_true")
    v.set_defaults(func=cmd_verify)
    d = sub.add_parser("detect", help="extract sitekey from a page")
    d.add_argument("url")
    d.set_defaults(func=cmd_detect)
    return p


def main(argv=None) -> int:
    a = build().parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
