"""音源ファイルからのタグ・音声情報の読み出し（mutagen）。

要件 4.1 の通り、MPD が扱える主要フォーマットを広くサポートする。
フォーマットごとにタグの持ち方が違うため、ここで共通の形へ正規化する。

重要: Vorbis Comment（FLAC / Ogg / Opus）は同名フィールドを複数持てるので、
アーティストが複数いる場合は区切り文字に頼らず正しく分割できる。
:mod:`kotone.library.artists` はこの多値をそのまま受け取る。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import mutagen
from mutagen.id3 import ID3
from mutagen.mp4 import MP4

log = logging.getLogger(__name__)

#: スキャン対象の拡張子（MPD が標準で扱えるものに合わせる）
AUDIO_EXTENSIONS = frozenset(
    {
        ".flac", ".mp3", ".m4a", ".mp4", ".aac", ".alac",
        ".ogg", ".oga", ".opus", ".wav", ".aiff", ".aif", ".aifc",
        ".wv", ".ape", ".wma", ".mpc", ".dsf", ".dff", ".m4b",
    }
)

DSD_EXTENSIONS = frozenset({".dsf", ".dff"})

_TRACK_RE = re.compile(r"^\s*(\d+)")


@dataclass
class TrackTags:
    """1 ファイル分の正規化済みタグ。"""

    path: Path
    title: str = ""
    artists: list[str] = field(default_factory=list)
    album_artists: list[str] = field(default_factory=list)
    album: str = ""
    genres: list[str] = field(default_factory=list)
    date: str = ""
    year: int | None = None
    track_no: int = 0
    disc_no: int = 1
    is_compilation: bool = False
    musicbrainz_album_id: str | None = None

    # 音声情報
    duration: float = 0.0
    codec: str = ""
    samplerate: int | None = None
    bit_depth: int | None = None
    channels: int | None = None
    bitrate: int | None = None
    is_dsd: bool = False

    #: タグ由来の読み仮名（TSOP/TSOA/ARTISTSORT 等）。あれば自動生成より優先する
    artist_sort: list[str] = field(default_factory=list)
    album_sort: str = ""
    title_sort: str = ""

    # --- 既存のラウドネス情報（あれば計測を省略できる。要件 3.1）---------
    # ファイルに ReplayGain / R128 タグが入っている場合はそれを読む。
    # 計測は 1 曲あたり数秒かかるため、大規模ライブラリでは効果が大きい。
    track_gain_db: float | None = None
    track_peak: float | None = None
    album_gain_db: float | None = None
    album_peak: float | None = None

    @property
    def has_gain(self) -> bool:
        return self.track_gain_db is not None

    @property
    def display_title(self) -> str:
        return self.title or self.path.stem


# --- タグキーの対応表 ---------------------------------------------------

# Vorbis Comment / APEv2（キーは小文字で比較する）
_VORBIS_MAP = {
    "title": "title",
    "artist": "artists",
    "albumartist": "album_artists",
    "album artist": "album_artists",
    "album_artist": "album_artists",
    "album": "album",
    "genre": "genres",
    "date": "date",
    "year": "date",
    "originaldate": "_originaldate",
    "tracknumber": "track_no",
    "track": "track_no",
    "discnumber": "disc_no",
    "disc": "disc_no",
    "compilation": "_compilation",
    "musicbrainz_albumid": "musicbrainz_album_id",
    "artistsort": "artist_sort",
    "albumsort": "album_sort",
    "titlesort": "title_sort",
    "albumartistsort": "_albumartistsort",
    # ReplayGain 2.0（-18 LUFS 基準）
    "replaygain_track_gain": "track_gain_db",
    "replaygain_track_peak": "track_peak",
    "replaygain_album_gain": "album_gain_db",
    "replaygain_album_peak": "album_peak",
    # EBU R128（-23 LUFS 基準・Q7.8 固定小数）。Opus/Vorbis で使われる
    "r128_track_gain": "_r128_track_gain",
    "r128_album_gain": "_r128_album_gain",
}

#: R128 タグ（-23 LUFS 基準）を ReplayGain 2.0（-18 LUFS 基準）へ揃える補正値
_R128_TO_RG2_DB = 5.0

# ID3v2 フレーム
_ID3_MAP = {
    "TIT2": "title",
    "TPE1": "artists",
    "TPE2": "album_artists",
    "TALB": "album",
    "TCON": "genres",
    "TDRC": "date",
    "TYER": "date",
    "TDOR": "_originaldate",
    "TRCK": "track_no",
    "TPOS": "disc_no",
    "TCMP": "_compilation",
    "TSOP": "artist_sort",
    "TSOA": "album_sort",
    "TSOT": "title_sort",
}

# MP4 / M4A アトム
_MP4_MAP = {
    "\xa9nam": "title",
    "\xa9ART": "artists",
    "aART": "album_artists",
    "\xa9alb": "album",
    "\xa9gen": "genres",
    "\xa9day": "date",
    "trkn": "track_no",
    "disk": "disc_no",
    "cpil": "_compilation",
    "soar": "artist_sort",
    "soal": "album_sort",
    "sonm": "title_sort",
}


def _as_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for v in value:
            out.extend(_as_text_list(v))
        return out
    if isinstance(value, bytes):
        return [value.decode("utf-8", "replace").strip()]
    text = str(value).strip()
    return [text] if text else []


def _first_int(values: list[str]) -> int | None:
    for value in values:
        m = _TRACK_RE.match(str(value))
        if m:
            return int(m.group(1))
    return None


def _parse_year(date: str) -> int | None:
    m = re.search(r"(\d{4})", date or "")
    return int(m.group(1)) if m else None


def _assign(tags: TrackTags, target: str, values: list[str]) -> None:
    if not values:
        return
    if target in ("artists", "album_artists", "genres", "artist_sort"):
        getattr(tags, target).extend(values)
    elif target in ("title", "album", "date", "album_sort", "title_sort"):
        if not getattr(tags, target):
            setattr(tags, target, values[0])
    elif target == "track_no":
        number = _first_int(values)
        if number is not None:
            tags.track_no = number
    elif target == "disc_no":
        number = _first_int(values)
        if number is not None:
            tags.disc_no = number
    elif target == "musicbrainz_album_id":
        tags.musicbrainz_album_id = values[0]
    elif target == "_compilation":
        tags.is_compilation = values[0] not in ("0", "", "false", "False")
    elif target == "_originaldate":
        if not tags.date:
            tags.date = values[0]
    elif target == "_albumartistsort":
        if not tags.artist_sort:
            tags.artist_sort = list(values)
    elif target in ("track_gain_db", "album_gain_db"):
        # "-6.77 dB" のような表記
        gain = _parse_float(values[0], strip=("db",))
        if gain is not None and getattr(tags, target) is None:
            setattr(tags, target, gain)
    elif target in ("track_peak", "album_peak"):
        peak = _parse_float(values[0])
        if peak is not None and peak > 0 and getattr(tags, target) is None:
            setattr(tags, target, peak)
    elif target in ("_r128_track_gain", "_r128_album_gain"):
        # Q7.8 固定小数（-23 LUFS 基準）→ dB（-18 LUFS 基準）へ換算
        raw = _parse_float(values[0])
        if raw is None:
            return
        gain = raw / 256.0 + _R128_TO_RG2_DB
        field_name = "track_gain_db" if target == "_r128_track_gain" else "album_gain_db"
        if getattr(tags, field_name) is None:
            setattr(tags, field_name, gain)


def _parse_float(value: str, strip: tuple[str, ...] = ()) -> float | None:
    text = str(value).strip().lower()
    for token in strip:
        text = text.replace(token, "")
    try:
        return float(text.strip())
    except ValueError:
        return None


def _read_vorbis_like(audio: Any, tags: TrackTags) -> None:
    for raw_key, raw_value in audio.tags.items():
        target = _VORBIS_MAP.get(str(raw_key).lower())
        if target:
            _assign(tags, target, _as_text_list(raw_value))


def _read_id3(id3: ID3, tags: TrackTags) -> None:
    for frame_id, target in _ID3_MAP.items():
        frame = id3.getall(frame_id)
        if not frame:
            continue
        values: list[str] = []
        for f in frame:
            values.extend(_as_text_list(getattr(f, "text", None)))
        _assign(tags, target, values)

    for frame in id3.getall("TXXX"):
        desc = str(getattr(frame, "desc", "")).lower()
        target = _VORBIS_MAP.get(desc)
        if target:
            _assign(tags, target, _as_text_list(getattr(frame, "text", None)))


def _read_mp4(audio: Any, tags: TrackTags) -> None:
    for key, target in _MP4_MAP.items():
        if key not in audio.tags:
            continue
        value = audio.tags[key]
        if target in ("track_no", "disc_no") and value and isinstance(value[0], tuple):
            number = value[0][0]
            if number:
                setattr(tags, target, int(number))
            continue
        _assign(tags, target, _as_text_list(value))


def _read_info(audio: Any, tags: TrackTags, path: Path) -> None:
    info = getattr(audio, "info", None)
    if info is None:
        return
    tags.duration = float(getattr(info, "length", 0.0) or 0.0)
    tags.samplerate = _int_or_none(getattr(info, "sample_rate", None))
    tags.bit_depth = _int_or_none(getattr(info, "bits_per_sample", None))
    tags.channels = _int_or_none(getattr(info, "channels", None))
    tags.bitrate = _int_or_none(getattr(info, "bitrate", None))
    tags.codec = type(info).__module__.rsplit(".", 1)[-1]

    suffix = path.suffix.lower()
    if suffix in DSD_EXTENSIONS:
        tags.is_dsd = True
        # DSD は 1bit なので bit_depth をそのまま出すと誤解を招かないよう明示
        tags.bit_depth = tags.bit_depth or 1


def _int_or_none(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result or None


def read_tags(path: Path) -> TrackTags | None:
    """1 ファイル分のタグを読む。読めない場合は ``None``。"""
    tags = TrackTags(path=path)
    try:
        audio = mutagen.File(path)
    except Exception as exc:
        log.warning("タグを読めませんでした: %s (%s)", path, exc)
        return None

    if audio is None:
        log.debug("mutagen が対応していないファイルです: %s", path)
        return None

    try:
        _read_info(audio, tags, path)
        container = getattr(audio, "tags", None)
        if container is not None:
            # 種別はコンテナのクラスで判定する。Vorbis Comment のキー検査は
            # 非 ASCII のキーで例外を投げるため、MP4 のアトム名で探ってはいけない。
            if isinstance(container, ID3):
                _read_id3(container, tags)
            elif isinstance(audio, MP4):
                _read_mp4(audio, tags)
            else:
                _read_vorbis_like(audio, tags)
    except Exception as exc:
        log.warning("タグの解釈に失敗しました: %s (%s: %s)", path, type(exc).__name__, exc)

    tags.title = tags.title or path.stem
    tags.year = _parse_year(tags.date)
    tags.artists = _dedupe(tags.artists)
    tags.album_artists = _dedupe(tags.album_artists)
    tags.genres = _dedupe(tags.genres)
    return tags


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        cleaned = value.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)
    return out


def is_audio_file(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTENSIONS
