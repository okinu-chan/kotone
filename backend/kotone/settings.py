"""ユーザー設定（SQLite の ``settings`` テーブルに JSON で保存）。

デプロイ設定（:mod:`kotone.config`）と違い、こちらは UI から変更できる。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from kotone.db import Database

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SettingSpec:
    key: str
    default: Any
    label: str
    kind: str  # "bool" | "int" | "float" | "enum" | "list[str]"
    description: str = ""
    choices: tuple[str, ...] | None = None
    minimum: float | None = None
    maximum: float | None = None


#: UI に出す設定の定義。ここに足すだけで API/画面に現れる。
SETTING_SPECS: tuple[SettingSpec, ...] = (
    # --- 再生 ---------------------------------------------------------
    SettingSpec(
        "crossfade_enabled", False, "クロスフェード", "bool",
        "曲間をフェードでつなぎます。既定はオフです（要件 3.1）。",
    ),
    SettingSpec(
        "crossfade_seconds", 3, "クロスフェード秒数", "int",
        "クロスフェードの長さ（秒）。", minimum=1, maximum=30,
    ),
    SettingSpec(
        "gapless_enabled", True, "ギャップレス再生", "bool",
        "トラック間に無音を挟まずに再生します。",
    ),
    SettingSpec(
        "random_avoid_last", 1, "ランダム時に直前の曲を避ける数", "int",
        "「ランダム（重複あり）」で、直前の N 曲を抽選対象から除外します。"
        "0 にすると完全に独立した抽選になります。",
        minimum=0, maximum=20,
    ),
    SettingSpec(
        "startup_warmup_ms", 600, "再生開始時のウォームアップ (ms)", "int",
        "USB DAC がサンプリングレートを補足するまでの間に曲の先頭が欠けるのを防ぎます。"
        "無音で先行再生してから曲頭に戻すため、この時間だけ再生開始が遅くなります。"
        "0 で無効。曲頭が切れる場合は増やしてください（要件 3.1）。",
        minimum=0, maximum=3000,
    ),
    # --- 音量 -----------------------------------------------------------
    SettingSpec(
        "volume_control", "kotone", "音量の操作方法", "enum",
        "「kotone」は画面の音量スライダーで操作します。"
        "「外部（アンプ側で調整）」はスライダーを隠し、MPD の音量を最大に保ちます。"
        "ヘッドホンアンプやプリアンプのつまみで音量を調整する構成向けです。",
        choices=("kotone", "external"),
    ),
    # --- ラウドネス補正 ------------------------------------------------
    SettingSpec(
        "loudness_enabled", True, "ラウドネス補正", "bool",
        "曲が変わるたびに音量つまみを触らなくて済むように、再生音量を自動で合わせます。"
        "音そのものは加工せず、音量の上げ下げだけを行います。"
        "DAC 内蔵のボリュームで音量を取っている場合は、精度が落ちるためオフを推奨します。",
    ),
    SettingSpec(
        "loudness_mode", "track", "補正の基準", "enum",
        "「トラック単位」はどの曲も同じ音量感に揃えます。"
        "シャッフルやプレイリストのように曲を混ぜて聴くときはこちらです。"
        "「アルバム単位」はアルバム内の曲の大小関係を残したまま、"
        "アルバムどうしの音量を揃えます。静かな曲と激しい曲の対比が意図されている"
        "作品を通しで聴くときはこちらが合います。",
        choices=("track", "album"),
    ),
    SettingSpec(
        "loudness_preamp_db", 0.0, "プリアンプ (dB)", "float",
        "補正後の音量をさらに全体的に上げ下げする微調整です。"
        "「補正は効いているが全体に少し小さい」と感じるときに +2 などを指定します。"
        "通常は 0 のままで構いません。",
        minimum=-15.0, maximum=15.0,
    ),
    SettingSpec(
        "loudness_clip_prevention", True, "クリッピング防止", "bool",
        "音量を上げる方向の補正で音が割れないよう、"
        "曲ごとの最大音量を見て上げ幅を抑えます。"
        "静かな曲が思ったほど大きくならないことがありますが、"
        "音が歪むより安全なのでオンを推奨します。",
    ),
    # --- ライブラリ ----------------------------------------------------
    SettingSpec(
        "artist_separators", [";", " / ", "／", "、"], "アーティスト名の区切り文字", "list[str]",
        "1 つの ARTIST タグに複数名が並記されている場合の分割に使います。"
        "`kotone-cli analyze-artists` で実ライブラリの傾向を確認できます（要件 3.6）。",
    ),
    SettingSpec(
        "ignore_leading_article", False, "先頭の The / A を無視して並べる", "bool",
        "オンにすると \"The Beatles\" が B の位置に並びます。",
    ),
    SettingSpec(
        "rsgain_enabled", False, "スキャン時にラウドネスを計測する", "bool",
        "オンにすると通常のライブラリスキャンでラウドネスも計測します。"
        "計測は 1 曲あたり数秒かかり、18,000 曲規模では十数時間になるため既定はオフです。"
        "オフのままでも、設定画面の「ラウドネスを計測」ボタンや "
        "`kotone-cli measure-loudness` でいつでも実行できます（中断・再開可）。",
    ),
    SettingSpec(
        "use_replaygain_tags", True, "ファイルの ReplayGain タグを使う", "bool",
        "音源に ReplayGain / R128 タグが入っていれば、計測せずにその値を使います。"
        "大規模ライブラリでは計測時間を大幅に短縮できます。",
    ),
    SettingSpec(
        "loudness_concurrency", 0, "ラウドネス計測の並列数", "int",
        "同時に走らせる計測プロセス数。0 で自動（CPU コア数と 3 の小さい方）。"
        "Raspberry Pi 4 ではメモリ帯域が上限になるため、2 を超えても速くなりません。",
        minimum=0, maximum=16,
    ),
    SettingSpec(
        "loudness_true_peak", False, "トゥルーピークで計測する", "bool",
        "クリッピング防止の精度が上がりますが、4 倍オーバーサンプリングを伴うため"
        "計測が約 1.5 倍遅くなります。通常はサンプルピーク（オフ）で十分です。",
    ),
    SettingSpec(
        "scan_on_start", False, "起動時にライブラリを再スキャンする", "bool",
        "オフの場合、スキャンは手動またはウォッチャー経由でのみ行われます。",
    ),
    # --- 表示 ----------------------------------------------------------
    SettingSpec(
        "default_library_sort", "album", "ライブラリの既定ソート", "enum",
        "ライブラリ画面を開いたときの並び順。",
        choices=("album", "artist", "genre", "year", "added"),
    ),
    SettingSpec(
        "album_layout", "grid", "アルバム一覧の表示形式", "enum",
        "「サムネイル」は画面幅に応じた複数列、「1 列」はアートワークを小さくして"
        "アルバム名とアーティスト名を並べます（文字の一覧性が高い）。",
        choices=("grid", "list"),
    ),
    SettingSpec(
        "theme", "dark", "テーマ", "enum", "画面の配色。", choices=("dark", "light"),
    ),
    SettingSpec(
        "font_size", "normal", "文字の大きさ", "enum",
        "画面全体の文字サイズ。曲名・アーティスト名などの大小関係は保ったまま拡大縮小します。"
        "「小」は大画面で一覧性を上げたいとき、「大」は読みやすさを優先したいときに。",
        choices=("small", "normal", "large"),
    ),
)

_SPECS_BY_KEY = {spec.key: spec for spec in SETTING_SPECS}

DEFAULTS: dict[str, Any] = {spec.key: spec.default for spec in SETTING_SPECS}


class SettingsStore:
    """設定の読み書き。値はプロセス内にキャッシュする。"""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._cache: dict[str, Any] | None = None

    def _load(self) -> dict[str, Any]:
        if self._cache is None:
            values = dict(DEFAULTS)
            for row in self._db.query("SELECT key, value FROM settings"):
                if row["key"] not in _SPECS_BY_KEY:
                    continue  # 廃止された設定は無視する
                try:
                    values[row["key"]] = json.loads(row["value"])
                except json.JSONDecodeError:
                    log.warning("設定 %s の値を解釈できませんでした", row["key"])
            self._cache = values
        return self._cache

    def all(self) -> dict[str, Any]:
        return dict(self._load())

    def get(self, key: str, default: Any = None) -> Any:
        return self._load().get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value: Any) -> Any:
        spec = _SPECS_BY_KEY.get(key)
        if spec is None:
            raise KeyError(f"未知の設定キーです: {key}")
        value = _coerce(spec, value)
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO settings (key, value, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT (key) DO UPDATE
                    SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, json.dumps(value, ensure_ascii=False)),
            )
        self._load()[key] = value
        return value

    def update(self, values: dict[str, Any]) -> dict[str, Any]:
        for key, value in values.items():
            self.set(key, value)
        return self.all()

    def invalidate(self) -> None:
        self._cache = None


def _coerce(spec: SettingSpec, value: Any) -> Any:
    if spec.kind == "bool":
        if isinstance(value, str):
            return value.lower() in ("1", "true", "yes", "on")
        return bool(value)
    if spec.kind == "int":
        value = int(value)
    elif spec.kind == "float":
        value = float(value)
    elif spec.kind == "enum":
        value = str(value)
        if spec.choices and value not in spec.choices:
            raise ValueError(f"{spec.key} は {spec.choices} のいずれかである必要があります")
        return value
    elif spec.kind == "list[str]":
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError(f"{spec.key} は文字列のリストである必要があります")
        return [v for v in value if v]

    if spec.minimum is not None and value < spec.minimum:
        value = type(value)(spec.minimum)
    if spec.maximum is not None and value > spec.maximum:
        value = type(value)(spec.maximum)
    return value


def spec_dicts() -> list[dict[str, Any]]:
    """UI へ渡す設定定義の一覧。"""
    return [
        {
            "key": s.key,
            "label": s.label,
            "kind": s.kind,
            "description": s.description,
            "choices": list(s.choices) if s.choices else None,
            "minimum": s.minimum,
            "maximum": s.maximum,
            "default": s.default,
        }
        for s in SETTING_SPECS
    ]
