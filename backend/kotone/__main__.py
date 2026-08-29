"""``kotone-backend`` エントリポイント（uvicorn の起動）。"""

from __future__ import annotations

import uvicorn

from kotone.config import get_config
from kotone.main import configure_logging


def main() -> None:
    config = get_config()
    configure_logging(config.log_level)
    uvicorn.run(
        "kotone.main:get_app",
        factory=True,
        host=config.host,
        port=config.port,
        log_level=config.log_level.lower(),
        # 家庭内 LAN 前提のシングルプロセス運用（要件 4.3）
        workers=1,
        access_log=False,
        ws_ping_interval=20,
        ws_ping_timeout=30,
        # 使い回しの接続を、既定の 5 秒では閉じない。
        # ブラウザは一度開いた接続を次の操作でも使う。サーバーが閉じるのと
        # 同時にブラウザが送ると、その要求はサーバーへ届かないまま落ちる
        # （Safari の "Load failed"）。閉じる間隔を空けるほど、この重なりに
        # 当たる機会が減る。家庭内 LAN の 1 台運用なので接続を抱えても軽い。
        timeout_keep_alive=75,
        # 停止時のグレースフルシャットダウンを有界化する。既定は無制限で、
        # keep-alive 接続などを待ち続けて systemd の TimeoutStopSec に達し
        # SIGKILL される（systemctl restart / kotone-update が毎回遅くなる）。
        timeout_graceful_shutdown=5,
    )


if __name__ == "__main__":
    main()
