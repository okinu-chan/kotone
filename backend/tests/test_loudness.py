"""ラウドネス計測結果のパースと、音量補正の計算のテスト（要件 3.1）。"""

from __future__ import annotations

import time
from pathlib import Path

from kotone.library.loudness import (
    TARGET_LUFS,
    AlbumLoudness,
    LoudnessResult,
    LoudnessScanner,
    _parse_loudnorm_json,
    _parse_rsgain_output,
)

RSGAIN_OUTPUT = (
    "Filename\tLoudness (LUFS)\tGain (dB)\tPeak\tPeak (dB)\tPeak Type\tClipping Adjustment?\n"
    "01.flac\t-11.23\t-6.77\t0.988\t-0.10\tTrue Peak\tN\n"
    "02.flac\t-13.10\t-4.90\t0.912\t-0.80\tTrue Peak\tN\n"
    "Album\t-12.05\t-5.95\t0.988\t-0.10\tTrue Peak\tN\n"
)


def test_parse_rsgain_output() -> None:
    files = [Path("/music/album/01.flac"), Path("/music/album/02.flac")]
    result = _parse_rsgain_output(RSGAIN_OUTPUT, files)

    assert set(result.tracks) == {str(files[0]), str(files[1])}
    first = result.tracks[str(files[0])]
    assert first.gain_db == -6.77
    assert first.peak == 0.988
    assert first.loudness_lufs == -11.23

    assert result.album is not None
    assert result.album.gain_db == -5.95


def test_parse_rsgain_handles_reordered_columns() -> None:
    """列の並びが変わってもヘッダーから解決できること。"""
    output = (
        "Gain (dB)\tFilename\tPeak\n"
        "-3.50\t01.flac\t0.75\n"
    )
    result = _parse_rsgain_output(output, [Path("/music/01.flac")])
    assert result.tracks[str(Path("/music/01.flac"))].gain_db == -3.50


def test_parse_rsgain_rejects_unknown_format() -> None:
    result = _parse_rsgain_output("何かよくわからない出力\n", [Path("/music/01.flac")])
    assert result.tracks == {}


def test_parse_rsgain_distinguishes_same_name_across_discs() -> None:
    """2 枚組でファイル名が重複しても取り違えないこと。

    rsgain はディレクトリを含まないファイル名しか返さないため、
    名前で引くと Disc1 と Disc2 の同名ファイルが衝突する。
    """
    files = [
        Path("/music/album/Disc1/01.flac"),
        Path("/music/album/Disc2/01.flac"),
    ]
    output = (
        "Filename\tLoudness (LUFS)\tGain (dB)\tPeak\n"
        "01.flac\t-11.00\t-7.00\t0.90\n"
        "01.flac\t-14.00\t-4.00\t0.80\n"
        "Album\t-12.30\t-5.70\t0.90\n"
    )
    result = _parse_rsgain_output(output, files)

    assert len(result.tracks) == 2
    assert result.tracks[str(files[0])].gain_db == -7.00
    assert result.tracks[str(files[1])].gain_db == -4.00


def test_parse_rsgain_skips_unreadable_track() -> None:
    """読めない曲の行が落ちても、後続のファイルがずれないこと。"""
    files = [Path("/m/01.flac"), Path("/m/02.flac"), Path("/m/03.flac")]
    output = (
        "Filename\tGain (dB)\tPeak\n"
        "01.flac\t-7.00\t0.90\n"
        "03.flac\t-4.00\t0.80\n"   # 02 は壊れていて出力されなかった
    )
    result = _parse_rsgain_output(output, files)

    assert set(result.tracks) == {str(files[0]), str(files[2])}
    assert result.tracks[str(files[2])].gain_db == -4.00


def test_parse_rsgain_ignores_unexpected_row() -> None:
    """入力に無いファイル名の行が来ても、対応づけが壊れないこと。"""
    files = [Path("/m/01.flac"), Path("/m/02.flac")]
    output = (
        "Filename\tGain (dB)\tPeak\n"
        "01.flac\t-7.00\t0.90\n"
        "zz.flac\t-9.99\t0.50\n"   # 想定外の行
        "02.flac\t-4.00\t0.80\n"
    )
    result = _parse_rsgain_output(output, files)

    assert set(result.tracks) == {str(files[0]), str(files[1])}
    assert result.tracks[str(files[1])].gain_db == -4.00


def test_scan_albums_keeps_input_order_when_parallel() -> None:
    """並列に走らせても、結果はアルバムの入力順で返ること。

    呼び出し側は「N 番目の結果は N 番目のアルバムのもの」として DB に
    書き込むため、順序が入れ替わると別アルバムのゲインが入ってしまう。
    """
    scanner = LoudnessScanner()
    scanner._rsgain = "/usr/bin/rsgain"   # rsgain がある環境として振る舞わせる
    scanner.concurrency = 3

    # わざと先頭を遅くして、完了順と入力順を食い違わせる
    def fake_scan(files: list[Path]) -> AlbumLoudness:
        time.sleep(0.05 if files[0].name == "a" else 0.0)
        return AlbumLoudness(tracks={str(files[0]): LoudnessResult(gain_db=0.0, peak=1.0)})

    scanner._scan_album_rsgain = fake_scan

    albums = [[Path(name)] for name in ("a", "b", "c", "d", "e", "f", "g")]
    results = list(scanner.scan_albums(albums))

    assert [next(iter(r.tracks)) for r in results] == ["a", "b", "c", "d", "e", "f", "g"]


def test_scan_albums_yields_before_consuming_everything() -> None:
    """全アルバムを一度に投入せず、先読みを並列数の 2 倍に抑えること。"""
    scanner = LoudnessScanner()
    scanner._rsgain = "/usr/bin/rsgain"
    scanner.concurrency = 2

    submitted: list[str] = []

    def fake_scan(files: list[Path]) -> AlbumLoudness:
        submitted.append(files[0].name)
        return AlbumLoudness(tracks={str(files[0]): LoudnessResult(gain_db=0.0, peak=1.0)})

    scanner._scan_album_rsgain = fake_scan

    albums = [[Path(str(i))] for i in range(20)]
    stream = scanner.scan_albums(albums)
    next(stream)                      # 1 件だけ受け取る
    assert len(submitted) <= 5        # 先読み 4 + 取り出し後の補充 1
    stream.close()


def test_parse_loudnorm_json() -> None:
    stderr = (
        "[Parsed_loudnorm_0 @ 0x55] \n"
        "{\n"
        '\t"input_i" : "-14.20",\n'
        '\t"input_tp" : "-1.30",\n'
        '\t"input_lra" : "7.30"\n'
        "}\n"
    )
    data = _parse_loudnorm_json(stderr)
    assert data is not None
    assert data["input_i"] == "-14.20"


def test_target_lufs_matches_replaygain2() -> None:
    assert TARGET_LUFS == -18.0
