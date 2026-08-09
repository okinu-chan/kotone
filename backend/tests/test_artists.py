"""アーティスト分割のテスト（要件 3.6）。"""

from __future__ import annotations

from kotone.library.artists import analyze_separators, split_artists

DEFAULT_SEPARATORS = [";", " / ", "／", "、"]


def test_multi_valued_tag_is_trusted_as_is() -> None:
    """多値タグは区切り文字を推測せずそのまま採用する。"""
    values = ["AC/DC", "Hall & Oates"]
    assert split_artists(values, DEFAULT_SEPARATORS) == ["AC/DC", "Hall & Oates"]


def test_single_value_is_split_by_separators() -> None:
    assert split_artists(["宇多田ヒカル; 椎名林檎"], DEFAULT_SEPARATORS) == [
        "宇多田ヒカル",
        "椎名林檎",
    ]
    assert split_artists(["A / B / C"], DEFAULT_SEPARATORS) == ["A", "B", "C"]


def test_default_separators_do_not_split_ampersand_or_comma() -> None:
    """名前自体に含まれやすい区切りは既定では使わない。"""
    assert split_artists(["Hall & Oates"], DEFAULT_SEPARATORS) == ["Hall & Oates"]
    assert split_artists(["Earth, Wind & Fire"], DEFAULT_SEPARATORS) == [
        "Earth, Wind & Fire"
    ]


def test_slash_without_spaces_is_not_split_by_default() -> None:
    """既定の ' / ' はスペース込みなので AC/DC は割れない。"""
    assert split_artists(["AC/DC"], DEFAULT_SEPARATORS) == ["AC/DC"]


def test_longest_separator_wins() -> None:
    assert split_artists(["A / B"], [" / ", "/"]) == ["A", "B"]


def test_duplicates_are_removed_case_insensitively() -> None:
    assert split_artists(["A; a; B"], DEFAULT_SEPARATORS) == ["A", "B"]


def test_empty_input() -> None:
    assert split_artists([], DEFAULT_SEPARATORS) == []
    assert split_artists(["   "], DEFAULT_SEPARATORS) == []


def test_analyze_separators_reports_frequency() -> None:
    values = [
        "宇多田ヒカル; 椎名林檎",
        "A; B",
        "C / D",
        "Hall & Oates",
    ]
    reports = analyze_separators(values)
    by_separator = {r.separator: r.count for r in reports}
    assert by_separator[";"] == 2
    assert by_separator[" / "] == 1
    assert by_separator[" & "] == 1
    # 件数の多い順に並ぶ
    assert reports[0].separator == ";"
    assert reports[0].examples[0] == "宇多田ヒカル; 椎名林檎"
