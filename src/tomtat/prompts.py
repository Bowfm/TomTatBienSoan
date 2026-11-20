"""Nạp mẫu prompt từ thư mục prompts\\ (Jinja2) và định nghĩa JSON Schema cho Gemini."""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, Undefined

from . import STYLE_SLUGS, STYLES

LABEL_SCHEMA = {
    "type": "object",
    "properties": {
        "label": {
            "type": "string",
            "enum": STYLES,
            "description": "Nhãn phong cách chức năng chung của toàn bài.",
        },
        "per_doc": {
            "type": "array",
            "description": "Phân loại phong cách cho từng văn bản gốc.",
            "items": {
                "type": "object",
                "properties": {
                    "doc": {"type": "integer", "description": "Số thứ tự văn bản (1..n)."},
                    "label": {"type": "string", "enum": STYLES},
                    "reason": {"type": "string", "description": "Dấu hiệu nhận biết ngắn gọn."},
                },
                "required": ["doc", "label", "reason"],
            },
        },
        "confidence": {
            "type": "string",
            "enum": ["cao", "trung_binh", "thap"],
            "description": "Mức độ chắc chắn của nhãn đã chọn.",
        },
        "reason": {
            "type": "string",
            "description": "Lý do ngắn gọn bằng tiếng Việt cho nhãn chung.",
        },
    },
    "required": ["label", "per_doc", "confidence", "reason"],
}

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "selections": {
            "type": "array",
            "description": "Danh sách đơn vị câu/đề mục được chọn theo từng văn bản.",
            "items": {
                "type": "object",
                "properties": {
                    "doc": {"type": "integer", "description": "Số thứ tự văn bản (1..n)."},
                    "ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Danh sách id đơn vị được chọn theo thứ tự tăng dần.",
                    },
                },
                "required": ["doc", "ids"],
            },
        },
        "note": {
            "type": "string",
            "description": "Ghi chú ngắn về cách chọn và ước tính tổng số từ.",
        },
    },
    "required": ["selections", "note"],
}

ABSTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "abstract": {
            "type": "string",
            "description": "Văn bản tóm tắt biên soạn hoàn chỉnh (ngắt đoạn bằng xuống dòng thực sự, tuyệt đối không viết ký tự \\n theo nghĩa đen hay Markdown).",
        },
        "note": {
            "type": "string",
            "description": "Ghi chú ngắn về cách biên soạn hoặc cách đã sửa lỗi.",
        },
    },
    "required": ["abstract", "note"],
}

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Phải phân tích và liệt kê TRƯỚC mọi vấn đề cụ thể phát hiện được "
                "(bịa ý/suy diễn, sai hoặc làm tròn số liệu, đổi dấu phẩy thập phân, "
                "đổi lập trường, lẫn phát ngôn với sự kiện, bỏ sót ý chính/query, "
                "lỗi mạch văn hoặc sai quy tắc phong cách). Mỗi mục ghi rõ `[Tiêu chí] chỗ sai -> cách sửa ngắn gọn`; "
                "trả về [] nếu hoàn hảo."
            ),
        },
        "summary": {
            "type": "string",
            "description": (
                "Nhận xét đối chiếu tổng quát ngắn gọn theo đủ 4 tiêu chí "
                "(Phù hợp / Liền mạch / Trung thực / Trôi chảy) trước khi chốt điểm."
            ),
        },
        "relevance": {"type": "number", "description": "Điểm mức độ phù hợp (1.0-5.0, bước 0.5), chốt sau khi đã liệt kê issues/summary."},
        "coherence": {"type": "number", "description": "Điểm mức độ liền mạch (1.0-5.0, bước 0.5), chốt sau khi đã liệt kê issues/summary."},
        "consistency": {"type": "number", "description": "Điểm tính nhất quán/trung thực (1.0-5.0, bước 0.5), chốt sau khi đã liệt kê issues/summary."},
        "fluency": {"type": "number", "description": "Điểm độ trôi chảy & chuẩn phong cách (1.0-5.0, bước 0.5), chốt sau khi đã liệt kê issues/summary."},
    },
    "required": ["issues", "summary", "relevance", "coherence", "consistency", "fluency"],
}


class PromptRenderer:
    def __init__(self, prompts_dir: Path):
        self.dir = Path(prompts_dir)
        self.env = Environment(
            loader=FileSystemLoader(str(self.dir), encoding="utf-8"),
            undefined=Undefined,
            trim_blocks=True,
            lstrip_blocks=True,
            autoescape=False,
        )

    def system(self) -> str:
        return (self.dir / "system.md").read_text(encoding="utf-8").strip()

    def style_guide(self, style: str) -> str:
        slug = STYLE_SLUGS.get(style)
        if not slug:
            return ""
        p = self.dir / "styles" / f"{slug}.md"
        return p.read_text(encoding="utf-8").strip() if p.exists() else ""

    def render(self, template_name: str, **ctx) -> str:
        tpl = self.env.get_template(template_name)
        return tpl.render(**ctx).strip()
