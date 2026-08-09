"""読み仮名の自動生成（fugashi + unidic-lite / MeCab ベースの形態素解析）。

要件 3.2 の通り、精度を優先して pykakasi 等の軽量ライブラリではなく
形態素解析器を採用する。Tagger の初期化は数百 ms かかるため遅延生成し、
プロセス内で 1 個だけ使い回す。

fugashi / unidic-lite が入っていない環境（開発機など）でも import は成功し、
その場合は「カタカナ化できる文字だけ変換する」フォールバックで動作する。
読みが取れなかったことは :attr:`ReadingGenerator.available` で判別できる。
"""

from __future__ import annotations

import logging
import re
import threading
import unicodedata
from functools import lru_cache

from kotone.text.sortkey import hiragana_to_katakana

log = logging.getLogger(__name__)

#: 形態素の素性から読みを取り出す際に見る属性（優先順）
_READING_ATTRS = ("kana", "pron", "kanaBase", "pronBase", "lForm")

_KANJI_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_KANA_RE = re.compile(r"[ぁ-ゟァ-ヿ]")


def contains_kanji(text: str) -> bool:
    return bool(_KANJI_RE.search(text))


class ReadingGenerator:
    """文字列からカタカナ読みを生成する。"""

    def __init__(self) -> None:
        self._tagger = None
        self._lock = threading.Lock()
        self._init_failed = False

    # --- Tagger の遅延生成 ---------------------------------------------
    def _get_tagger(self):  # noqa: ANN202 - fugashi.Tagger（未インストール時は None）
        if self._tagger is not None or self._init_failed:
            return self._tagger
        with self._lock:
            if self._tagger is not None or self._init_failed:
                return self._tagger
            try:
                import fugashi  # noqa: PLC0415 - 遅延 import（起動を速く保つため）

                self._tagger = fugashi.Tagger()
                log.info("fugashi + unidic-lite を初期化しました")
            except Exception as exc:  # pragma: no cover - 環境依存
                self._init_failed = True
                log.warning(
                    "fugashi/unidic-lite を初期化できませんでした (%s)。"
                    "読み仮名の自動生成は簡易フォールバックで動作します。",
                    exc,
                )
        return self._tagger

    @property
    def available(self) -> bool:
        """形態素解析器が使えるか。"""
        return self._get_tagger() is not None

    # --- 生成 -----------------------------------------------------------
    def reading(self, text: str) -> str:
        """カタカナ読みを返す。読みを決められない場合は空文字を返す。"""
        if not text:
            return ""
        return _cached_reading(self, unicodedata.normalize("NFKC", text).strip())

    def _compute(self, text: str) -> str:
        if not text:
            return ""

        # 漢字を含まないなら解析不要。カナはカタカナへ揃え、
        # ラテン文字・数字はそのまま返す（要件 3.7「変換不要のソートキー」）。
        if not contains_kanji(text):
            return hiragana_to_katakana(text)

        tagger = self._get_tagger()
        if tagger is None:
            # フォールバック: 読める部分だけカタカナ化する。漢字は残るため
            # sortkey 側では「その他」扱いになる。
            return hiragana_to_katakana(text)

        parts: list[str] = []
        try:
            for word in tagger(text):
                parts.append(self._word_reading(word))
        except Exception as exc:  # pragma: no cover - 解析器の異常
            log.warning("形態素解析に失敗しました (%r): %s", text, exc)
            return hiragana_to_katakana(text)
        return "".join(parts)

    @staticmethod
    def _word_reading(word: object) -> str:
        surface = getattr(word, "surface", "")
        feature = getattr(word, "feature", None)
        if feature is not None:
            for attr in _READING_ATTRS:
                value = getattr(feature, attr, None)
                if value and value != "*":
                    return hiragana_to_katakana(str(value))
        # 未知語（英字・記号・数字など）は表層形をそのまま採用
        return hiragana_to_katakana(surface)


@lru_cache(maxsize=8192)
def _cached_reading(generator: ReadingGenerator, text: str) -> str:
    return generator._compute(text)


_default: ReadingGenerator | None = None
_default_lock = threading.Lock()


def get_reading_generator() -> ReadingGenerator:
    global _default
    if _default is None:
        with _default_lock:
            if _default is None:
                _default = ReadingGenerator()
    return _default
