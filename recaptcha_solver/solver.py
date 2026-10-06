"""reCAPTCHA v2を解く (Playwright)。checkbox→音声認証→token取得。"""
from __future__ import annotations

import random
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

from .audio import AudioError, transcribe_mp3_bytes, validate_audio_url

ANCHOR_SEL = 'iframe[src*="recaptcha"][src*="anchor"]'
BFRAME_SEL = 'iframe[src*="recaptcha"][src*="bframe"]'

TOKEN_JS = """() => {
  const areas = document.querySelectorAll(
    '#g-recaptcha-response, textarea[name="g-recaptcha-response"]');
  for (const el of areas) {
    if (el.value && el.value.length > 40) return el.value;
  }
  try { if (window.grecaptcha && grecaptcha.getResponse) {
    const t = grecaptcha.getResponse(); if (t) return t; } } catch (e) {}
  return null;
}"""

LANG_MAP = {"en": "en-US", "en-US": "en-US", "en-GB": "en-GB",
            "ja": "ja-JP", "ja-JP": "ja-JP"}


class RecaptchaError(RuntimeError):
    pass


def _log(msg: str) -> None:
    print(msg, flush=True)


def _jitter(a: float, b: float) -> None:
    time.sleep(random.uniform(a, b))


def _bezier(p0, p1, p2, p3, t) -> float:
    u = 1.0 - t
    return u**3 * p0 + 3 * u * u * t * p1 + 3 * u * t * t * p2 + t**3 * p3


def human_click(page, x: float, y: float) -> None:
    """ベジェ曲線で人間っぽくクリック。"""
    sx, sy = getattr(page, "_mouse_pos",
                      (random.randint(40, 240), random.randint(40, 180)))
    dx, dy = x - sx, y - sy
    c1x = sx + dx * random.uniform(0.15, 0.4) + random.uniform(-80, 80)
    c1y = sy + dy * random.uniform(0.05, 0.35) + random.uniform(-80, 80)
    c2x = sx + dx * random.uniform(0.55, 0.85) + random.uniform(-80, 80)
    c2y = sy + dy * random.uniform(0.6, 0.95) + random.uniform(-80, 80)
    steps = random.randint(22, 38)
    for i in range(steps + 1):
        t = i / steps
        te = t * t * (3 - 2 * t)
        page.mouse.move(_bezier(sx, c1x, c2x, x, te) + random.uniform(-0.6, 0.6),
                        _bezier(sy, c1y, c2y, y, te) + random.uniform(-0.6, 0.6))
        page.wait_for_timeout(random.randint(4, 16))
    page.wait_for_timeout(random.randint(60, 220))
    page.mouse.down()
    page.wait_for_timeout(random.randint(35, 110))
    page.mouse.up()
    page._mouse_pos = (x, y)


