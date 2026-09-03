from __future__ import annotations

from pathlib import Path

from capture_app.i18n import STRINGS, Translator
from capture_app.tts import TTSCache, TTSConfig


def test_tts_cache_key_is_stable_and_configuration_sensitive(tmp_path: Path) -> None:
    credentials = tmp_path / "credentials.json"
    credentials.write_text("{}", encoding="utf-8")
    first = TTSCache(
        tmp_path / "cache",
        TTSConfig(credentials, "cmn-CN", "", 1.0),
    )
    second = TTSCache(
        tmp_path / "cache",
        TTSConfig(credentials, "cmn-CN", "", 1.2),
    )
    assert first.key_for("相同文本") == first.key_for("相同文本")
    assert first.key_for("相同文本") != second.key_for("相同文本")
    assert first.missing_texts(["相同文本", "相同文本"]) == ["相同文本"]


def test_every_ui_language_has_the_same_keys() -> None:
    baseline = set(STRINGS["zh_CN"])
    assert set(STRINGS["en"]) == baseline
    assert set(STRINGS["ja"]) == baseline
    assert Translator("ja").text("start_experiment") == "実験開始"

