#!/usr/bin/env bash
#
# kotone セットアップスクリプト（要件 5 章）
#
# 前提: 公式 Raspberry Pi OS (Lite, Bookworm 以降) が書き込み・起動済みであること。
#
#   sudo ./install.sh [オプション]
#
# 主なオプション:
#   --music-dir PATH   音楽ライブラリの場所（既定: /var/lib/kotone/music）
#   --port PORT        Web UI のポート（既定: 8088）
#   --prefix PATH      アプリの配置先（既定: /opt/kotone）
#   --with-rsgain      apt に rsgain が無い場合、GitHub リリースから導入する
#   --no-rsgain        rsgain を導入しない（ラウドネス計測は FFmpeg で行う）
#   --no-mpd-config    mpd.conf を書き換えない
#   --unattended       確認プロンプトを出さない
#
set -euo pipefail

APP_NAME="kotone"
PREFIX="/opt/${APP_NAME}"
DATA_DIR="/var/lib/${APP_NAME}"
CONFIG_DIR="/etc/${APP_NAME}"
MUSIC_DIR=""
PORT="8088"
SERVICE_USER="${APP_NAME}"
REPO_URL="https://github.com/okinu-chan/kotone.git"
WITH_RSGAIN=0
NO_RSGAIN=0
CONFIGURE_MPD=1
UNATTENDED=0

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; BOLD=$'\033[1m'; RESET=$'\033[0m'

info()  { printf '%s==>%s %s\n' "${GREEN}${BOLD}" "${RESET}" "$*"; }
warn()  { printf '%s警告:%s %s\n' "${YELLOW}${BOLD}" "${RESET}" "$*" >&2; }
die()   { printf '%sエラー:%s %s\n' "${RED}${BOLD}" "${RESET}" "$*" >&2; exit 1; }

# ---------------------------------------------------------------- 引数解析

while [[ $# -gt 0 ]]; do
  case "$1" in
    --music-dir)     MUSIC_DIR="$2"; shift 2 ;;
    --port)          PORT="$2"; shift 2 ;;
    --prefix)        PREFIX="$2"; shift 2 ;;
    --repo)          REPO_URL="$2"; shift 2 ;;
    --with-rsgain)   WITH_RSGAIN=1; shift ;;
    --no-rsgain)     NO_RSGAIN=1; shift ;;
    --no-mpd-config) CONFIGURE_MPD=0; shift ;;
    --unattended)    UNATTENDED=1; shift ;;
    -h|--help)       sed -n '2,17p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *)               die "不明なオプションです: $1" ;;
  esac
done

[[ $EUID -eq 0 ]] || die "root で実行してください（sudo ./install.sh）"

# ------------------------------------------------------------ 事前チェック

if ! command -v apt-get >/dev/null 2>&1; then
  die "Debian 系（Raspberry Pi OS）以外では未対応です。"
fi

ARCH="$(dpkg --print-architecture)"
info "アーキテクチャ: ${ARCH}"
if [[ "$ARCH" != "arm64" && "$ARCH" != "armhf" && "$ARCH" != "amd64" ]]; then
  warn "想定外のアーキテクチャです。動作確認は Raspberry Pi 4B (arm64) で行っています。"
fi

PY_VERSION="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "0.0")"
if [[ "$(printf '%s\n3.11\n' "$PY_VERSION" | sort -V | head -1)" != "3.11" ]]; then
  die "Python 3.11 以上が必要です（検出: ${PY_VERSION}）。Raspberry Pi OS Bookworm 以降を使ってください。"
fi

# --------------------------------------------------------- ソースの配置

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -f "${SCRIPT_DIR}/backend/pyproject.toml" ]]; then
  SOURCE_DIR="${SCRIPT_DIR}"
  info "このリポジトリを使います: ${SOURCE_DIR}"
else
  SOURCE_DIR="${PREFIX}"
  info "リポジトリを取得します: ${REPO_URL}"
fi

# ------------------------------------------------------------ 依存パッケージ

