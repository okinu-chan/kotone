#!/usr/bin/env bash
#
# kotone のアンインストール。
#
#   sudo ./uninstall.sh [--purge]
#
#   --purge  ユーザーデータ（DB・アートワークキャッシュ・設定）も削除する
#            ※ 音楽ファイル自体は削除しません
#
set -euo pipefail

PREFIX="${KOTONE_PREFIX:-/opt/kotone}"
DATA_DIR="${KOTONE_DATA_DIR:-/var/lib/kotone}"
CONFIG_DIR="/etc/kotone"
SERVICE_USER="kotone"
PURGE=0

GREEN=$'\033[32m'; YELLOW=$'\033[33m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
info() { printf '%s==>%s %s\n' "${GREEN}${BOLD}" "${RESET}" "$*"; }
warn() { printf '%s警告:%s %s\n' "${YELLOW}${BOLD}" "${RESET}" "$*" >&2; }

[[ $EUID -eq 0 ]] || { echo "root で実行してください" >&2; exit 1; }

[[ "${1:-}" == "--purge" ]] && PURGE=1

info "サービスを停止します"
systemctl disable --now kotone >/dev/null 2>&1 || true
systemctl disable --now kotone-storage-recover.timer >/dev/null 2>&1 || true
rm -f /etc/systemd/system/kotone.service
rm -f /etc/systemd/system/kotone-storage-recover@.service /etc/systemd/system/kotone-storage-recover.timer
rm -f /etc/udev/rules.d/99-kotone-storage.rules
udevadm control --reload-rules >/dev/null 2>&1 || true
systemctl daemon-reload

info "コマンドを削除します"
rm -f /usr/local/bin/kotone-update /usr/local/bin/kotone-cli /usr/local/sbin/kotone-storage-recover

info "アプリを削除します: ${PREFIX}"
rm -rf "$PREFIX"

if [[ -f /etc/mpd.conf.kotone-backup ]]; then
  info "mpd.conf を元に戻します"
  mv /etc/mpd.conf.kotone-backup /etc/mpd.conf
  systemctl restart mpd >/dev/null 2>&1 || true
fi

if [[ $PURGE -eq 1 ]]; then
  warn "ユーザーデータを削除します: ${DATA_DIR}, ${CONFIG_DIR}"
  # 音楽ファイルだけは残す
  find "$DATA_DIR" -mindepth 1 -maxdepth 1 ! -name music -exec rm -rf {} + 2>/dev/null || true
  rm -rf "$CONFIG_DIR"
  userdel "$SERVICE_USER" 2>/dev/null || true
  info "削除しました（${DATA_DIR}/music は残しています）"
else
  info "ユーザーデータは残しました: ${DATA_DIR}"
  info "完全に削除するには --purge を付けて実行してください。"
fi

info "アンインストールが完了しました。MPD 自体は残っています（apt remove mpd で削除できます）。"
