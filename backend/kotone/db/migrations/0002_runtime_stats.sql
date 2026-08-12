-- 実行時に観測した統計値の保存先。
--
-- ユーザー設定（settings）とは分ける。こちらはアプリが自動で書き込む値で、
-- 画面から編集するものではない。いまのところラウドネス計測のスループット
-- （実時間比）を記録し、次回以降の所要時間の見積もりに使う。

CREATE TABLE runtime_stats (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
