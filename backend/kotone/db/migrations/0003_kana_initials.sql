-- イニシャルサーチのカナを行の代表から 1 文字ずつへ移行する（要件 3.7）
--
-- 「英数・他」タブが A〜Z を 1 文字ずつ並べるのに対し、カナタブは行の代表
-- （あ・か・さ…）10 個だけだった。五十音すべてを並べるため、initial に
-- 1 文字そのものを入れる。
--
-- ソートキーは濁点除去・小書き展開・前後の空白除去を済ませてあるので、
-- カナ扱いの行では先頭 1 文字がそのまま頭文字になる（「ギ」→「き」）。
-- 再スキャンなしで既存の値を詰め替えられる。

UPDATE artists   SET initial = substr(sort_key, 1, 1) WHERE initial_group = 'kana';
UPDATE genres    SET initial = substr(sort_key, 1, 1) WHERE initial_group = 'kana';
UPDATE albums    SET initial = substr(sort_key, 1, 1) WHERE initial_group = 'kana';
UPDATE tracks    SET initial = substr(sort_key, 1, 1) WHERE initial_group = 'kana';
UPDATE playlists SET initial = substr(sort_key, 1, 1) WHERE initial_group = 'kana';
