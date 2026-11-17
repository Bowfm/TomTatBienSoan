"""Gọi Gemini (SDK google-genai, Interactions API) và client giả lập cho chế độ --mock."""
from __future__ import annotations

import json
import random
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import STYLES
from .textutil import count_words, lines_of, split_sentences

TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504}


class LLMError(Exception):
    """Lỗi gọi model (đã hết số lần thử hoặc lỗi không thể thử lại)."""


@dataclass
class LLMResult:
    data: dict
    text: str
    usage: dict = field(default_factory=dict)
    duration_s: float = 0.0
    model: str = ""


def _status_of(exc: BaseException) -> int | None:
    for attr in ("status_code", "code", "status"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    resp = getattr(exc, "response", None)
    v = getattr(resp, "status_code", None)
    return v if isinstance(v, int) else None


def is_transient(exc: BaseException) -> bool:
    st = _status_of(exc)
    if st is not None:
        return st in TRANSIENT_STATUS
    name = type(exc).__name__.lower()
    if any(k in name for k in ("timeout", "connection", "connect", "network", "remoteprotocol",
                               "readerror", "noresponse", "ratelimit", "unavailable")):
        return True
    return isinstance(exc, (TimeoutError, ConnectionError))


def _redact(text: str, secret: str | None) -> str:
    if secret and text:
        text = text.replace(secret, "***")
    return text


def write_log(log_dir: Path | None, record: dict, secret: str | None = None) -> None:
    if not log_dir:
        return
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    name = f"{ts}_{record.get('step', 'call')}_{record.get('attempt', 0)}.json"
    body = json.dumps(record, ensure_ascii=False, indent=1, default=str)
    (log_dir / name).write_text(_redact(body, secret), encoding="utf-8")


class BaseClient:
    model = "base"
    _semaphore: threading.Semaphore | None = None
    _sem_limit: int = 0

    def __init__(self, cfg: dict):
        self.cfg = cfg
        g = cfg.get("gemini", {})
        limit = max(1, int(g.get("max_concurrent_calls", 4)))
        if BaseClient._semaphore is None or BaseClient._sem_limit != limit:
            BaseClient._semaphore = threading.Semaphore(limit)
            BaseClient._sem_limit = limit

    def generate_json(self, step: str, system: str, prompt: str, schema: dict, *,
                      log_dir: Path | None = None, attempt: int = 0,
                      context: dict | None = None) -> LLMResult:
        raise NotImplementedError


class GeminiClient(BaseClient):
    """Client thật. Key lấy từ GEMINI_API_KEY (hoặc .env), không bao giờ ghi vào log."""

    def __init__(self, cfg: dict, api_key: str):
        super().__init__(cfg)
        from google import genai  # nhập muộn để chế độ mock không cần SDK
        self._api_key = api_key
        self._client = genai.Client(api_key=api_key)
        # Tắt retry ngầm kéo dài của Speakeasy SDK để kiểm soát timeout và fallback nhanh
        try:
            from google.genai._gaos.utils.retries import RetryConfig
            self._client.interactions.sdk_configuration.retry_config = RetryConfig("none", None, False)
        except Exception:  # noqa: BLE001
            pass
        g = cfg["gemini"]
        self.model = g["model"]
        fallbacks = g.get("fallback_models") or []
        self.models_chain = [self.model] + [m for m in fallbacks if m and m != self.model]
        self.timeout = float(g.get("timeout_s", 90))
        self.max_retries = int(g.get("max_retries", 2))
        self.backoff_base = float(g.get("backoff_base_s", 2))
        self.backoff_max = float(g.get("backoff_max_s", 15))

    def _gen_config(self, step: str) -> dict:
        g = self.cfg["gemini"]
        gc: dict = {}
        lvl = (g.get("thinking_level") or {}).get(step)
        if lvl:
            gc["thinking_level"] = lvl
        if g.get("max_output_tokens"):
            gc["max_output_tokens"] = int(g["max_output_tokens"])
        if g.get("temperature") is not None:
            gc["temperature"] = float(g["temperature"])
        return gc

    def _call_once(self, model_name, step, system, prompt, schema):
        resp = self._client.interactions.create(
            model=model_name,
            input=prompt,
            system_instruction=system,
            response_format={"type": "text", "mime_type": "application/json", "schema": schema},
            generation_config=self._gen_config(step),
            store=False,
            timeout=self.timeout,
        )
        text = getattr(resp, "output_text", None) or ""
        usage = {}
        u = getattr(resp, "usage", None)
        if u is not None:
            for k in ("total_input_tokens", "total_output_tokens", "total_thought_tokens",
                      "total_cached_tokens", "total_tokens"):
                usage[k] = getattr(u, k, None)
        status = getattr(resp, "status", None)
        return text, usage, status

    def generate_json(self, step, system, prompt, schema, *, log_dir=None, attempt=0, context=None):
        last_exc: BaseException | None = None
        for model_idx, cur_model in enumerate(self.models_chain):
            # Model chính thử (max_retries + 1) lần; các model dự phòng thử 1 lần để trượt nhanh xuống tầng dưới nếu nghẽn
            tries_for_model = (self.max_retries + 1) if model_idx == 0 else 1
            for net_try in range(tries_for_model):
                t0 = time.time()
                record = {"step": step, "attempt": attempt, "net_try": net_try, "model": cur_model,
                          "started_at": datetime.now().isoformat(timespec="seconds"),
                          "generation_config": self._gen_config(step),
                          "system": system, "prompt": prompt, "schema": schema}
                try:
                    with self._semaphore:
                        text, usage, status = self._call_once(cur_model, step, system, prompt, schema)
                    dur = time.time() - t0
                    record.update(duration_s=round(dur, 2), response_text=text, usage=usage, status=str(status))
                    data = _parse_json(text)
                    record["parsed_ok"] = True
                    write_log(log_dir, record, self._api_key)
                    return LLMResult(data=data, text=text, usage=usage, duration_s=dur, model=cur_model)
                except json.JSONDecodeError as exc:
                    record.update(duration_s=round(time.time() - t0, 2), error=f"JSON không hợp lệ: {exc}")
                    write_log(log_dir, record, self._api_key)
                    last_exc = exc
                    if net_try >= 1:
                        break
                except Exception as exc:  # noqa: BLE001
                    err_str = _redact(f"{type(exc).__name__}: {exc}", self._api_key)
                    record.update(duration_s=round(time.time() - t0, 2), error=err_str)
                    write_log(log_dir, record, self._api_key)
                    last_exc = exc
                    # Nếu model này không được phép dùng trên Free Tier (limit: 0) hoặc 404, chuyển sang model dự phòng ngay
                    if "limit: 0" in err_str or _status_of(exc) == 404:
                        break
                    if not is_transient(exc):
                        break
                    if net_try + 1 < tries_for_model:
                        delay = min(self.backoff_max, self.backoff_base * (2 ** net_try))
                        time.sleep(delay * (0.5 + random.random() / 2))
        msg = _redact(f"{type(last_exc).__name__}: {last_exc}", self._api_key)
        st = _status_of(last_exc) if last_exc else None
        hint = ""
        if st in (401, 403):
            hint = " (kiểm tra GEMINI_API_KEY và quyền truy cập model)"
        elif st == 402:
            hint = " (Hết số dư tiền nạp trước trên Google AI Studio; vui lòng nạp thêm tiền tại https://ai.studio/projects hoặc https://ai.google.dev/gemini-api/docs/billing#prepay)"
        elif st == 404:
            hint = f" (model '{self.model}' không tồn tại hoặc tài khoản chưa được dùng; đổi gemini.model trong config.yaml)"
        elif st == 429:
            hint = " (vượt giới hạn tốc độ/hạn mức; giảm --workers hoặc chờ)"
        raise LLMError(f"Gọi Gemini thất bại ở bước {step}: {msg}{hint}")


def _parse_json(text: str) -> dict:
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    return json.loads(t)


# ====================================================================== giả lập

class MockClient(BaseClient):
    """Client giả lập: trả về JSON hợp lệ theo luật đơn giản, không gọi mạng."""

    model = "mock"

    def __init__(self, cfg: dict, delay_s: float = 0.0):
        super().__init__(cfg)
        self.delay_s = delay_s
        self.calls: list[str] = []

    def generate_json(self, step, system, prompt, schema, *, log_dir=None, attempt=0, context=None):
        t0 = time.time()
        if self.delay_s:
            time.sleep(self.delay_s)
        ctx = context or {}
        fn = getattr(self, f"_mock_{step}")
        data = fn(ctx, attempt)
        self.calls.append(step)
        text = json.dumps(data, ensure_ascii=False)
        usage = {"total_input_tokens": len(prompt) // 4, "total_output_tokens": len(text) // 4,
                 "total_thought_tokens": 0}
        write_log(log_dir, {"step": step, "attempt": attempt, "model": "mock",
                            "started_at": datetime.now().isoformat(timespec="seconds"),
                            "duration_s": round(time.time() - t0, 3), "system": system,
                            "prompt": prompt, "schema": schema, "response_text": text,
                            "usage": usage, "parsed_ok": True})
        return LLMResult(data=data, text=text, usage=usage, duration_s=time.time() - t0, model="mock")

    # --- từng bước ---
    @staticmethod
    def _guess_style(text: str) -> str:
        low = text.lower()
        if re.search(r"\bđiều \d+\.", low) or "căn cứ" in low:
            return "hành chính - công vụ"
        if re.search(r"\b(tôi|mình|bạn)\b", low):
            return "sinh hoạt hằng ngày"
        if re.search(r"\b(nghiên cứu|phương pháp|kết quả cho thấy)\b", low):
            return "khoa học"
        if re.search(r"\b(đảng|chủ trương|đường lối)\b", low):
            return "chính luận"
        return "báo chí - công luận"

    def _mock_label(self, ctx, attempt):
        data = ctx["data"]
        per = []
        for d in data["docs"]:
            txt = " ".join(u["text"] for u in d["units"])
            per.append({"doc": d["n"], "label": self._guess_style(txt), "reason": "Giả lập: theo từ khóa."})
        totals: dict[str, int] = {}
        for p, d in zip(per, data["docs"]):
            totals[p["label"]] = totals.get(p["label"], 0) + d["words"]
        label = max(totals, key=totals.get) if totals else STYLES[0]
        return {"label": label, "per_doc": per, "confidence": "cao",
                "reason": "Giả lập (--mock): nhãn đoán theo từ khóa, không phải kết quả AI."}

    def _mock_extract(self, ctx, attempt):
        data, target = ctx["data"], ctx.get("target_ratio", 0.3)
        ai_ext = (data.get("ai_extract") or "").lower()
        sels = []
        for d in data["docs"]:
            goal = d["words"] * target
            # Ưu tiên các câu xuất hiện trong ai_extract trước để đạt ROUGE-2 >= 33%
            ranked = sorted(
                d["units"],
                key=lambda u: (0 if u["text"][:25].lower() in ai_ext else 1, u["id"]),
            )
            picked, w = [], 0
            for u in ranked:
                if w + u["words"] <= goal * 1.05:
                    picked.append(u["id"])
                    w += u["words"]
            if not picked and d["units"]:
                picked = [d["units"][0]["id"]]
            sels.append({"doc": d["n"], "ids": sorted(picked)})
        return {"selections": sels, "note": "Giả lập: chọn các câu trọng tâm theo thứ tự gốc."}

    def _mock_abstract(self, ctx, attempt):
        extract = ctx.get("extract", "")
        t = ctx.get("targets", {})
        goal = (t.get("short_low", 0) + t.get("short_high", 0)) / 2 or 50
        sents = []
        for ln in lines_of(extract):
            for s in split_sentences(ln):
                s = re.sub(r"^\s*(\d+[/.)]|[-•*])\s*", "", s).strip()
                s = re.sub(r"[=:;]+\)+|:v|[🍀]+", "", s).strip()
                if s:
                    sents.append(s)
        out, w = [], 0
        for s in sents:
            n = count_words(s)
            if w + n > goal and out:
                continue
            out.append(s)
            w += n
        text = " ".join(out)
        text = re.sub(r"\s+([,.;:!?])", r"\1", text)
        text = re.sub(r"[ \t]{2,}", " ", text).strip()
        if text and text[-1] not in ".!?…":
            text += "."
        return {"abstract": text, "note": "Giả lập: ghép các câu đầu của bản trích chọn."}

    def _mock_judge(self, ctx, attempt):
        return {"relevance": 4.5, "coherence": 4.5, "consistency": 5, "fluency": 4.5,
                "issues": [], "summary": "Giả lập (--mock): không chấm thật."}