info "依存パッケージを導入します（apt）"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
  git \
  mpd \
  mpc \
  python3 \
  python3-venv \
  python3-dev \
  build-essential \
  ffmpeg \
  alsa-utils \
  bluez \
  bluez-alsa-utils \
  rfkill \
  ca-certificates \
  curl

# --------------------------------------------------------- Bluetooth 出力
#
# Pi から BT イヤホン・スピーカーへ音を送れるようにする。bluez-alsa の
# 「bluealsa」PCM は最後に接続した機器を指すので、MPD 側は 1 つの出力定義の
# ままで機器を切り替えられる（詳細は backend/kotone/bluetooth.py）。
info "Bluetooth 出力を設定します"

# 起動時にアダプタの電源を入れる
if grep -q '^#AutoEnable=true' /etc/bluetooth/main.conf 2>/dev/null; then
  sed -i 's/^#AutoEnable=true/AutoEnable=true/' /etc/bluetooth/main.conf
fi

# Pi は「音を送る側」だけにする。受け側 (a2dp-sink) を有効にしたままだと、
# 受信音声を DAC へ流す bluealsa-aplay が MPD と出力を奪い合う。
systemctl disable --now bluealsa-aplay >/dev/null 2>&1 || true
mkdir -p /etc/systemd/system/bluealsa.service.d
cat > /etc/systemd/system/bluealsa.service.d/kotone.conf <<'BTEOF'
# kotone は Pi から BT 機器へ送る用途だけを使う。
# コーデックは既定だと SBC のみ。LDAC / aptX が使えるビルドなら有効にする
# （機器が対応していれば自動でそちらが選ばれる）。
[Service]
ExecStart=
ExecStart=/usr/bin/bluealsa -S -p a2dp-source -c sbc -c aptx -c aptx-hd -c ldac --ldac-quality=high --ldac-abr --sbc-quality=xq --a2dp-volume --keep-alive=10
BTEOF
systemctl daemon-reload
systemctl enable --now bluealsa >/dev/null 2>&1 || warn "bluealsa を起動できませんでした"

# ラウドネス計測は rsgain が FFmpeg の ebur128 より約 1.8 倍速い
# （Pi 4B 実測: 3 並列で 335x 対 182x。18,000 曲で 4.0 時間対 7.4 時間）。
# Debian 13 (trixie) 以降は公式リポジトリに入っているので既定で導入する。
# bookworm には無いため、そちらは --with-rsgain 指定時のみ GitHub から取る。
if [[ $NO_RSGAIN -eq 1 ]]; then
  info "rsgain は導入しません。ラウドネス計測は FFmpeg で行います"
elif command -v rsgain >/dev/null 2>&1; then
  info "rsgain は既に導入されています"
elif apt-cache show rsgain >/dev/null 2>&1; then
  info "rsgain を apt から導入します"
  apt-get install -y --no-install-recommends rsgain \
    || warn "rsgain の導入に失敗しました（FFmpeg で代替します）"
elif [[ $WITH_RSGAIN -eq 1 ]]; then
  info "rsgain を GitHub リリースから導入します（apt に無いため）"
  RSGAIN_VERSION="3.6"
  RSGAIN_DEB="rsgain_${RSGAIN_VERSION}-1_${ARCH}.deb"
  TMP_DEB="$(mktemp -d)/${RSGAIN_DEB}"
  if curl -fsSL -o "$TMP_DEB" \
    "https://github.com/complexlogic/rsgain/releases/download/v${RSGAIN_VERSION}/${RSGAIN_DEB}"; then
    apt-get install -y "$TMP_DEB" || warn "rsgain の導入に失敗しました（FFmpeg で代替します）"
  else
    warn "rsgain の取得に失敗しました（FFmpeg で代替します）"
  fi
  rm -rf "$(dirname "$TMP_DEB")"
else
  info "rsgain は apt に無いため導入しません（--with-rsgain で GitHub から取得できます）"
  info "ラウドネス計測は FFmpeg で行います（rsgain より 1.8 倍ほど遅くなります）"
fi

# ------------------------------------------------------------ ユーザー作成

if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  info "サービス用ユーザー ${SERVICE_USER} を作成します"
  useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi
