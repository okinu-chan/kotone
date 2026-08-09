"""デプロイ時に決まる設定（パス・ポート・MPD 接続先）。

ユーザーが UI から変更する設定（クロスフェード秒数、ラウドネス補正の ON/OFF 等）は
ここではなく SQLite の ``settings`` テーブルで管理する（:mod:`kotone.settings`）。
こちらは環境変数 ``KOTONE_*`` または ``/etc/kotone/kotone.env`` から読み込む。
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KOTONE_",
        env_file=("/etc/kotone/kotone.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- ネットワーク ---------------------------------------------------
    host: str = "0.0.0.0"
    port: int = 8088
    # 家庭内 LAN 限定運用（要件 4.3）。外部公開は想定しないが、
    # ブラウザからの fetch で Origin が食い違う開発時のために CORS を許可できる。
    cors_origins: list[str] = Field(default_factory=list)

    # --- MPD ------------------------------------------------------------
    mpd_host: str = "localhost"
    mpd_port: int = 6600
    mpd_password: str | None = None
    # MPD の music_directory。DB に保存するパスはここからの相対パスに揃える。
    music_dir: Path = Path("/var/lib/kotone/music")

    # --- ユーザーデータ（git 管理外・アップデートで上書きされない領域）----
    data_dir: Path = Path("/var/lib/kotone")

    # --- フロントエンド（ビルド済み静的ファイル）------------------------
    static_dir: Path | None = None

    # --- 動作 -----------------------------------------------------------
    log_level: str = "INFO"
    # 起動時に未適用のマイグレーションを自動適用するか
    auto_migrate: bool = True

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

    # --- 派生パス -------------------------------------------------------
    @property
    def db_path(self) -> Path:
        return self.data_dir / "library.sqlite3"

    @property
    def artwork_cache_dir(self) -> Path:
        return self.data_dir / "artwork"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def resolved_static_dir(self) -> Path:
        """フロントエンドのビルド成果物の場所。

        明示指定が無ければリポジトリ内の ``frontend/dist`` を探す
        （開発時のリポジトリ直下でも、Pi 上の ``/opt/kotone`` でも同じ相対関係）。
        """
        if self.static_dir is not None:
            return self.static_dir
        return Path(__file__).resolve().parents[2] / "frontend" / "dist"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.artwork_cache_dir, self.backup_dir):
            path.mkdir(parents=True, exist_ok=True)


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config


def set_config(config: Config) -> None:
    """テスト用に設定を差し替える。"""
    global _config
    _config = config
