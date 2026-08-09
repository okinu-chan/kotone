-- kotone 初期スキーマ
--
-- 命名方針:
--   *_reading      … 形態素解析で得たカタカナ読み（表示・編集対象）
--   *_sort_key     … 比較用に正規化した文字列（濁点除去・小書き展開・長音展開済み）
--   *_initial      … イニシャルサーチ用の頭文字（'あ'..'わ' / 'A'..'Z' / '#'）
--   *_initial_group… 'kana' | 'latin' | 'other'（イニシャルサーチのタブに対応）
--   *_locked       … ユーザーが手動修正した印。再スキャンで自動生成値に上書きしない

-- ============================================================ アーティスト
CREATE TABLE artists (
    id             INTEGER PRIMARY KEY,
    name           TEXT    NOT NULL,
    reading        TEXT    NOT NULL DEFAULT '',
    sort_key       TEXT    NOT NULL DEFAULT '',
    initial        TEXT    NOT NULL DEFAULT '#',
    initial_group  TEXT    NOT NULL DEFAULT 'other',
    reading_locked INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (name)
);
CREATE INDEX idx_artists_sort    ON artists (sort_key, reading, name);
CREATE INDEX idx_artists_initial ON artists (initial_group, initial, sort_key);

-- ============================================================ ジャンル
CREATE TABLE genres (
    id             INTEGER PRIMARY KEY,
    name           TEXT    NOT NULL,
    reading        TEXT    NOT NULL DEFAULT '',
    sort_key       TEXT    NOT NULL DEFAULT '',
    initial        TEXT    NOT NULL DEFAULT '#',
    initial_group  TEXT    NOT NULL DEFAULT 'other',
    reading_locked INTEGER NOT NULL DEFAULT 0,
    UNIQUE (name)
);
CREATE INDEX idx_genres_sort ON genres (sort_key, reading, name);

-- ============================================================ アルバム
CREATE TABLE albums (
    id              INTEGER PRIMARY KEY,
    -- 同名異アルバムを区別するための合成キー（正規化名 + アルバムアーティスト + MBID/ディレクトリ）
    album_key       TEXT    NOT NULL,
    name            TEXT    NOT NULL,
    reading         TEXT    NOT NULL DEFAULT '',
    sort_key        TEXT    NOT NULL DEFAULT '',
    initial         TEXT    NOT NULL DEFAULT '#',
    initial_group   TEXT    NOT NULL DEFAULT 'other',
    reading_locked  INTEGER NOT NULL DEFAULT 0,
    album_artist_id INTEGER REFERENCES artists (id) ON DELETE SET NULL,
    year            INTEGER,
    release_date    TEXT,
    directory       TEXT    NOT NULL DEFAULT '',
    musicbrainz_id  TEXT,
    is_compilation  INTEGER NOT NULL DEFAULT 0,
    added_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (album_key)
);
CREATE INDEX idx_albums_sort     ON albums (sort_key, reading, name);
CREATE INDEX idx_albums_initial  ON albums (initial_group, initial, sort_key);
CREATE INDEX idx_albums_artist   ON albums (album_artist_id);
CREATE INDEX idx_albums_year     ON albums (year);
CREATE INDEX idx_albums_added    ON albums (added_at);
CREATE INDEX idx_albums_dir      ON albums (directory);

-- ============================================================ 楽曲
CREATE TABLE tracks (
    id                   INTEGER PRIMARY KEY,
    -- music_directory からの相対パス（POSIX 区切り）。MPD へ渡す ID でもある
    path                 TEXT    NOT NULL,
    mtime                REAL    NOT NULL DEFAULT 0,
    size                 INTEGER NOT NULL DEFAULT 0,

    title                TEXT    NOT NULL DEFAULT '',
    reading              TEXT    NOT NULL DEFAULT '',
    sort_key             TEXT    NOT NULL DEFAULT '',
    initial              TEXT    NOT NULL DEFAULT '#',
    initial_group        TEXT    NOT NULL DEFAULT 'other',
    reading_locked       INTEGER NOT NULL DEFAULT 0,

    album_id             INTEGER REFERENCES albums (id) ON DELETE SET NULL,
    disc_no              INTEGER NOT NULL DEFAULT 1,
    track_no             INTEGER NOT NULL DEFAULT 0,

    duration             REAL    NOT NULL DEFAULT 0,
    codec                TEXT    NOT NULL DEFAULT '',
    samplerate           INTEGER,
    bit_depth            INTEGER,
    channels             INTEGER,
    bitrate              INTEGER,
    is_dsd               INTEGER NOT NULL DEFAULT 0,

    year                 INTEGER,
    release_date         TEXT,

    -- ラウドネス補正（EBU R128 / rsgain 計測値。元ファイルにはタグを書かない）
    track_gain_db        REAL,
    track_peak           REAL,
    album_gain_db        REAL,
    album_peak           REAL,
    gain_scanned_at      TEXT,

    artists_locked       INTEGER NOT NULL DEFAULT 0,
    tags_locked          INTEGER NOT NULL DEFAULT 0,

    added_at             TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at           TEXT    NOT NULL DEFAULT (datetime('now')),
    missing              INTEGER NOT NULL DEFAULT 0,
    UNIQUE (path)
);
CREATE INDEX idx_tracks_album     ON tracks (album_id, disc_no, track_no);
CREATE INDEX idx_tracks_sort      ON tracks (sort_key, reading, title);
CREATE INDEX idx_tracks_initial   ON tracks (initial_group, initial, sort_key);
CREATE INDEX idx_tracks_year      ON tracks (year);
CREATE INDEX idx_tracks_added     ON tracks (added_at);
CREATE INDEX idx_tracks_missing   ON tracks (missing);

