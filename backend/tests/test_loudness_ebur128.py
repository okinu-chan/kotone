"""ebur128 出力のパースと、既存 ReplayGain タグの読み取りのテスト（要件 3.1）。

実機（Raspberry Pi 4B）で loudnorm が 9.1x、ebur128 が 45.8x だったため
計測方式を ebur128 へ切り替えた。その出力形式の回帰テスト。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kotone.library import tags as tags_module
from kotone.library.loudness import (
    TARGET_LUFS,
    LoudnessResult,
    compute_album_gain,
    default_concurrency,
    parse_ebur128_output,
)
from kotone.library.tags import TrackTags, _read_vorbis_like, read_tags
from tests.test_tags import FakeAudio, FakeInfo, FakeVorbisDict

EBUR128_OUTPUT = """\
[Parsed_ebur128_0 @ 0x7f8c002090] Summary:

  Integrated loudness:
    I:         -15.0 LUFS
    Threshold: -25.3 LUFS

  Loudness range:
    LRA:         2.2 LU
    Threshold: -35.3 LUFS
    LRA low:   -16.1 LUFS
    LRA high:  -13.8 LUFS

  True peak:
    Peak:       -0.1 dBFS
"""


def test_parse_ebur128_summary() -> None:
    result = parse_ebur128_output(EBUR128_OUTPUT)
    assert result is not None
    assert result.loudness_lufs == -15.0
    # -18 LUFS 基準なので -18 - (-15.0) = -3.0 dB の減衰
    assert result.gain_db == pytest.approx(-3.0)
    # -0.1 dBFS -> 線形ピーク
    assert result.peak == pytest.approx(10 ** (-0.1 / 20), rel=1e-6)


def test_parse_ebur128_rejects_silence() -> None:
    silent = EBUR128_OUTPUT.replace("-15.0 LUFS", "-70.0 LUFS")
    assert parse_ebur128_output(silent) is None


def test_parse_ebur128_rejects_garbage() -> None:
    assert parse_ebur128_output("") is None
    assert parse_ebur128_output("何も出力されなかった") is None


def test_parse_ebur128_without_peak_section() -> None:
    """peak=true を付けない出力でも落ちない。"""
    text = "  Integrated loudness:\n    I:         -12.5 LUFS\n"
    result = parse_ebur128_output(text)
    assert result is not None
    assert result.peak == 1.0


def test_compute_album_gain_uses_energy_mean() -> None:
    tracks = {
        "a": LoudnessResult(gain_db=0, peak=0.9, loudness_lufs=-18.0),
        "b": LoudnessResult(gain_db=0, peak=0.5, loudness_lufs=-12.0),
    }
    album = compute_album_gain(tracks)
    assert album is not None
    # エネルギー平均は大きい方（-12）に引っ張られる
    assert -15.5 < album.loudness_lufs < -14.0
    assert album.gain_db == pytest.approx(TARGET_LUFS - album.loudness_lufs)
    assert album.peak == 0.9  # ピークは最大値


def test_compute_album_gain_empty() -> None:
    assert compute_album_gain({}) is None


def test_default_concurrency_is_positive() -> None:
    assert default_concurrency() >= 1


# ------------------------------------------------------ ReplayGain タグの読み取り


def test_replaygain_tags_are_read() -> None:
    tags = TrackTags(path=Path("x.flac"))
    _read_vorbis_like(
        FakeAudio(
            FakeVorbisDict(
                {
                    "title": ["t"],
                    "replaygain_track_gain": ["-6.77 dB"],
                    "replaygain_track_peak": ["0.988"],
                    "replaygain_album_gain": ["-5.95 dB"],
                    "replaygain_album_peak": ["0.995"],
                }
            )
        ),
        tags,
    )
    assert tags.has_gain
    assert tags.track_gain_db == pytest.approx(-6.77)
    assert tags.track_peak == pytest.approx(0.988)
    assert tags.album_gain_db == pytest.approx(-5.95)
    assert tags.album_peak == pytest.approx(0.995)


def test_r128_tag_is_converted_to_replaygain2_reference() -> None:
    """R128 は -23 LUFS 基準、ReplayGain 2.0 は -18 LUFS 基準。"""
    tags = TrackTags(path=Path("x.opus"))
    _read_vorbis_like(
        FakeAudio(FakeVorbisDict({"title": ["t"], "r128_track_gain": ["-1536"]})), tags
    )
    # -1536/256 = -6.0 dB（-23 基準）→ -18 基準では +5.0 して -1.0 dB
    assert tags.track_gain_db == pytest.approx(-1.0)


def test_no_gain_tags_means_measurement_needed(monkeypatch: pytest.MonkeyPatch) -> None:
    audio = FakeAudio(FakeVorbisDict({"title": ["t"], "artist": ["a"]}), FakeInfo())
    monkeypatch.setattr(tags_module.mutagen, "File", lambda _p: audio)
    tags = read_tags(Path("x.flac"))
    assert tags is not None
    assert not tags.has_gain
