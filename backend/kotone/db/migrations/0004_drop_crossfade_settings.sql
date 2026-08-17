-- 廃止したクロスフェード設定を settings テーブルから消す
--
-- 未知のキーは読み込み時に無視されるので消さなくても動くが、残しておくと
-- 「設定してあるのに効かない」値が DB に居座ることになる。
-- 機能を取り下げた理由は kotone/settings.py の冒頭を参照。

DELETE FROM settings WHERE key IN ('crossfade_enabled', 'crossfade_seconds');
