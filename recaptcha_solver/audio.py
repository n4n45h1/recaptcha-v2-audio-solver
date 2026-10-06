"""Audio helpers: secure download + mp3->wav + STT + normalization.

STT order: Google free API (no key, via ffmpeg-made FLAC so no `flac`
binary is needed) -> faster-whisper local fallback (optional dep).
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import imageio_ffmpeg
import speech_recognition as sr

ALLOWED_HOSTS = frozenset({"www.google.com", "www.recaptcha.net"})
ALLOWED_PATHS = (
    "/recaptcha/api2/payload",
    "/recaptcha/api2/payload/audio.mp3",
    "/recaptcha/enterprise/payload",
    "/recaptcha/enterprise/payload/audio.mp3",
)
MAX_AUDIO_BYTES = 10 * 1024 * 1024

WORD_TO_DIGIT = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "oh": "0", "o": "0", "nought": "0",
    "ゼロ": "0", "零": "0", "れい": "0",
    "いち": "1", "一": "1", "に": "2", "二": "2",
    "さん": "3", "三": "3", "よん": "4", "し": "4", "四": "4",
    "ご": "5", "五": "5", "ろく": "6", "六": "6",
    "なな": "7", "しち": "7", "七": "7",
    "はち": "8", "八": "8", "きゅう": "9", "く": "9", "九": "9",
}


class AudioError(RuntimeError):
    pass


def validate_audio_url(value: str) -> str:
    """Allowlist validation for page-controlled audio URLs."""
    if not isinstance(value, str) or not value:
        raise AudioError("empty audio url")
    if any(ord(c) <= 32 or ord(c) == 127 for c in value) or "\\" in value:
        raise AudioError("invalid characters in audio url")
    u = urlsplit(value)
    if u.scheme != "https":
        raise AudioError("audio url must be https")
    if (u.hostname or "").lower() not in ALLOWED_HOSTS:
        raise AudioError(f"untrusted audio host: {u.hostname}")
    if u.username or u.password or u.port not in (None, 443) or u.fragment:
        raise AudioError("untrusted audio url form")
    if u.path not in ALLOWED_PATHS:
        raise AudioError(f"untrusted audio path: {u.path}")
    return value


def mp3_to_wav(mp3: Path, wav: Path) -> None:
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [ffmpeg, "-y", "-i", str(mp3), "-ac", "1", "-ar", "16000",
           "-c:a", "pcm_s16le", str(wav)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0 or not wav.is_file() or wav.stat().st_size == 0:
        raise AudioError(f"ffmpeg failed: {proc.stderr[-500:]}")


def normalize_answer(text: str) -> str:
    t = text.lower().strip()
    t = re.sub(r"[^a-z0-9ぁ-んァ-ン一-鿿\s]", " ", t)
    parts = [p for p in t.split() if p]
    mapped = [WORD_TO_DIGIT.get(p, p) for p in parts]
    joined = "".join(mapped)
    if re.fullmatch(r"\d+", joined) and len(joined) >= 4:
        return joined
    digits = "".join(p for p in mapped if re.fullmatch(r"\d", p))
    if len(digits) >= 4:
        return digits
    return " ".join(parts)


def _flac_via_ffmpeg(wav_path: Path) -> bytes:
    """WAV -> FLAC with ffmpeg (no `flac` binary needed, rootless-friendly)."""
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    out = wav_path.with_suffix(".flac")
    cmd = [ffmpeg, "-y", "-i", str(wav_path), "-c:a", "flac",
           "-ar", "16000", "-ac", "1", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0 or not out.is_file():
        raise AudioError(f"flac convert failed: {proc.stderr[-300:]}")
    return out.read_bytes()


def _recognize_flac(flac_bytes: bytes, sample_rate: int,
                    language: str, timeout: int) -> str:
    from speech_recognition.recognizers.google import (
        ENDPOINT, OutputParser, create_request_builder,
    )
    builder = create_request_builder(endpoint=ENDPOINT, key=None,
                                     language=language, filter_level=0)
    url = builder.build_url()
    from urllib.request import Request, urlopen
    req = Request(url, data=flac_bytes,
                  headers={"Content-Type": f"audio/x-flac; rate={sample_rate}"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            response_text = resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        raise AudioError(f"speech service error: {e}") from e
    try:
        return OutputParser(show_all=False,
                            with_confidence=False).parse(response_text)
    except Exception as e:
        raise AudioError("speech recognition heard nothing") from e


def transcribe_wav(wav_path: Path, lang: str = "en-US", timeout: int = 15) -> str:
    import wave
    try:
        with wave.open(str(wav_path), "rb") as w:
            sample_rate = w.getframerate() or 16000
    except Exception:
        sample_rate = 16000
    try:
        flac_bytes = _flac_via_ffmpeg(wav_path)
    except AudioError:
        rec = sr.Recognizer()
        rec.energy_threshold = 50
        rec.operation_timeout = timeout
        with sr.AudioFile(str(wav_path)) as source:
            audio = rec.record(source)
        last: Exception | None = None
        for cand in dict.fromkeys([lang, "en-US", "ja-JP"]):
            try:
                return rec.recognize_google(audio, language=cand)
            except sr.UnknownValueError as e:
                last = e
                continue
            except sr.RequestError as e:
                raise AudioError(f"speech service error: {e}") from e
        raise AudioError("speech recognition heard nothing") from last
    last_err: Exception | None = None
    for cand in dict.fromkeys([lang, "en-US", "ja-JP"]):
        try:
            return _recognize_flac(flac_bytes, sample_rate, cand, timeout)
        except AudioError as e:
            last_err = e
            if "heard nothing" not in str(e):
                raise
            continue
    raise AudioError("speech recognition heard nothing") from last_err


def transcribe_mp3_bytes(data: bytes, lang: str = "en-US") -> tuple[str, str]:
    """MP3 bytes -> (raw transcript, normalized answer). Cleans up tempfiles."""
    if len(data) > MAX_AUDIO_BYTES:
        raise AudioError("audio too large")
    if len(data) < 200:
        raise AudioError("audio payload too small")
    work = Path(tempfile.mkdtemp(prefix="rc-audio-"))
    try:
        mp3 = work / "c.mp3"
        wav = work / "c.wav"
        mp3.write_bytes(data)
        mp3_to_wav(mp3, wav)
        try:
            raw = transcribe_wav(wav, lang)
        except AudioError:
            raw = whisper_transcribe(mp3)
        return raw, normalize_answer(raw)
    finally:
        shutil.rmtree(work, ignore_errors=True)


_WHISPER_MODEL = None


def whisper_transcribe(mp3_path: Path, model: str = "base.en") -> str:
    """Local STT via faster-whisper (av-independent ffmpeg decode)."""
    import os as _os
    model = _os.environ.get("WHISPER_MODEL", model)
    global _WHISPER_MODEL
    import subprocess as _sp
    import wave as _wave
    import numpy as _np
    import imageio_ffmpeg as _iff
    import faster_whisper.audio as _fwa
    import faster_whisper.transcribe as _fwt

    def _decode(input_file, sampling_rate=16000, split_stereo=False):
        ff = _iff.get_ffmpeg_exe()
        tmp = str(Path(input_file).parent / "fw_decode.wav")
        _sp.run([ff, "-y", "-i", str(input_file), "-ac", "1",
                 "-ar", str(sampling_rate), "-c:a", "pcm_s16le", tmp],
                capture_output=True, check=True)
        with _wave.open(tmp, "rb") as w:
            frames = w.readframes(w.getnframes())
        return _np.frombuffer(frames, dtype=_np.int16).astype(_np.float32) / 32768.0

    _fwa.decode_audio = _decode
    _fwt.decode_audio = _decode
    from faster_whisper import WhisperModel
    if _WHISPER_MODEL is None:
        _WHISPER_MODEL = WhisperModel(model, device="cpu", compute_type="int8")
    segs, _ = _WHISPER_MODEL.transcribe(str(mp3_path), beam_size=5, language="en")
    text = " ".join(s.text for s in segs).strip()
    if not text:
        raise AudioError("whisper heard nothing")
    return text
