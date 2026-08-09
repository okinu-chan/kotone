#!/usr/bin/env bash
#
# プロジェクト名の一括変更（要件定義書 8 章の保留事項を解消するためのスクリプト）。
#
#   ./scripts/rename-project.sh <新しい名前>
#
# 例: ./scripts/rename-project.sh otoha
#
# 変更対象:
#   ・Python パッケージ名          backend/kotone/ → backend/<新名>/
#   ・systemd サービス名           kotone.service → <新名>.service
#   ・環境変数の接頭辞             KOTONE_ → <大文字の新名>_
#   ・各種パス                     /opt/kotone, /var/lib/kotone, /etc/kotone
#   ・CLI 名                       kotone-cli, kotone-update
#
# 実行後は必ず backend のテストとフロントエンドのビルドを通してください。
#
set -euo pipefail

OLD="kotone"
NEW="${1:-}"

if [[ -z "$NEW" ]]; then
  echo "使い方: $0 <新しい名前>" >&2
  exit 1
fi

if [[ ! "$NEW" =~ ^[a-z][a-z0-9_-]*$ ]]; then
  echo "名前は英小文字で始まる [a-z0-9_-] のみで指定してください（Python パッケージ名にも使うため）" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

OLD_UPPER="$(echo "$OLD" | tr '[:lower:]' '[:upper:]')"
NEW_UPPER="$(echo "$NEW" | tr '[:lower:]' '[:upper:]')"
# Python パッケージ名にハイフンは使えない
NEW_PKG="${NEW//-/_}"

echo "==> ${OLD} → ${NEW} に変更します（パッケージ名: ${NEW_PKG}）"

# 1. ディレクトリ名
if [[ -d "backend/${OLD}" ]]; then
  git mv "backend/${OLD}" "backend/${NEW_PKG}" 2>/dev/null || mv "backend/${OLD}" "backend/${NEW_PKG}"
fi

# 2. ファイル名
for path in systemd/${OLD}.service scripts/${OLD}-update scripts/${OLD}-cli-wrapper \
            config/mpd.conf.template; do
  [[ -e "$path" ]] || continue
  target="${path//${OLD}/${NEW}}"
  [[ "$path" == "$target" ]] && continue
  git mv "$path" "$target" 2>/dev/null || mv "$path" "$target"
done

# 3. 中身の置換（バイナリと生成物は除く）
echo "==> ファイル内の文字列を置換します"
find . \
  -type d \( -name .git -o -name .venv -o -name node_modules -o -name dist \
             -o -name __pycache__ -o -name '*.egg-info' \) -prune -o \
  -type f \( -name '*.py' -o -name '*.js' -o -name '*.svelte' -o -name '*.css' \
             -o -name '*.html' -o -name '*.json' -o -name '*.toml' -o -name '*.md' \
             -o -name '*.sh' -o -name '*.sql' -o -name '*.service' -o -name '*.template' \
             -o -name "${NEW}-update" -o -name "${NEW}-cli-wrapper" \) -print0 |
  while IFS= read -r -d '' file; do
    sed -i \
      -e "s/${OLD_UPPER}_/${NEW_UPPER}_/g" \
      -e "s/\b${OLD}\b/${NEW}/g" \
      -e "s/${OLD}_/${NEW_PKG}_/g" \
      "$file"
  done

# パッケージ名がハイフン入りだった場合の後始末（import 文はアンダースコア）
if [[ "$NEW" != "$NEW_PKG" ]]; then
  find . -type d \( -name .git -o -name .venv -o -name node_modules -o -name dist \) -prune -o \
    -type f -name '*.py' -print0 |
    while IFS= read -r -d '' file; do
      sed -i -e "s/^from ${NEW}/from ${NEW_PKG}/" -e "s/^import ${NEW}/import ${NEW_PKG}/" "$file"
    done
fi

cat <<EOF

==> 置換が完了しました。

次の手順で確認してください:

  cd backend && python -m pytest -q
  cd ../frontend && npm run build

問題がなければコミットしてください:

  git add -A && git commit -m "rename: ${OLD} -> ${NEW}"

要件定義書 8 章の「プロジェクト名」の項目も忘れずに更新してください。
EOF
