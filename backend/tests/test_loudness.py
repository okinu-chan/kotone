"""ラウドネス計測結果のパースと、音量補正の計算のテスト（要件 3.1）。"""

from __future__ import annotations

from pathlib import Path

from kotone.library.loudness import TARGET_LUFS, _parse_loudnorm_json, _parse_rsgain_output

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
