-- プレイリストの並び順を利用者が決められるようにする
--
-- これまでは名前の読み順で固定だった。よく聴くものを上に置きたいので、
-- 手で並べ替えられる位置を持たせる。既存のプレイリストには、いまの
-- 表示順（読み順）をそのまま初期値として振る。

ALTER TABLE playlists ADD COLUMN position INTEGER NOT NULL DEFAULT 0;

UPDATE playlists
SET position = (
    SELECT COUNT(*) FROM playlists other
    WHERE other.sort_key < playlists.sort_key
       OR (other.sort_key = playlists.sort_key AND other.id < playlists.id)
);

CREATE INDEX idx_playlists_position ON playlists (position);
