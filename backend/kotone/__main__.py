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
    )


if __name__ == "__main__":
    main()