-- ------------------------------------------------------------ 多対多
CREATE TABLE track_artists (
    track_id  INTEGER NOT NULL REFERENCES tracks (id) ON DELETE CASCADE,
    artist_id INTEGER NOT NULL REFERENCES artists (id) ON DELETE CASCADE,
    role      TEXT    NOT NULL DEFAULT 'artist',  -- 'artist' | 'composer' | 'performer'
    position  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (track_id, artist_id, role)
);
CREATE INDEX idx_track_artists_artist ON track_artists (artist_id, role);

CREATE TABLE album_artists (
    album_id  INTEGER NOT NULL REFERENCES albums (id) ON DELETE CASCADE,
    artist_id INTEGER NOT NULL REFERENCES artists (id) ON DELETE CASCADE,
    position  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (album_id, artist_id)
);
CREATE INDEX idx_album_artists_artist ON album_artists (artist_id);

CREATE TABLE track_genres (
    track_id INTEGER NOT NULL REFERENCES tracks (id) ON DELETE CASCADE,
    genre_id INTEGER NOT NULL REFERENCES genres (id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (track_id, genre_id)
);
CREATE INDEX idx_track_genres_genre ON track_genres (genre_id);

-- ============================================================ アートワーク
CREATE TABLE artwork (
    id          INTEGER PRIMARY KEY,
    album_id    INTEGER NOT NULL REFERENCES albums (id) ON DELETE CASCADE,
    -- 'embedded' | 'folder' | 'ffmpeg'
    source_kind TEXT    NOT NULL,
    source_path TEXT,
    source_hash TEXT    NOT NULL,
    src_width   INTEGER NOT NULL,
    src_height  INTEGER NOT NULL,
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (album_id)
);

CREATE TABLE artwork_files (
    artwork_id INTEGER NOT NULL REFERENCES artwork (id) ON DELETE CASCADE,
    -- 'small'(短辺300) | 'medium'(短辺800) | 'large'(短辺1400)
    tier       TEXT    NOT NULL,
    path       TEXT    NOT NULL,   -- artwork_cache_dir からの相対パス
    width      INTEGER NOT NULL,
    height     INTEGER NOT NULL,
    bytes      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (artwork_id, tier)
);

-- ============================================================ プレイリスト
CREATE TABLE playlists (
    id            INTEGER PRIMARY KEY,
    name          TEXT    NOT NULL,
    reading       TEXT    NOT NULL DEFAULT '',
    sort_key      TEXT    NOT NULL DEFAULT '',
    initial       TEXT    NOT NULL DEFAULT '#',
    initial_group TEXT    NOT NULL DEFAULT 'other',
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (name)
);
CREATE INDEX idx_playlists_sort ON playlists (sort_key, reading, name);

CREATE TABLE playlist_tracks (
    playlist_id INTEGER NOT NULL REFERENCES playlists (id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    track_id    INTEGER NOT NULL REFERENCES tracks (id) ON DELETE CASCADE,
    added_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (playlist_id, position)
);
CREATE INDEX idx_playlist_tracks_track ON playlist_tracks (track_id);

-- ============================================================ 設定・状態
CREATE TABLE settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,          -- JSON エンコードされた値
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE scan_state (
    id             INTEGER PRIMARY KEY CHECK (id = 1),
    status         TEXT    NOT NULL DEFAULT 'idle',   -- idle | running | error
    phase          TEXT    NOT NULL DEFAULT '',
    processed      INTEGER NOT NULL DEFAULT 0,
    total          INTEGER NOT NULL DEFAULT 0,
    started_at     TEXT,
    finished_at    TEXT,
    error          TEXT,
    added          INTEGER NOT NULL DEFAULT 0,
    updated        INTEGER NOT NULL DEFAULT 0,
    removed        INTEGER NOT NULL DEFAULT 0
);
INSERT INTO scan_state (id) VALUES (1);

-- 再生状態の永続化（再起動後に前回のスコープ・モードを復元する）
CREATE TABLE player_state (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    scope_kind    TEXT    NOT NULL DEFAULT 'all',
    scope_id      TEXT,
    repeat_mode   TEXT    NOT NULL DEFAULT 'off',    -- off | one | range
    order_mode    TEXT    NOT NULL DEFAULT 'normal', -- normal | shuffle | random
    user_volume   INTEGER NOT NULL DEFAULT 60,
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
INSERT INTO player_state (id) VALUES (1);
