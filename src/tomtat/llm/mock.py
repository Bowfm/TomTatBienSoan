"""MockClient — client giả lập, không gọi mạng, dùng trong chế độ --mock."""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path

from .. import STYLES
from ..textutil import count_words, lines_of, split_sentences
from .base import (
    BaseClient,
    LLMCancelledError,
    LLMResult,
    check_not_cancelled,
    get_active_cancel_event,
    write_log,
)


class MockClient(BaseClient):
    """Client giả lập: trả về JSON hợp lệ theo luật đơn giản, không gọi mạng."""

    model = "mock"

    def __init__(self, cfg: dict, delay_s: float = 0.0):
        super().__init__(cfg)
        self.delay_s = delay_s
        self.calls: list[str] = []

    def generate_json(self, step, system, prompt, schema, *, log_dir=None, attempt=0, context=None):
        cancel_ev = get_active_cancel_event()
        check_not_cancelled(cancel_ev)
        t0 = time.time()
        record = {
            "step": step,
            "attempt": attempt,
            "net_try": 1,
            "model": "mock",
            "key_label": "Mock",
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "started_epoch": round(t0, 3),
            "in_progress": True,
            "phase": "calling_api",
            "status": "running",
            "system": system,
            "prompt": prompt,
            "schema": schema,
        }
        log_path = write_log(log_dir, record) if self.delay_s else None
        if self.delay_s:
            if cancel_ev is not None:
                if cancel_ev.wait(timeout=self.delay_s):
                    record.update(
                        in_progress=False,
                        phase="done",
                        status="cancelled",
                        action="cancelled_in_flight",
                        duration_s=round(time.time() - t0, 3),
                        error="Đã dừng theo yêu cầu của người dùng.",
                    )
                    write_log(log_dir, record, log_path=log_path)
                    raise LLMCancelledError("Đã dừng chạy AI theo yêu cầu của người dùng.")
            else:
                time.sleep(self.delay_s)
        check_not_cancelled(cancel_ev)
        ctx = context or {}
        fn = getattr(self, f"_mock_{step}")
        data = fn(ctx, attempt)
        self.calls.append(step)
        text = json.dumps(data, ensure_ascii=False)
        usage = {"total_input_tokens": len(prompt) // 4, "total_output_tokens": len(text) // 4,
                 "total_thought_tokens": 0}
        record.update(
            in_progress=False,
            phase="done",
            status="completed",
            duration_s=round(time.time() - t0, 3),
            response_text=text,
            usage=usage,
            parsed_ok=True,
        )
        write_log(log_dir, record, log_path=log_path)
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
                s = re.sub(r"^\s*(\d+[/.)']|[-•*])\s*", "", s).strip()
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
