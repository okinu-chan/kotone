"""アートワークキャッシュのテスト（要件 3.3）。"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from kotone.library.artwork import (
    TIERS,
    ArtworkCache,
    _decode,
    find_folder_image,
    folder_signature,
)


def _png_bytes(width: int, height: int) -> bytes:
    image = Image.new("RGB", (width, height), (12, 34, 56))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_generates_three_tiers_for_large_source(tmp_path: Path) -> None:
    source = _decode(_png_bytes(2000, 2000), "embedded", "x.flac")
    assert source is not None

    cache = ArtworkCache(tmp_path)
    tiers = cache.generate(source)

    assert {t.tier for t in tiers} == {"small", "medium", "large"}
    for tier in tiers:
        assert min(tier.width, tier.height) == TIERS[tier.tier]
        assert (tmp_path / tier.path).is_file()


def test_non_square_art_is_not_cropped(tmp_path: Path) -> None:
    """短辺を基準に縮小し、縦横比を維持する。"""
    source = _decode(_png_bytes(3000, 1500), "embedded", "x.flac")
    assert source is not None

    tiers = {t.tier: t for t in ArtworkCache(tmp_path).generate(source)}
    small = tiers["small"]
    assert small.height == 300          # 短辺が 300
    assert small.width == 600           # 2:1 の比率が保たれている


def test_does_not_upscale_beyond_source(tmp_path: Path) -> None:
    """元画像より大きい階層は作らない（要件 3.3）。"""
    source = _decode(_png_bytes(500, 500), "embedded", "x.flac")
    assert source is not None

    tiers = {t.tier: t for t in ArtworkCache(tmp_path).generate(source)}
    assert tiers["small"].width == 300
    # 800 / 1400 は元解像度を超えるので、元解像度のまま 1 階層だけ作る
    assert "medium" in tiers
    assert tiers["medium"].width == 500
    assert "large" not in tiers


def test_tiny_source_produces_single_tier(tmp_path: Path) -> None:
    source = _decode(_png_bytes(120, 120), "embedded", "x.flac")
    assert source is not None
    tiers = ArtworkCache(tmp_path).generate(source)
    assert len(tiers) == 1
    assert tiers[0].width == 120


def test_regeneration_reuses_existing_files(tmp_path: Path) -> None:
    source = _decode(_png_bytes(1000, 1000), "embedded", "x.flac")
    assert source is not None
    cache = ArtworkCache(tmp_path)
    first = cache.generate(source)
    mtimes = {t.tier: (tmp_path / t.path).stat().st_mtime_ns for t in first}

    second = cache.generate(source)
    for tier in second:
        assert (tmp_path / tier.path).stat().st_mtime_ns == mtimes[tier.tier]


def test_output_is_png_regardless_of_input_format(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (900, 900), (200, 100, 50)).save(buffer, format="JPEG", quality=90)
    source = _decode(buffer.getvalue(), "folder", "cover.jpg")
    assert source is not None

    tiers = ArtworkCache(tmp_path).generate(source)
    for tier in tiers:
        assert tier.path.endswith(".png")
        with Image.open(tmp_path / tier.path) as image:
            assert image.format == "PNG"


def test_find_folder_image_prefers_cover(tmp_path: Path) -> None:
    (tmp_path / "random.jpg").write_bytes(_png_bytes(10, 10))
    (tmp_path / "cover.jpg").write_bytes(_png_bytes(10, 10))
    found = find_folder_image(tmp_path)
    assert found is not None
    assert found.name == "cover.jpg"


def test_find_folder_image_returns_none_for_empty_dir(tmp_path: Path) -> None:
    assert find_folder_image(tmp_path) is None


def test_folder_signature_changes_when_the_image_is_replaced(tmp_path: Path) -> None:
    image = tmp_path / "Folder.png"
    image.write_bytes(_png_bytes(10, 10))
    before = folder_signature(tmp_path)
    assert before is not None and before.startswith("Folder.png|")

    image.write_bytes(_png_bytes(20, 20))
    assert folder_signature(tmp_path) != before


def test_folder_signature_follows_a_renamed_image(tmp_path: Path) -> None:
    """Folder.png を Folder.jpg に替えた場合も別の署名になる。"""
    (tmp_path / "Folder.png").write_bytes(_png_bytes(10, 10))
    before = folder_signature(tmp_path)
    (tmp_path / "Folder.png").unlink()
    (tmp_path / "Folder.jpg").write_bytes(_png_bytes(10, 10))
    after = folder_signature(tmp_path)
    assert after is not None and after.startswith("Folder.jpg|")
    assert after != before


def test_folder_signature_without_image_or_folder(tmp_path: Path) -> None:
    assert folder_signature(tmp_path) == ""
    # フォルダが無い（読めない）ときは「分からない」
    assert folder_signature(tmp_path / "missing") is None


def test_prune_removes_only_unused_cache(tmp_path: Path) -> None:
    cache = ArtworkCache(tmp_path)
    kept = _decode(_png_bytes(400, 400), "folder", "a.png")
    dropped = _decode(_png_bytes(300, 300), "folder", "b.png")
    assert kept is not None and dropped is not None
    cache.generate(kept)
    cache.generate(dropped)
    unrelated = tmp_path / "notes"
    unrelated.mkdir()

    assert cache.prune({kept.digest}) == 1
    assert (tmp_path / cache.relative_dir(kept.digest)).is_dir()
    assert not (tmp_path / cache.relative_dir(dropped.digest)).exists()
    assert unrelated.is_dir()


def test_decode_rejects_non_image() -> None:
    assert _decode(b"not an image", "embedded", "x.flac") is None
    assert _decode(None, "embedded", "x.flac") is None
