# recaptcha-v2-audio-solver

Local reCAPTCHA v2 solver. No captcha farms, no API keys.

A real Chromium browser (Playwright) clicks the checkbox. If an image
challenge pops up, it switches to the **audio challenge**, transcribes it
with free speech recognition, submits the answer, and returns the
`g-recaptcha-response` token.

## Install

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

`ffmpeg` is bundled via `imageio-ffmpeg` (no system package needed).
Audio transcription uses Google's free speech API by default, with an
optional local [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
fallback (`pip install faster-whisper`).

## CLI

```bash
# solve a page and print the token
python demo.py solve --url "https://example.com/signup" --sitekey "6Le..." --out token.txt

# headed mode (passes bot checks more often)
python demo.py solve --url "..." --headed

# end-to-end check on Google's official demo
python demo.py verify
python demo.py verify --headed

# extract sitekey from a page
python demo.py detect "https://example.com/signup"
```

On servers without a display, use `xvfb-run` with headed mode:

```bash
xvfb-run -a python demo.py verify --headed
```

## HTTP API

```bash
CAPTCHA_PORT=6768 MAX_CONCURRENT_SOLVES=3 HEADLESS=true python -m recaptcha_solver.server
# Web UI: http://localhost:6768
```

```bash
curl "http://localhost:6768/solve?type=recaptcha&url=https://example.com&sitekey=6Le...&timeout=120"
# {"success":true,"token":"03AG...","time":12.3,"type":"recaptcha"}

curl http://localhost:6768/health
curl http://localhost:6768/stats
```

| var | default | meaning |
|---|---|---|
| `CAPTCHA_PORT` | 6768 | listen port |
| `MAX_CONCURRENT_SOLVES` | 3 | parallel solves (1 solve = 1 browser) |
| `SOLVE_TIMEOUT` | 120 | default timeout (s) |
| `HEADLESS` | true | `false` for visible browser |

## Library

```python
from recaptcha_solver import RecaptchaSolver

solver = RecaptchaSolver(headless=True, timeout=120)
token = solver.solve_with_retries("https://example.com", "6Le...", retries=2)
# POST it as `g-recaptcha-response` within ~2 minutes (tokens expire)
```

See [example.py](example.py).

## How it works

```
checkbox click (human-like bezier mouse)
  -> solved? return token
  -> challenge open? switch to audio
    -> download mp3 (allowlisted google hosts only, 10MB cap)
    -> mp3 -> wav (bundled ffmpeg) -> STT (Google free API, whisper fallback)
    -> word-to-digit normalize ("one two three four" -> "1234")
    -> type answer, Verify, read token
```

## Tests

```bash
python -m unittest discover -s recaptcha_solver/tests -v
```

Offline only (normalization, URL validation). For a live check run
`python demo.py verify --headed`.

## Limitations (honest)

- reCAPTCHA v2 checkbox only (no v3 / hCaptcha / Turnstile / Enterprise).
- `try again later` / `automated queries` = Google bot wall (IP, headless,
  velocity). `--headed` + spacing helps. No guaranteed success rate.
- Audio is sent to Google's speech API. STT can mis-hear; the solver retries
  (default 5 audio rounds).
- Use only on sites you own or are authorized to test.

## License

MIT — see [LICENSE](LICENSE).
