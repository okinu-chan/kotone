-- 「最近追加したアルバム」の起点を、いま入っているライブラリの後ろに置く
--
-- 最初の取り込みではライブラリ全体が同じ時期に入るため、追加日で見ると
-- 全部が新着になってしまう。更新前から使っている環境では、この時点を
-- 起点として控え、これ以降に増えたぶんだけを新着として扱う。
--
-- まだ 1 枚も入っていない（これから初回スキャンをする）環境では起点を
-- 置かない。初回スキャンの直後に、そのときの時刻で設定される
-- （StatsStore.ensure_recent_baseline）。

INSERT INTO runtime_stats (key, value, updated_at)
SELECT 'recent_baseline', datetime('now'), datetime('now')
WHERE EXISTS (SELECT 1 FROM albums)
ON CONFLICT (key) DO NOTHING;
