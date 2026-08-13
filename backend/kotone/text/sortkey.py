"""ソートキーとイニシャル（頭文字）の算出。

日本語の「あいうえお順」は文字コード順と一致しないため、比較専用に正規化した
文字列を別カラム（``sort_key``）で保持する（要件 3.2）。

正規化の手順:
    1. NFKC 正規化（全角英数 → 半角、半角カナ → 全角カナ）
    2. カタカナ → ひらがな
    3. 小書き文字 → 通常文字（ゃ→や、っ→つ …）
    4. 濁点・半濁点の除去（が→か、ぱ→は）
    5. 長音記号「ー」を直前の母音へ展開（こーひー → こおひい）
    6. ラテン文字を小文字化、連続空白を 1 個に圧縮

4 で濁音の区別が失われるが、``ORDER BY sort_key, reading`` の 2 段ソートに
することで「か → が → き」という期待どおりの順序になる（design-notes.md 参照）。
"""

from __future__ import annotations

import re
import unicodedata

# --- 変換テーブル -------------------------------------------------------

_KATAKANA_START, _KATAKANA_END = 0x30A1, 0x30F6
_KANA_OFFSET = 0x60

_SMALL_TO_LARGE = str.maketrans(
    {
        "ぁ": "あ", "ぃ": "い", "ぅ": "う", "ぇ": "え", "ぉ": "お",
        "っ": "つ", "ゃ": "や", "ゅ": "ゆ", "ょ": "よ", "ゎ": "わ",
        "ゕ": "か", "ゖ": "け",
    }
)

# 長音記号の展開に使う、ひらがな → 母音の対応
_VOWEL_ROWS = {
    "あ": "あいうえお", "か": "かきくけこ", "さ": "さしすせそ",
    "た": "たちつてと", "な": "なにぬねの", "は": "はひふへほ",
    "ま": "まみむめも", "や": "や　ゆ　よ", "ら": "らりるれろ",
    "わ": "わゐ　ゑを",
}
_CHAR_TO_VOWEL: dict[str, str] = {}
for _row in _VOWEL_ROWS.values():
    for _i, _ch in enumerate(_row):
        if _ch != "　":
            _CHAR_TO_VOWEL[_ch] = "あいうえお"[_i]

#: 五十音表の行（濁点除去・小書き展開後の文字で構成する）
#:
#: 「英数・他」タブが A〜Z を 1 文字ずつ並べるのに合わせ、カナも 1 文字ずつ
#: 並べる（要件 3.7）。行ごとに分けてあるのは、画面側で五十音表の形に
#: 並べ直せるようにするため。
KANA_ROWS: list[str] = [
    "あいうえお",
    "かきくけこ",
    "さしすせそ",
    "たちつてと",
    "なにぬねの",
    "はひふへほ",
    "まみむめも",
    "やゆよ",
    "らりるれろ",
    "わゐゑをん",
]

#: イニシャルサーチの「カナ」タブに並べる頭文字（五十音順）
KANA_INITIALS: list[str] = [ch for row in KANA_ROWS for ch in row]

_KANA_INITIAL_SET = frozenset(KANA_INITIALS)

#: イニシャルサーチの「英数・他」タブに並べる頭文字
LATIN_INITIALS: list[str] = [chr(c) for c in range(ord("A"), ord("Z") + 1)] + ["#"]

_WS_RE = re.compile(r"\s+")

#: 長音記号として扱う文字（NFKC 後に現れうるもの）
_PROLONGED_MARKS = frozenset("ー―—─")


# --- 変換関数 -----------------------------------------------------------


def katakana_to_hiragana(text: str) -> str:
    """カタカナをひらがなへ変換する（長音記号「ー」はそのまま残す）。"""
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if _KATAKANA_START <= code <= _KATAKANA_END:
            out.append(chr(code - _KANA_OFFSET))
        else:
            out.append(ch)
    return "".join(out)


def hiragana_to_katakana(text: str) -> str:
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if 0x3041 <= code <= 0x3096:
            out.append(chr(code + _KANA_OFFSET))
        else:
            out.append(ch)
    return "".join(out)


def _strip_diacritics(text: str) -> str:
    """濁点・半濁点を含むすべての結合発音区別記号を除去する。

    ラテン文字のアクセント（é → e）も同時に落ちるが、ソート順としては
    そちらが期待される挙動なので意図的にそのままにしている。
    """
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _expand_long_vowels(text: str) -> str:
    out: list[str] = []
    for ch in text:
        if ch in _PROLONGED_MARKS:
            # 直前の文字の母音へ展開。直前が無い/母音を持たない場合は捨てる
            prev = out[-1] if out else ""
            vowel = _CHAR_TO_VOWEL.get(prev)
            if vowel:
                out.append(vowel)
            continue
        out.append(ch)
    return "".join(out)


def normalize_for_sort(text: str, *, ignore_leading_article: bool = False) -> str:
    """比較用に正規化したソートキーを返す。"""
    if not text:
        return ""

    s = unicodedata.normalize("NFKC", text).strip()
    if ignore_leading_article:
        s = _strip_leading_article(s)
    s = katakana_to_hiragana(s)
    s = s.translate(_SMALL_TO_LARGE)
    s = _strip_diacritics(s)
    s = _expand_long_vowels(s)
    s = s.casefold()
    s = _WS_RE.sub(" ", s).strip()
    return s


_ARTICLE_RE = re.compile(r"^(the|a|an)\s+", re.IGNORECASE)


def _strip_leading_article(text: str) -> str:
    stripped = _ARTICLE_RE.sub("", text)
    return stripped if stripped else text


def initial_of(sort_key: str) -> tuple[str, str]:
    """ソートキーから ``(頭文字, グループ)`` を求める。

    グループは ``"kana"``（カナタブ）または ``"latin"``（英数・他タブ）。

    カナは行の代表（あ・か・さ…）ではなく 1 文字そのものを返す。
    ソートキーは濁点除去・小書き展開済みなので、「ギ」も「き」になる。
    ここが変わると保存済みの ``initial`` 列と食い違うため、値の詰め替えは
    マイグレーション 0003 で行っている。
    """
    for ch in sort_key:
        if ch.isspace():
            continue
        if ch in _KANA_INITIAL_SET:
            return ch, "kana"
        if "a" <= ch <= "z":
            return ch.upper(), "latin"
        if "A" <= ch <= "Z":
            return ch, "latin"
        # 漢字が残っている（読みが取得できなかった）場合も「その他」扱い
        return "#", "latin"
    return "#", "latin"


def make_sort_fields(
    display: str,
    reading: str | None = None,
    *,
    ignore_leading_article: bool = False,
) -> tuple[str, str, str]:
    """``(sort_key, initial, initial_group)`` をまとめて返す。

    ``reading`` があればそれを、無ければ表示名そのものをソートの元にする。
    ラテン文字表記のアーティスト・アルバム名は読みを持たないため、
    表示名がそのままソートキーになる（要件 3.7）。
    """
    base = reading if reading else display
    sort_key = normalize_for_sort(base, ignore_leading_article=ignore_leading_article)
    if not sort_key:
        sort_key = normalize_for_sort(display, ignore_leading_article=ignore_leading_article)
    initial, group = initial_of(sort_key)
    return sort_key, initial, group
