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
    # --- ラウドネス補正 ------------------------------------------------
    SettingSpec(
        "loudness_enabled", True, "ラウドネス補正", "bool",
        "曲ごとの音量差を EBU R128 の計測値で補正します。"
        "ハードウェアミキサー使用時は精度が落ちるためオフを推奨します。",
    ),
    SettingSpec(
        "loudness_mode", "track", "補正の基準", "enum",
        "トラック単位／アルバム単位のどちらのゲインを使うか。",
        choices=("track", "album"),
    ),
    SettingSpec(
        "loudness_preamp_db", 0.0, "プリアンプ (dB)", "float",
        "補正後の音量にさらに加減算するオフセット。", minimum=-15.0, maximum=15.0,
    ),
    SettingSpec(
        "loudness_clip_prevention", True, "クリッピング防止", "bool",
        "計測したピーク値を考慮して、音量を上げる方向の補正を制限します。",
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
        "rsgain_enabled", True, "スキャン時にラウドネスを計測する", "bool",
        "rsgain が未インストールの場合は自動的にスキップされます。",
    ),
    SettingSpec(
        "scan_on_start", False, "起動時にライブラリを再スキャンする", "bool",
        "オフの場合、スキャンは手動またはウォッチャー経由でのみ行われます。",
    ),
    # --- 表示 ----------------------------------------------------------
    SettingSpec(
        "default_library_sort", "artist", "ライブラリの既定ソート", "enum",
        "ライブラリ画面を開いたときの並び順。",
        choices=("artist", "album", "genre", "year", "added"),
    ),
    SettingSpec(
        "theme", "dark", "テーマ", "enum", "画面の配色。", choices=("dark", "light"),
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
