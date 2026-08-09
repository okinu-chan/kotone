"""ARTIST タグから複数アーティストを取り出す（要件 3.6）。

方針:

1. **タグが多値なら、それをそのまま信頼する。** Vorbis Comment（FLAC/Ogg）や
   ID3 の TPE1 は同名フィールドを複数持てるため、区切り文字を推測せずに
   正しく分割できる。
2. 単値だった場合のみ、設定 ``artist_separators`` で分割する。
   既定値には「アーティスト名自体に含まれやすい」カンマや ``&`` を含めない。
3. それでも誤る場合はタグ編集 UI から曲ごとに修正でき、
   ``tracks.artists_locked`` が立って再スキャンでも保持される。

実ライブラリでどの区切り文字が使われているかは
``kotone-cli analyze-artists`` で調べられる。
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

#: 区切り文字候補（調査コマンドが探す対象）
CANDIDATE_SEPARATORS: tuple[str, ...] = (
    ";", " / ", "／", "、", ",", " & ", " feat. ", " ft. ", " featuring ",
    " with ", " × ", "×", " x ", "・", "／", "|", " vs. ", " vs ",
)


def _separator_pattern(separators: Iterable[str]) -> re.Pattern[str] | None:
    # 長い区切りを先に試す（" / " が "/" より優先されるように）
    parts = sorted({s for s in separators if s}, key=len, reverse=True)
    if not parts:
        return None
    return re.compile("|".join(re.escape(p) for p in parts))


def split_artists(values: list[str], separators: Iterable[str]) -> list[str]:
    """タグの値リストからアーティスト名のリストを作る。"""
    cleaned = [v.strip() for v in values if v and v.strip()]
    if not cleaned:
        return []

    # 多値タグはそのまま信頼する（区切り文字による誤分割を避ける）
    if len(cleaned) > 1:
        return _dedupe(cleaned)

    pattern = _separator_pattern(separators)
    if pattern is None:
        return _dedupe(cleaned)

    parts = [p.strip() for p in pattern.split(cleaned[0])]
    parts = [p for p in parts if p]
    return _dedupe(parts or cleaned)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            out.append(value)
    return out


@dataclass
class SeparatorReport:
    """区切り文字候補ごとの出現状況。"""

    separator: str
    count: int = 0
    examples: list[str] = field(default_factory=list)


def analyze_separators(
    artist_values: Iterable[str],
    candidates: Iterable[str] = CANDIDATE_SEPARATORS,
    *,
    max_examples: int = 5,
) -> list[SeparatorReport]:
    """ライブラリ中の ARTIST 文字列を走査し、区切り文字候補の頻度を集計する。

    ``kotone-cli analyze-artists`` の実体。初回スキャン前にこれを実行し、
    設定 ``artist_separators`` を実データに合わせて調整する運用を想定している。
    """
    candidates = list(dict.fromkeys(candidates))
    counters: Counter[str] = Counter()
    examples: dict[str, list[str]] = {c: [] for c in candidates}

    for value in artist_values:
        if not value:
            continue
        for candidate in candidates:
            if candidate in value:
                counters[candidate] += 1
                if len(examples[candidate]) < max_examples:
                    examples[candidate].append(value)

    reports = [
        SeparatorReport(separator=c, count=counters[c], examples=examples[c])
        for c in candidates
        if counters[c] > 0
    ]
    reports.sort(key=lambda r: r.count, reverse=True)
    return reports
