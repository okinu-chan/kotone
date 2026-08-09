"""タグ読み出しの正規化テスト（要件 4.1）。

実際の音源ファイルを用意せずに済むよう、mutagen の各コンテナの振る舞いを
模したダミーオブジェクトで検証する。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kotone.library import tags as tags_module
from kotone.library.tags import TrackTags, _read_vorbis_like, is_audio_file, read_tags


class FakeVorbisDict:
    """mutagen の ``VCommentDict`` を模したマッピング。

    重要: 実物は非 ASCII のキーを検査すると ``ValueError`` を投げる。
    MP4 のアトム名（``\\xa9nam`` 等）で種別を探ろうとして踏んだ不具合の回帰テスト。
    """

    def __init__(self, data: dict[str, list[str]]) -> None:
        self._data = data

    def items(self):
        return list(self._data.items())

    def keys(self):
        return list(self._data)

    def __contains__(self, key: str) -> bool:
        for char in key:
            if not 0x20 <= ord(char) <= 0x7D:
                raise ValueError("非 ASCII のキーは Vorbis Comment では無効です")
        return key in self._data


class FakeAudio:
    def __init__(self, tags, info=None) -> None:
        self.tags = tags
        self.info = info


class FakeInfo:
    length = 245.5
    sample_rate = 44100
    bits_per_sample = 16
    channels = 2
    bitrate = 900000


VORBIS_TAGS = {
    "title": ["Automatic"],
    "artist": ["宇多田ヒカル"],
    "albumartist": ["宇多田ヒカル"],
    "album": ["First Love"],
    "genre": ["J-Pop"],
    "date": ["1999-03-10"],
    "tracknumber": ["1/12"],
    "discnumber": ["1"],
}


def test_vorbis_tags_are_normalised() -> None:
    tags = TrackTags(path=Path("x.flac"))
    _read_vorbis_like(FakeAudio(FakeVorbisDict(VORBIS_TAGS)), tags)

    assert tags.title == "Automatic"
    assert tags.artists == ["宇多田ヒカル"]
    assert tags.album == "First Love"
    assert tags.genres == ["J-Pop"]
    assert tags.track_no == 1        # "1/12" から番号だけを取り出す
    assert tags.disc_no == 1


def test_multi_valued_vorbis_artist_is_preserved() -> None:
    """Vorbis Comment の多値 ARTIST は区切り文字を推測せずに扱える（要件 3.6）。"""
    tags = TrackTags(path=Path("x.flac"))
    _read_vorbis_like(
        FakeAudio(FakeVorbisDict({**VORBIS_TAGS, "artist": ["椎名林檎", "宇多田ヒカル"]})), tags
    )
    assert tags.artists == ["椎名林檎", "宇多田ヒカル"]


def test_read_tags_does_not_probe_mp4_atoms_on_vorbis(monkeypatch: pytest.MonkeyPatch) -> None:
    """非 ASCII キーの検査で例外を投げるコンテナでも、タグを取りこぼさない。"""
    audio = FakeAudio(FakeVorbisDict(VORBIS_TAGS), FakeInfo())
    monkeypatch.setattr(tags_module.mutagen, "File", lambda _path: audio)

    tags = read_tags(Path("dummy.flac"))
    assert tags is not None
    assert tags.artists == ["宇多田ヒカル"]
    assert tags.album == "First Love"
    assert tags.year == 1999
    assert tags.duration == pytest.approx(245.5)
    assert tags.samplerate == 44100


def test_read_tags_falls_back_to_filename(monkeypatch: pytest.MonkeyPatch) -> None:
    audio = FakeAudio(FakeVorbisDict({}), FakeInfo())
    monkeypatch.setattr(tags_module.mutagen, "File", lambda _path: audio)

    tags = read_tags(Path("music/07 名もなき曲.flac"))
    assert tags is not None
    assert tags.title == "07 名もなき曲"


def test_read_tags_returns_none_for_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tags_module.mutagen, "File", lambda _path: None)
    assert read_tags(Path("dummy.txt")) is None


def test_dsd_extension_is_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    audio = FakeAudio(FakeVorbisDict(VORBIS_TAGS), FakeInfo())
    monkeypatch.setattr(tags_module.mutagen, "File", lambda _path: audio)

    tags = read_tags(Path("album/01 track.dsf"))
    assert tags is not None
    assert tags.is_dsd is True


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("a.flac", True),
        ("a.FLAC", True),
        ("a.mp3", True),
        ("a.dsf", True),
        ("a.dff", True),
        ("cover.jpg", False),
        ("notes.txt", False),
    ],
)
def test_is_audio_file(name: str, expected: bool) -> None:
    assert is_audio_file(Path(name)) is expected
