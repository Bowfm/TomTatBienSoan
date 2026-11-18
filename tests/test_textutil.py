"""Kiểm thử xử lý chuỗi tiếng Việt, NFC, đếm từ, tách câu."""
from __future__ import annotations

from tomtat.textutil import (
    TextConfig,
    configure,
    count_words,
    get_active_config,
    nfc,
    split_sentences,
)


def test_split_sentences_vietnamese_edge_cases():
    text = (
        "1. Tại TP. Hồ Chí Minh, PGS.TS. Nguyễn Văn A. công bố mức tăng trưởng 18,5% "
        "với kinh phí 1.500.000 đồng. "
        "Điều 5. Phạm vi áp dụng cho toàn bộ cơ sở. "
        "Tuy nhiên... nhiều vấn đề còn bỏ ngỏ! "
        "Ông hỏi: “Khi nào hoàn thành?” rồi kết thúc."
    )
    sents = split_sentences(text)
    assert len(sents) == 4
    assert sents[0].startswith("1. Tại TP. Hồ Chí Minh, PGS.TS. Nguyễn Văn A.")
    assert "18,5%" in sents[0]
    assert sents[1] == "Điều 5. Phạm vi áp dụng cho toàn bộ cơ sở."
    assert sents[2] == "Tuy nhiên... nhiều vấn đề còn bỏ ngỏ!"
    assert sents[3] == "Ông hỏi: “Khi nào hoàn thành?” rồi kết thúc."


def test_count_words_ignores_emojis_and_punct():
    txt = "🍀🍀 Xin chào   bạn =))) — đây là 18,5% kết quả! #hashtag"
    # Xin(1) chào(2) bạn(3) đây(4) là(5) 18,5%(6) kết(7) quả!(8) #hashtag(9)
    assert count_words(txt) == 9
    assert count_words("   ---   =)))  🍀 ") == 0


def test_nfc_normalization():
    decomposed = "tiê\u0301ng Viê\u0323t"
    composed = nfc(decomposed)
    assert composed == "tiếng Việt"


def test_textutil_immutable_config():
    cfg_active = get_active_config()
    assert isinstance(cfg_active, TextConfig)
    assert isinstance(cfg_active.abbreviations, frozenset)
    assert isinstance(cfg_active.heading_words, frozenset)

    custom_cfg = configure(abbreviations=["tp", "ts"], heading_words=["điều"])
    assert isinstance(custom_cfg.abbreviations, frozenset)
    assert "tp" in custom_cfg.abbreviations
    assert get_active_config() is custom_cfg
