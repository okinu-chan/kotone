"""アートワークの取得と、用途別 3 階層キャッシュの生成（要件 3.3）。

* 取得は多段フォールバック: 埋め込み画像 → 同じフォルダの画像 → FFmpeg 抽出
* リサイズは **短辺** 基準。長辺は元の比率のままなので非正方形をクロップしない
* 保存形式は PNG 固定（可逆。リサイズ以外の劣化を足さない）
* 元画像の解像度を超える拡大は行わない
* 生成はライブラリスキャン時のみ。配信時はファイルを返すだけ
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

log = logging.getLogger(__name__)

#: 用途別のキャッシュ階層（値は短辺のピクセル数）
TIERS: dict[str, int] = {
    "small": 300,    # 一覧のサムネイル
    "medium": 800,   # スマートフォンの再生画面
    "large": 1400,   # iPad / PC などの大画面・高 DPI
}

TIER_ORDER: tuple[str, ...] = ("small", "medium", "large")

#: フォルダ内から探すカバー画像のファイル名（優先順、大文字小文字は無視）
FOLDER_IMAGE_NAMES: tuple[str, ...] = (
    "cover.png", "cover.jpg", "cover.jpeg",
    "folder.png", "folder.jpg", "folder.jpeg",
    "front.png", "front.jpg", "front.jpeg",
    "album.png", "album.jpg", "album.jpeg",
    "albumart.jpg", "albumartsmall.jpg",
)

_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"})


@dataclass
class ArtworkSource:
    data: bytes
    kind: str           # "embedded" | "folder" | "ffmpeg"
    source_path: str | None
    width: int
    height: int

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.data).hexdigest()[:16]

    @property
    def short_side(self) -> int:
        return min(self.width, self.height)


@dataclass
class GeneratedTier:
    tier: str
    path: str      # キャッシュディレクトリからの相対パス
    width: int
    height: int
    bytes: int


# --------------------------------------------------------------- 取得


def extract_embedded(track_path: Path) -> bytes | None:
    """音源ファイルに埋め込まれた画像を取り出す。"""
    try:
        import mutagen  # noqa: PLC0415

        audio = mutagen.File(track_path)
    except Exception as exc:
        log.debug("埋め込み画像の読み出しに失敗: %s (%s)", track_path, exc)
        return None
    if audio is None:
        return None

    # FLAC / Ogg の PICTURE ブロック
    pictures = getattr(audio, "pictures", None)
    if pictures:
        return _best_picture([(p.data, getattr(p, "type", 0)) for p in pictures])

    tags = getattr(audio, "tags", None)
    if tags is None:
        return None

    # ID3 の APIC
    getall = getattr(tags, "getall", None)
    if callable(getall):
        frames = getall("APIC")
        if frames:
            return _best_picture([(f.data, getattr(f, "type", 0)) for f in frames])

    # MP4 の covr
    try:
        if "covr" in tags and tags["covr"]:
            return bytes(tags["covr"][0])
    except (TypeError, KeyError):
        pass

    # Vorbis Comment の metadata_block_picture（Ogg Vorbis/Opus）
    try:
        encoded = tags.get("metadata_block_picture")
    except AttributeError:
        encoded = None
    if encoded:
        from mutagen.flac import Picture  # noqa: PLC0415

        candidates: list[tuple[bytes, int]] = []
        for item in encoded if isinstance(encoded, list) else [encoded]:
            try:
                picture = Picture(base64.b64decode(item))
                candidates.append((picture.data, picture.type))
            except Exception:  # pragma: no cover - 壊れたタグ
                continue
        if candidates:
            return _best_picture(candidates)

    return None


def _best_picture(candidates: list[tuple[bytes, int]]) -> bytes | None:
    """front cover (type=3) を優先し、なければ最初の画像を返す。"""
    if not candidates:
        return None
    for data, kind in candidates:
        if kind == 3 and data:
            return bytes(data)
    for data, _ in candidates:
        if data:
            return bytes(data)
    return None


def find_folder_image(directory: Path) -> Path | None:
    """アルバムのフォルダからカバー画像ファイルを探す。"""
    if not directory.is_dir():
        return None
    try:
        entries = {p.name.lower(): p for p in directory.iterdir() if p.is_file()}
    except OSError:
        return None

    for name in FOLDER_IMAGE_NAMES:
        found = entries.get(name)
        if found:
            return found

    # 名前が合わなければ、フォルダ内で最大の画像を使う
    images = [p for p in entries.values() if p.suffix.lower() in _IMAGE_SUFFIXES]
    if images:
        return max(images, key=lambda p: p.stat().st_size)
    return None


def extract_with_ffmpeg(track_path: Path, timeout: float = 20.0) -> bytes | None:
    """mutagen で取れない形式のために FFmpeg で埋め込み画像を抽出する。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    try:
        result = subprocess.run(  # noqa: S603 - 固定コマンド + パス引数のみ
            [
                ffmpeg, "-v", "error", "-i", str(track_path),
                "-an", "-frames:v", "1", "-f", "image2", "-c:v", "png", "-",
            ],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.debug("FFmpeg でのアートワーク抽出に失敗: %s (%s)", track_path, exc)
        return None
    return result.stdout or None


def load_source(track_path: Path) -> ArtworkSource | None:
    """多段フォールバックでアートワークを 1 つ取得する。"""
    attempts: list[tuple[str, str | None, bytes | None]] = []

    attempts.append(("embedded", str(track_path), extract_embedded(track_path)))

    folder_image = find_folder_image(track_path.parent)
    if folder_image is not None:
        try:
            attempts.append(("folder", str(folder_image), folder_image.read_bytes()))
        except OSError:
            pass

    for kind, source_path, data in attempts:
        source = _decode(data, kind, source_path)
        if source is not None:
            return source

    data = extract_with_ffmpeg(track_path)
    return _decode(data, "ffmpeg", str(track_path))


def _decode(data: bytes | None, kind: str, source_path: str | None) -> ArtworkSource | None:
    if not data:
        return None
    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
    except Exception as exc:
        log.debug("アートワークを画像として解釈できませんでした (%s): %s", kind, exc)
        return None
    if width <= 0 or height <= 0:
        return None
    return ArtworkSource(data=data, kind=kind, source_path=source_path,
                         width=width, height=height)


# --------------------------------------------------------------- 生成


class ArtworkCache:
    """キャッシュ画像の生成とパス解決。"""

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = Path(cache_dir)

    def relative_dir(self, digest: str) -> Path:
        # 1 ディレクトリあたりのエントリ数を抑えるため 2 文字で分割
        return Path(digest[:2]) / digest

    def generate(self, source: ArtworkSource, *, force: bool = False) -> list[GeneratedTier]:
        """3 階層の PNG を生成する。既に存在する階層はスキップする。

        元画像の短辺より大きい階層は作らない。ただし「元画像の短辺」が
        階層の値を下回る場合は、その階層を **元解像度のまま** 1 度だけ生成する
        （利用可能な解像度を捨てないため）。同じサイズになる上位階層は作らない。
        """
        digest = source.digest
        target_dir = self.cache_dir / self.relative_dir(digest)
        target_dir.mkdir(parents=True, exist_ok=True)

        generated: list[GeneratedTier] = []
        produced_sizes: set[int] = set()

        try:
            with Image.open(io.BytesIO(source.data)) as raw:
                image = ImageOps.exif_transpose(raw)
                image = _to_savable_mode(image)

                for tier in TIER_ORDER:
                    target_short = min(TIERS[tier], source.short_side)
                    if target_short in produced_sizes:
                        continue  # 同じ解像度の重複生成を避ける
                    produced_sizes.add(target_short)

                    out_path = target_dir / f"{tier}.png"
                    rel_path = str(self.relative_dir(digest) / f"{tier}.png").replace("\\", "/")

                    if out_path.exists() and not force:
                        with Image.open(out_path) as existing:
                            width, height = existing.size
                        generated.append(
                            GeneratedTier(tier, rel_path, width, height,
                                          out_path.stat().st_size)
                        )
                        continue

                    resized = _resize_short_side(image, target_short)
                    resized.save(out_path, format="PNG", optimize=True, compress_level=6)
                    generated.append(
                        GeneratedTier(tier, rel_path, resized.width, resized.height,
                                      out_path.stat().st_size)
                    )
        except Exception as exc:
            log.warning("アートワークキャッシュの生成に失敗しました (%s): %s",
                        source.source_path, exc)
            return generated

        return generated

    def absolute_path(self, relative: str) -> Path:
        return self.cache_dir / relative

    def remove(self, digest: str) -> None:
        target = self.cache_dir / self.relative_dir(digest)
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)


def _to_savable_mode(image: Image.Image) -> Image.Image:
    if image.mode in ("RGB", "RGBA", "L"):
        return image
    if image.mode == "P":
        return image.convert("RGBA" if "transparency" in image.info else "RGB")
    if image.mode in ("CMYK", "YCbCr", "LAB", "HSV"):
        return image.convert("RGB")
    if image.mode in ("1", "I", "F", "I;16"):
        return image.convert("L")
    if image.mode == "LA":
        return image.convert("RGBA")
    return image.convert("RGB")


def _resize_short_side(image: Image.Image, target_short: int) -> Image.Image:
    """短辺を ``target_short`` に合わせて縮小する（アスペクト比は維持）。"""
    width, height = image.size
    short = min(width, height)
    if short <= target_short:
        return image.copy()
    scale = target_short / short
    new_size = (max(1, round(width * scale)), max(1, round(height * scale)))
    # LANCZOS はバイキュービックと同等以上の縮小品質（要件 3.3）
    return image.resize(new_size, Image.Resampling.LANCZOS)
