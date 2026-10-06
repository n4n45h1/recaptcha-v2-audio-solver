# recaptcha-v2-audio-solver

reCAPTCHA v2 の音声認証を使って解くローカルソルバーです。

Captcha代行サービスとかAPIキーは不要。
PlaywrightでChromiumを動かしてチェックボックスを押して、画像認証が出たら音声認証に切り替えて解きます。

音声を文字起こし → 回答送信 → g-recaptcha-response のtokenを取得、という流れです。

## Install

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

ffmpeg は imageio-ffmpeg 経由で入るので、別で入れなくても大丈夫です。

音声認識はデフォルトでGoogleの無料Speech APIを使います。

ローカルで完結させたい場合は faster-whisper も使えます。

```bash
pip install faster-whisper
```

## CLI

ページを開いてreCAPTCHAを解いて、tokenを保存：

```bash
python demo.py solve --url "https://example.com/signup" --sitekey "6Le..." --out token.txt
```

ブラウザを表示して動かす場合：

```bash
python demo.py solve --url "..." --headed
```

headlessよりheadedのほうがBot判定されにくいことがあります。

Google公式デモで動作確認：

```bash
python demo.py verify
python demo.py verify --headed
```

ページからsitekeyだけ取りたい場合：

```bash
python demo.py detect "https://example.com/signup"
```

画面のないサーバーなら xvfb-run でも動かせます。

```bash
xvfb-run -a python demo.py verify --headed
```

## HTTP API

APIとして起動することもできます。

```bash
CAPTCHA_PORT=6768 MAX_CONCURRENT_SOLVES=3 HEADLESS=true python -m recaptcha_solver.server
```

Web UI:

```
http://localhost:6768
```

solve:

```bash
curl "http://localhost:6768/solve?type=recaptcha&url=https://example.com&sitekey=6Le...&timeout=120"
```

返り値はこんな感じ：

```json
{
  "success": true,
  "token": "03AG...",
  "time": 12.3,
  "type": "recaptcha"
}
```

health / stats:

```bash
curl http://localhost:6768/health
curl http://localhost:6768/stats
```

| var | default | 内容 |
|---|---|---|
| CAPTCHA_PORT | 6768 | ポート |
| MAX_CONCURRENT_SOLVES | 3 | 同時に動かすsolver数 |
| SOLVE_TIMEOUT | 120 | timeout秒数 |
| HEADLESS | true | false でブラウザ表示 |

1 solveごとにChromiumを1つ使います。

## Library

Pythonから直接呼ぶこともできます。

```python
from recaptcha_solver import RecaptchaSolver
solver = RecaptchaSolver(headless=True, timeout=120)
token = solver.solve_with_retries(
    "https://example.com",
    "6Le...",
    retries=2
)
```

取得したtokenを g-recaptcha-response として送ればOKです。

tokenはずっと使えるわけじゃないので、取ったら早めに使ってください。

詳しくは example.py。

## How it works

だいたいこんな感じです。

```text
checkboxをクリック
  ↓
そのまま通った？
  ├─ yes → token取得
  │
  └─ no
      ↓
   audio challengeへ切り替え
      ↓
   mp3取得
      ↓
   wavに変換
      ↓
   音声認識
      ↓
   "one two three four"
        ↓
      "1234"
      ↓
   回答を入力
      ↓
   Verify
      ↓
   token取得
```

マウス移動はBezier曲線を使って、ある程度人間っぽく動かしています。

音声ファイルはGoogle系の許可したホストからのみ取得していて、サイズ上限は10MBです。

## Tests

```bash
python -m unittest discover -s recaptcha_solver/tests -v
```

テスト自体はオフラインで動きます。

主に、

* 認識結果のnormalize
* URL validation

あたりをチェックしています。

実際にreCAPTCHAまで含めて試すなら：

```bash
python demo.py verify --headed
```

## Limitations

当然ですが100%通るわけではないです。

* try again later
* automated queries

このへんが出たらGoogle側のBot判定です。

IP、headless、実行頻度とかで普通に弾かれます。

--headed にしたり、連続実行を避けたりすると改善することはありますが、成功率は保証できません。

音声認識もたまにミスります。
その場合は別のaudio challengeを取って再試行します。デフォルトは最大5回。

あと、Google Speech APIを使う場合は音声データがGoogle側に送られます。

完全ローカルにしたいなら faster-whisper を使ってください。

## Note

自分のサイトか、テスト許可をもらっている環境で使ってください。

## License

MIT

[LICENSE](LICENSE)
