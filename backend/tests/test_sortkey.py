"""ソートキーとイニシャル判定のテスト（要件 3.2 / 3.7 の中核）。"""

from __future__ import annotations

import pytest

from kotone.text.sortkey import (
    KANA_INITIALS,
    KANA_ROWS,
    initial_of,
    katakana_to_hiragana,
    make_sort_fields,
    normalize_for_sort,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("アイウエオ", "あいうえお"),
        ("ガギグゲゴ", "かきくけこ"),          # 濁点を落とす
        ("パピプペポ", "はひふへほ"),          # 半濁点も落とす
        ("キョウ", "きよう"),                  # 小書き文字を大きくする
        ("コーヒー", "こおひい"),              # 長音記号を母音へ展開
        ("ジャンプ", "しやんふ"),
        ("ＡＢＣ", "abc"),                     # NFKC で全角→半角、小文字化
        ("The Beatles", "the beatles"),
        ("  空白   の  圧縮 ", "空白 の 圧縮"),
    ],
)
def test_normalize_for_sort(text: str, expected: str) -> None:
    assert normalize_for_sort(text) == expected


def test_katakana_to_hiragana_keeps_prolonged_mark() -> None:
    assert katakana_to_hiragana("ラーメン") == "らーめん"


def test_leading_article_option() -> None:
    assert normalize_for_sort("The Beatles", ignore_leading_article=True) == "beatles"
    assert normalize_for_sort("The Beatles", ignore_leading_article=False) == "the beatles"
    # 記事だけの名前は消さない
    assert normalize_for_sort("The", ignore_leading_article=True) == "the"


@pytest.mark.parametrize(
    ("sort_key", "expected"),
    [
        # カナは行の代表ではなく 1 文字そのものを返す（五十音を並べるため）
        ("あいうえお", ("あ", "kana")),
        ("きようと", ("き", "kana")),
        ("さくら", ("さ", "kana")),
        ("わたし", ("わ", "kana")),
        ("ん", ("ん", "kana")),
        ("beatles", ("B", "latin")),
        ("2001", ("#", "latin")),
        ("薔薇", ("#", "latin")),   # 読みが取れず漢字が残った場合
        ("", ("#", "latin")),
    ],
)
def test_initial_of(sort_key: str, expected: tuple[str, str]) -> None:
    assert initial_of(sort_key) == expected


def test_gojuon_ordering() -> None:
    """文字コード順ではなく「あいうえお順」に並ぶこと。"""
    names = [
        ("山田", "ヤマダ"),
        ("井上", "イノウエ"),
        ("鈴木", "スズキ"),
        ("小林", "コバヤシ"),
        ("阿部", "アベ"),
    ]
    rows = [(display, *make_sort_fields(display, reading)) for display, reading in names]
    # DB の ORDER BY sort_key, reading と同じキーで並べる
    ordered = [row[0] for row in sorted(rows, key=lambda r: (r[1], r[0]))]
    assert ordered == ["阿部", "井上", "小林", "鈴木", "山田"]


def test_dakuten_ordering_is_resolved_by_reading() -> None:
    """sort_key が同点になる濁音は reading で解決される（か → が → き）。"""
    entries = [("ガ", "ガ"), ("カ", "カ"), ("キ", "キ")]
    rows = []
    for display, reading in entries:
        sort_key, _, _ = make_sort_fields(display, reading)
        rows.append((display, sort_key, reading))
    ordered = [r[0] for r in sorted(rows, key=lambda r: (r[1], r[2]))]
    assert ordered == ["カ", "ガ", "キ"]


def test_make_sort_fields_falls_back_to_display() -> None:
    sort_key, initial, group = make_sort_fields("Nujabes", reading="")
    assert sort_key == "nujabes"
    assert (initial, group) == ("N", "latin")


def test_latin_is_used_as_is() -> None:
    """ラテン文字表記は変換不要のソートキーとしてそのまま扱う（要件 3.7）。"""
    sort_key, initial, group = make_sort_fields("Radiohead", "Radiohead")
    assert sort_key == "radiohead"
    assert group == "latin"
    assert initial == "R"


def test_kana_initials_cover_the_gojuon() -> None:
    """カナタブに並ぶ頭文字が五十音を網羅していること。"""
    assert KANA_INITIALS[:5] == list("あいうえお")
    assert KANA_INITIALS[-5:] == list("わゐゑをん")
    assert len(KANA_INITIALS) == len(set(KANA_INITIALS))
    # や行は「や・ゆ・よ」の 3 文字（画面側はここに空きを入れて表の形にする）
    assert "やゆよ" in KANA_ROWS


def test_every_kana_initial_is_reachable() -> None:
    """並べたどの頭文字も、実際にその文字で始まる読みから導けること。

    表に出ていても initial_of が返さない文字があると、押しても 0 件の
    死んだセルになる。
    """
    for ch in KANA_INITIALS:
        assert initial_of(ch) == (ch, "kana"), ch


def test_voiced_kana_share_the_unvoiced_initial() -> None:
    """濁点・半濁点・小書きは清音の頭文字にまとめる。"""
    assert initial_of(normalize_for_sort("ギター"))[0] == "き"
    assert initial_of(normalize_for_sort("パンダ"))[0] == "は"
    assert initial_of(normalize_for_sort("ヴィレッジ"))[0] == "う"