# MPD の音楽ディレクトリを読めるようにグループへ入れる
usermod -aG audio "$SERVICE_USER" 2>/dev/null || true
usermod -aG "$SERVICE_USER" mpd 2>/dev/null || true

# ------------------------------------------------------------ ディレクトリ

MUSIC_DIR="${MUSIC_DIR:-${DATA_DIR}/music}"

info "ディレクトリを準備します"
mkdir -p "$PREFIX" "$DATA_DIR" "$DATA_DIR/artwork" "$DATA_DIR/backups" "$CONFIG_DIR" "$MUSIC_DIR"
chown -R "${SERVICE_USER}:${SERVICE_USER}" "$DATA_DIR"
chmod 755 "$DATA_DIR"

# --------------------------------------------------------- アプリの配置

if [[ "$SOURCE_DIR" != "$PREFIX" ]]; then
  info "アプリを ${PREFIX} へ配置します"
  # git 管理下のファイルだけをコピーする（.venv や node_modules を持ち込まない）
  if [[ -d "${SOURCE_DIR}/.git" ]] && command -v git >/dev/null 2>&1; then
    git -C "$SOURCE_DIR" archive HEAD | tar -x -C "$PREFIX"
    # 履歴も残して git pull で更新できるようにする
    if [[ ! -d "${PREFIX}/.git" ]]; then
      cp -a "${SOURCE_DIR}/.git" "${PREFIX}/.git"
      git -C "$PREFIX" checkout -- . 2>/dev/null || true
    fi
  else
    cp -a "${SOURCE_DIR}/." "$PREFIX/"
    rm -rf "${PREFIX}/backend/.venv" "${PREFIX}/frontend/node_modules"
  fi
elif [[ ! -d "${PREFIX}/.git" ]]; then
  info "リポジトリを clone します"
  git clone --depth 1 "$REPO_URL" "$PREFIX"
fi

[[ -f "${PREFIX}/frontend/dist/index.html" ]] \
  || warn "ビルド済みフロントエンドが見つかりません。API のみ利用できます。"

# ------------------------------------------------------------ Python 環境

info "Python 仮想環境を構築します（数分かかります）"
python3 -m venv "${PREFIX}/backend/.venv"
"${PREFIX}/backend/.venv/bin/pip" install --quiet --upgrade pip wheel
"${PREFIX}/backend/.venv/bin/pip" install --quiet "${PREFIX}/backend"

chown -R "${SERVICE_USER}:${SERVICE_USER}" "$PREFIX"

# -------------------------------------------------------------- 設定ファイル

if [[ ! -f "${CONFIG_DIR}/kotone.env" ]]; then
  info "設定ファイルを作成します: ${CONFIG_DIR}/kotone.env"
  cat > "${CONFIG_DIR}/kotone.env" <<EOF
# kotone の環境設定。変更後は systemctl restart kotone で反映されます。
KOTONE_HOST=0.0.0.0
KOTONE_PORT=${PORT}
KOTONE_MUSIC_DIR=${MUSIC_DIR}
KOTONE_DATA_DIR=${DATA_DIR}
KOTONE_MPD_HOST=localhost
KOTONE_MPD_PORT=6600
KOTONE_LOG_LEVEL=INFO
# pip インストール済みパッケージからはリポジトリ相対で frontend/dist を辿れないため明示する
KOTONE_STATIC_DIR=${PREFIX}/frontend/dist
EOF
  chmod 644 "${CONFIG_DIR}/kotone.env"
else
  info "既存の設定ファイルを保持します: ${CONFIG_DIR}/kotone.env"
fi

# ------------------------------------------------------------------ MPD