class RecaptchaSolver:
    """1 solveごとに新しいブラウザで解く。"""

    DEMO_URL = "https://www.google.com/recaptcha/api2/demo"
    DEMO_SITEKEY = "6Le-wvkSAAAAAPBMRTvw0Q4Muexq9bi0DJwx_mJ-"

    def __init__(self, headless: bool = True, timeout: float = 120.0,
                 audio_rounds: int = 5, lang: str = "en-US",
                 proxy: Optional[dict] = None,
                 on_log: Callable[[str], None] | None = None):
        self.headless = headless
        self.timeout = timeout
        self.audio_rounds = audio_rounds
        self.lang = lang
        self.proxy = proxy
        self._log = on_log or _log

    def solve(self, url: str, sitekey: Optional[str] = None) -> str:
        """URLを開いて解き、tokenを返す。"""
        from playwright.sync_api import sync_playwright
        profile = Path(tempfile.mkdtemp(prefix="rc-chrome-"))
        try:
            with sync_playwright() as pw:
                browser = self._launch(pw, profile)
                try:
                    page = browser.pages[0] if browser.pages else browser.new_page()
                    self._apply_stealth(page)
                    return self._solve_on_page(page, url, sitekey)
                finally:
                    browser.close()
        finally:
            shutil.rmtree(profile, ignore_errors=True)

    def solve_with_retries(self, url: str, sitekey: Optional[str] = None,
                           retries: int = 2) -> str:
        last: str = "unknown"
        for i in range(1, retries + 1):
            try:
                self._log(f"[solve] attempt {i}/{retries}")
                return self.solve(url, sitekey)
            except Exception as e:  # noqa: BLE001 - 新ブラウザでリトライ
                last = f"{type(e).__name__}: {e}"
                self._log(f"[solve] FAIL {last}")
                time.sleep(2)
        raise RecaptchaError(f"giving up after {retries}: {last}")

    def verify_demo(self) -> tuple[bool, str]:
        """公式デモで動作確認。(ok, token)を返す。"""
        from playwright.sync_api import sync_playwright
        profile = Path(tempfile.mkdtemp(prefix="rc-chrome-"))
        try:
            with sync_playwright() as pw:
                browser = self._launch(pw, profile)
                try:
                    page = browser.pages[0] if browser.pages else browser.new_page()
                    self._apply_stealth(page)
                    token = self._solve_on_page(page, self.DEMO_URL,
                                                self.DEMO_SITEKEY)
                    try:
                        page.locator("#recaptcha-demo-submit").click(timeout=5000)
                    except Exception:
                        pass
                    page.wait_for_timeout(1500)
                    ok = "Verification Success" in (page.content() or "")
                    return ok, token
                finally:
                    browser.close()
        finally:
            shutil.rmtree(profile, ignore_errors=True)

    def _launch(self, pw, profile: Path):
        args = [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run", "--no-default-browser-check",
            "--window-size=1920,1080", "--lang=en-US",
        ]
        kwargs: dict = dict(
            headless=self.headless,
            locale="en-US", timezone_id="America/New_York",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            ignore_default_args=["--enable-automation"],
            args=args,
        )
        if self.proxy:
            kwargs["proxy"] = self.proxy
        ctx = pw.chromium.launch_persistent_context(str(profile), **kwargs)
        try:
            ctx.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        except Exception:
            pass
        return ctx

    def _apply_stealth(self, page) -> None:
        try:
            from playwright_stealth import Stealth
            Stealth().apply_stealth(page)
        except Exception:
            pass

    def _force_english(self, page) -> None:
        def _rewrite(route):
            u = route.request.url
            if "recaptcha" in u and "hl=" in u:
                u = re.sub(r"hl=[^&]*", "hl=en", u)
            elif "recaptcha/api.js" in u and "hl=" not in u:
                u += ("&" if "?" in u else "?") + "hl=en"
            route.continue_(url=u)
        try:
            page.route("**/*recaptcha*", _rewrite)
        except Exception:
            pass

    def _iframe_lang(self, page) -> str:
        try:
            src = page.locator(ANCHOR_SEL).first.get_attribute("src") or ""
        except Exception:
            src = ""
        m = re.search(r"[?&]hl=([^&]+)", src)
        hl = m.group(1) if m else self.lang
        return LANG_MAP.get(hl, LANG_MAP.get(hl.split("-")[0], "en-US"))

    def _solve_on_page(self, page, url: str, sitekey: Optional[str]) -> str:
        self._force_english(page)
        self._log(f"[solver] goto {url}")
        page.goto(url, wait_until="domcontentloaded",
                  timeout=int(self.timeout * 1000))
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        _jitter(1.2, 2.4)

        token = page.evaluate(TOKEN_JS)
        if token:
            return token

        if page.locator(ANCHOR_SEL).count() == 0:
            if sitekey:
                self._inject_widget(page, sitekey)
            else:
                try:
                    page.wait_for_selector(ANCHOR_SEL, timeout=25000)
                except Exception:
                    raise RecaptchaError("no reCAPTCHA iframe on page")

        _jitter(0.6, 1.4)
        self._click_checkbox(page)
        return self._wait_token_loop(page, time.time() + self.timeout)

    def _wait_token_loop(self, page, deadline: float) -> str:
        tries = 0
        while time.time() < deadline:
            token = page.evaluate(TOKEN_JS)
            if token:
                self._log(f"[solver] got token len={len(token)}")
                return token
            if self._blocked(page):
                raise RecaptchaError("google served 'try again later' (bot wall)")
            if self._checkbox_checked(page):
                page.wait_for_timeout(800)
                token = page.evaluate(TOKEN_JS)
                if token:
                    return token
            if self._challenge_open(page):
                if tries >= self.audio_rounds:
                    raise RecaptchaError("audio retries exhausted")
                tries += 1
                self._log(f"[solver] audio round {tries}/{self.audio_rounds}")
                self._solve_audio_round(page)
            page.wait_for_timeout(400)
        raise RecaptchaError("timeout waiting for token")

    def _inject_widget(self, page, sitekey: str) -> None:
        page.evaluate("""(key) => {
          if (document.querySelector('.g-recaptcha')) return;
          const box = document.createElement('div');
          box.className = 'g-recaptcha';
          box.setAttribute('data-sitekey', key);
          document.body.prepend(box);
          const s = document.createElement('script');
          s.src = 'https://www.google.com/recaptcha/api.js?hl=en';
          s.async = true; s.defer = true;
          document.head.appendChild(s);
        }""", sitekey)
        page.wait_for_selector(ANCHOR_SEL, timeout=30000)

    def _click_checkbox(self, page) -> None:
        loc = page.locator(ANCHOR_SEL).first
        loc.wait_for(state="visible", timeout=30000)
        box = loc.bounding_box()
        if not box:
            raise RecaptchaError("anchor iframe has no box")
        x = box["x"] + min(28, box["width"] * 0.12) + random.uniform(-3, 3)
        y = box["y"] + box["height"] / 2 + random.uniform(-4, 4)
        human_click(page, x, y)

    def _checkbox_checked(self, page) -> bool:
        try:
            f = page.frame_locator(ANCHOR_SEL)
            return f.locator("#recaptcha-anchor[aria-checked='true']").count() > 0
        except Exception:
            return False

    def _challenge_open(self, page) -> bool:
        try:
            loc = page.locator(BFRAME_SEL)
            if loc.count() == 0:
                return False
            box = loc.first.bounding_box()
            return bool(box) and box["width"] > 50 and box["height"] > 50
        except Exception:
            return False

    def _blocked(self, page) -> bool:
        try:
            body = page.frame_locator(BFRAME_SEL).locator("body").inner_text(
                timeout=500)
        except Exception:
            return False
        low = body.lower()
        return ("try again later" in low or "automated queries" in low
                or "your computer or network" in low)

    def _audio_src(self, page) -> Optional[str]:
        b = page.frame_locator(BFRAME_SEL)
        sels = ("#audio-source", "audio#audio-source", "audio source", "audio",
                ".rc-audiochallenge-tdownload-link", "a[href*='payload']")
        for _ in range(12):
            for sel in sels:
                try:
                    loc = b.locator(sel).first
                    if loc.count() == 0:
                        continue
                    src = loc.get_attribute("src") or loc.get_attribute("href")
                    if src:
                        if src.startswith("//"):
                            src = "https:" + src
                        elif src.startswith("/"):
                            src = "https://www.google.com" + src
                        return src
                except Exception:
                    continue
            page.wait_for_timeout(250)
        return None

    def _solve_audio_round(self, page) -> None:
        b = page.frame_locator(BFRAME_SEL)
        audio_btn = b.locator("#recaptcha-audio-button, .rc-button-audio")
        audio_input = b.locator("#audio-response")
        try:
            audio_btn.wait_for(state="visible", timeout=15000)
        except Exception:
            raise RecaptchaError("audio button not visible")
        _jitter(0.8, 1.6)

        if not audio_input.is_visible():
            self._log("[solver] switching to audio challenge")
            try:
                box = audio_btn.bounding_box()
                if box:
                    human_click(page, box["x"] + box["width"] / 2,
                                box["y"] + box["height"] / 2)
                else:
                    audio_btn.click(timeout=5000)
            except Exception:
                try:
                    audio_btn.click(force=True)
                except Exception as e:
                    raise RecaptchaError(f"cannot click audio button: {e}")
            try:
                audio_input.wait_for(state="visible", timeout=12000)
            except Exception:
                if self._blocked(page):
                    raise RecaptchaError("try again later after audio switch")
                raise RecaptchaError("audio UI did not appear")

        if self._blocked(page):
            raise RecaptchaError("try again later after audio switch")

        _jitter(0.4, 0.9)
        src = self._audio_src(page)
        if not src:
            try:
                b.locator("#recaptcha-reload-button").click(timeout=3000)
                _jitter(1.0, 2.0)
            except Exception:
                pass
            src = self._audio_src(page)
        if not src:
            raise RecaptchaError("no audio source in challenge iframe")

        try:
            validate_audio_url(src)
        except AudioError as e:
            raise RecaptchaError(str(e))

        self._log("[solver] downloading audio...")
        resp = page.request.get(src, timeout=15000)
        if not resp.ok:
            raise RecaptchaError(f"audio download HTTP {resp.status}")
        data = resp.body()
        self._log(f"[solver] audio bytes={len(data)}")

        try:
            raw, answer = transcribe_mp3_bytes(data, self._iframe_lang(page))
        except AudioError as e:
            raise RecaptchaError(str(e))
        self._log(f"[solver] stt -> {answer!r}")
        if not answer:
            raise RecaptchaError("empty transcription")

        inp = b.locator("#audio-response")
        inp.wait_for(state="visible", timeout=10000)
        inp.click()
        _jitter(0.15, 0.4)
        inp.press_sequentially(answer, delay=random.randint(55, 130))
        _jitter(0.25, 0.6)
        b.locator("#recaptcha-verify-button").click()
        _jitter(1.5, 2.8)