if [[ $CONFIGURE_MPD -eq 1 ]]; then
  info "MPD を設定します"
  MPD_CONF="/etc/mpd.conf"
  if [[ -f "$MPD_CONF" && ! -f "${MPD_CONF}.kotone-backup" ]]; then
    cp "$MPD_CONF" "${MPD_CONF}.kotone-backup"
    info "既存の mpd.conf を ${MPD_CONF}.kotone-backup に退避しました"
  fi
  sed -e "s|@MUSIC_DIR@|${MUSIC_DIR}|g" \
      -e "s|@DATA_DIR@|${DATA_DIR}|g" \
      "${PREFIX}/config/mpd.conf.template" > "$MPD_CONF"
  chown mpd:audio "$MPD_CONF" 2>/dev/null || true

  mkdir -p "${DATA_DIR}/mpd"
  chown -R mpd:audio "${DATA_DIR}/mpd"

  # Raspberry Pi OS の既定ではソケット起動になっていることがある
  systemctl disable --now mpd.socket >/dev/null 2>&1 || true
  systemctl enable mpd >/dev/null 2>&1 || true
  systemctl restart mpd
else
  info "mpd.conf は変更しません（--no-mpd-config）"
fi

# ------------------------------------------------------------- systemd

info "systemd サービスを登録します"
sed -e "s|@PREFIX@|${PREFIX}|g" \
    -e "s|@USER@|${SERVICE_USER}|g" \
    -e "s|@DATA_DIR@|${DATA_DIR}|g" \
    "${PREFIX}/systemd/kotone.service" > /etc/systemd/system/kotone.service

install -m 755 "${PREFIX}/scripts/kotone-update" /usr/local/bin/kotone-update
install -m 755 "${PREFIX}/scripts/kotone-cli-wrapper" /usr/local/bin/kotone-cli

# 音楽用ドライブの自動復旧（USB の瞬断で外れたドライブを付け直す）
info "音楽用ドライブの自動復旧を登録します"
install -m 755 "${PREFIX}/scripts/kotone-storage-recover" /usr/local/sbin/kotone-storage-recover
install -m 644 "${PREFIX}/systemd/kotone-storage-recover@.service" /etc/systemd/system/
install -m 644 "${PREFIX}/systemd/kotone-storage-recover.timer" /etc/systemd/system/
install -m 644 "${PREFIX}/config/99-kotone-storage.rules" /etc/udev/rules.d/
udevadm control --reload-rules >/dev/null 2>&1 || true

systemctl daemon-reload
systemctl enable kotone >/dev/null 2>&1
systemctl enable --now kotone-storage-recover.timer >/dev/null 2>&1 \
  || warn "音楽用ドライブの見回りタイマーを有効にできませんでした"
systemctl restart kotone

# --------------------------------------------------------------- 初期スキャン

sleep 2
if systemctl is-active --quiet kotone; then
  info "kotone サービスが起動しました"
else
  warn "サービスが起動していません。journalctl -u kotone -n 50 を確認してください。"
fi

IP_ADDR="$(hostname -I 2>/dev/null | awk '{print $1}')"
HOSTNAME_LOCAL="$(hostname).local"

cat <<EOF

${GREEN}${BOLD}kotone のセットアップが完了しました。${RESET}

  Web UI       http://${HOSTNAME_LOCAL}:${PORT}/
               http://${IP_ADDR:-<PiのIP>}:${PORT}/
  API ドキュメント  http://${HOSTNAME_LOCAL}:${PORT}/api/docs

  音楽ディレクトリ  ${MUSIC_DIR}
  データ領域        ${DATA_DIR}（アップデートで上書きされません）
  設定ファイル      ${CONFIG_DIR}/kotone.env

${BOLD}次の手順${RESET}
  1. 音楽ファイルを ${MUSIC_DIR} に配置します
     （SSD/USB メディアを使う場合は、そこへマウントするか
       KOTONE_MUSIC_DIR を書き換えて systemctl restart kotone）

  2. アーティスト名の区切り文字を実データで確認します（推奨）
       sudo -u ${SERVICE_USER} kotone-cli analyze-artists

  3. ライブラリをスキャンします
       sudo -u ${SERVICE_USER} kotone-cli scan
     （Web UI の「設定」画面からも実行できます）

${BOLD}運用コマンド${RESET}
  systemctl status kotone      状態確認
  systemctl restart kotone     再起動
  journalctl -u kotone -f      ログ追跡
  kotone-update                アップデート（git pull + 移行）

EOF
