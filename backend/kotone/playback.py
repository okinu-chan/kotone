"""再生制御 — 再生スコープ、リピート／シャッフル／ランダム、ラウドネス補正。

設計の詳細は ``docs/design-notes.md`` を参照。要点だけ再掲する。

* **スコープ (PlayScope)** … 「アルバム内」「プレイリスト内」「全曲」等の再生範囲。
  再生開始時にスコープの全曲を MPD のキューへ読み込む。
* **シャッフル（重複なし）** … MPD の ``random`` フラグ。キューを走査しきるまで
  重複しないので要件を満たす。
* **ランダム（重複あり）** … MPD に相当機能が無いため独自実装。キューを
  ``[再生中, 次の1曲]`` に保ち、曲が変わるたびに次の 1 曲を抽選して追加する。
  次曲を先にキューへ入れておくことで MPD の gapless 機構がそのまま効く。
* **ラウドネス補正** … 曲が変わるたびに ``setvol`` で実効音量を計算し直す。
  UI に見せるのはユーザーが設定した音量のほうで、補正値は裏に隠す。
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from typing import Any, Literal

from kotone.db import Database
from kotone.events import EventBus
from kotone.mpdclient import MPDConnection, MPDUnavailable
from kotone.repository import LibraryRepository
from kotone.settings import SettingsStore

log = logging.getLogger(__name__)

RepeatMode = Literal["off", "one", "range"]
OrderMode = Literal["normal", "shuffle", "random"]

REPEAT_MODES: tuple[str, ...] = ("off", "one", "range")
ORDER_MODES: tuple[str, ...] = ("normal", "shuffle", "random")

#: ランダムモードで先読みしておく曲数（gapless のために 1 曲だけ先行させる）
RANDOM_LOOKAHEAD = 1


@dataclass
class PlayScope:
    kind: str = "all"           # all | album | artist | genre | year | playlist
    id: str | None = None
    label: str = "すべての曲"

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "id": self.id, "label": self.label}


@dataclass
class _RandomState:
    """ランダム（重複あり）モードの内部状態。"""

    pool: list[str] = field(default_factory=list)   # スコープ内の全曲パス
    recent: list[str] = field(default_factory=list)  # 直近に選んだ曲（連続回避用）


class PlaybackController:
    def __init__(
        self,
        mpd: MPDConnection,
        db: Database,
        repo: LibraryRepository,
        settings: SettingsStore,
        events: EventBus,
    ) -> None:
        self.mpd = mpd
        self.db = db
        self.repo = repo
        self.settings = settings
        self.events = events

        self.scope = PlayScope()
        self.repeat_mode: RepeatMode = "off"
        self.order_mode: OrderMode = "normal"
        self.user_volume = 60

        self._random = _RandomState()
        self._expected_volume: int | None = None
        self._last_song_id: str | None = None
        self._lock = asyncio.Lock()
        self._cached_state: dict[str, Any] = {}

    # ============================================================ 起動・復元

    async def start(self) -> None:
        self._restore_state()
        self.mpd.on_event(self._on_mpd_event)
        # MPD が落ちていてもバックエンドの起動は止めない。接続できたら
        # idle イベント経由で設定が反映される。
        if await self.mpd.wait_connected(timeout=2.0):
            try:
                await self._apply_mode_flags()
                await self.apply_crossfade()
            except MPDUnavailable:
                log.warning("起動時に MPD へ設定を反映できませんでした")

    def _restore_state(self) -> None:
        row = self.db.query_one("SELECT * FROM player_state WHERE id = 1")
        if not row:
            return
        self.scope = PlayScope(
            kind=row["scope_kind"],
            id=row["scope_id"],
            label=self.repo.scope_label(row["scope_kind"], row["scope_id"]),
        )
        if row["repeat_mode"] in REPEAT_MODES:
            self.repeat_mode = row["repeat_mode"]
        if row["order_mode"] in ORDER_MODES:
            self.order_mode = row["order_mode"]
        self.user_volume = int(row["user_volume"])

    def _persist_state(self) -> None:
        self.db.execute(
            """
            UPDATE player_state SET scope_kind = ?, scope_id = ?, repeat_mode = ?,
                order_mode = ?, user_volume = ?, updated_at = datetime('now')
            WHERE id = 1
            """,
            (self.scope.kind, self.scope.id, self.repeat_mode,
             self.order_mode, self.user_volume),
        )
        self.db.commit()

    # ============================================================ イベント

    async def _on_mpd_event(self, subsystems: set[str]) -> None:
        try:
            if "connection" in subsystems and self.mpd.connected:
                # 再接続時にモードとクロスフェードを入れ直す
                await self._apply_mode_flags()
                await self.apply_crossfade()
            if "mixer" in subsystems:
                await self._reconcile_external_volume()
            if "player" in subsystems:
                await self._on_song_changed()
            if subsystems & {"player", "mixer", "options", "playlist", "connection"}:
                await self.broadcast_state()
        except MPDUnavailable:
            self.events.publish("player", {"connected": False})
        except Exception:
            log.exception("MPD イベントの処理でエラーが発生しました")

    async def _on_song_changed(self) -> None:
        status = await self.mpd.status()
        song_id = status.get("songid")
        if song_id == self._last_song_id:
            return
        self._last_song_id = song_id

        await self._apply_loudness(status)
        if self.order_mode == "random":
            await self._top_up_random_queue(status)

    # ======================================================== 再生スコープ

    async def play_scope(
        self,
        kind: str,
        scope_id: str | None = None,
        *,
        start_track_id: int | None = None,
    ) -> dict[str, Any]:
        """スコープを読み込んで再生を開始する。

        ``start_track_id`` を渡すとその曲から始まる（要件 3.4 / 3.6）。
        """
        async with self._lock:
            tracks = await asyncio.to_thread(self.repo.resolve_scope, kind, scope_id)
            if not tracks:
                raise ValueError("再生できる曲がありません")

            self.scope = PlayScope(
                kind=kind,
                id=str(scope_id) if scope_id is not None else None,
                label=await asyncio.to_thread(self.repo.scope_label, kind, scope_id),
            )
            self._random = _RandomState(pool=[t["path"] for t in tracks])

            await self.mpd.clear()
            if self.order_mode == "random":
                start_path = None
                if start_track_id is not None:
                    start_path = next(
                        (t["path"] for t in tracks if t["id"] == start_track_id), None
                    )
                await self._fill_random_queue(first=start_path)
                await self.mpd.play(0)
            else:
                await self.mpd.add_many(t["path"] for t in tracks)
                index = 0
                if start_track_id is not None:
                    index = next(
                        (i for i, t in enumerate(tracks) if t["id"] == start_track_id), 0
                    )
                await self._apply_mode_flags()
                await self.mpd.play(index)

            self._persist_state()

        await self._on_song_changed()
        await self.broadcast_state()
        return self.state_snapshot()

    async def play_tracks(self, track_ids: list[int], *, start_index: int = 0) -> None:
        """任意の曲リストを直接キューへ入れて再生する（検索結果からの再生など）。"""
        async with self._lock:
            tracks = [self.repo.get_track(tid) for tid in track_ids]
            paths = [t["path"] for t in tracks if t]
            if not paths:
                raise ValueError("再生できる曲がありません")
            self.scope = PlayScope(kind="custom", id=None, label="選択した曲")
            self._random = _RandomState(pool=paths)
            await self.mpd.clear()
            await self.mpd.add_many(paths)
            await self._apply_mode_flags()
            await self.mpd.play(min(start_index, len(paths) - 1))
            self._persist_state()
        await self.broadcast_state()

    # ============================================================ 基本操作

    async def toggle(self) -> None:
        status = await self.mpd.status()
        state = status.get("state")
        if state == "play":
            await self.mpd.pause(True)
        elif state == "pause":
            await self.mpd.pause(False)
        else:
            await self.mpd.play()

    async def play(self) -> None:
        await self.mpd.play()

    async def pause(self) -> None:
        await self.mpd.pause(True)

    async def stop(self) -> None:
        await self.mpd.stop()

    async def next(self) -> None:
        if self.order_mode == "random":
            # キューには次の 1 曲が入っているので通常の next で進める。
            # 進んだ後のイベントで補充される。
            await self.mpd.next()
            return
        await self.mpd.next()

    async def previous(self) -> None:
        status = await self.mpd.status()
        elapsed = float(status.get("elapsed") or 0)
        # 3 秒以上再生していたら曲の先頭へ戻す（一般的なプレイヤーの挙動）
        if elapsed > 3.0:
            await self.mpd.seek(0)
            return
        if self.order_mode == "random":
            # ランダムでは過去のキューを保持していないため先頭へ戻すだけにする
            await self.mpd.seek(0)
            return
        await self.mpd.previous()

    async def seek(self, seconds: float) -> None:
        await self.mpd.seek(max(0.0, seconds))

    # ============================================================ モード

    async def set_repeat(self, mode: RepeatMode) -> None:
        if mode not in REPEAT_MODES:
            raise ValueError(f"未知のリピートモードです: {mode}")
        self.repeat_mode = mode
        await self._apply_mode_flags()
        self._persist_state()
        await self.broadcast_state()

    async def set_order(self, mode: OrderMode) -> None:
        if mode not in ORDER_MODES:
            raise ValueError(f"未知の再生順モードです: {mode}")
        previous = self.order_mode
        self.order_mode = mode
        await self._apply_mode_flags()

        if mode == "random" and previous != "random":
            await self._enter_random_mode()
        elif previous == "random" and mode != "random":
            await self._leave_random_mode()

        self._persist_state()
        await self.broadcast_state()

    async def _apply_mode_flags(self) -> None:
        await self.mpd.set_repeat(self.repeat_mode in ("one", "range"))
        await self.mpd.set_single(self.repeat_mode == "one")
        await self.mpd.set_random(self.order_mode == "shuffle")
        await self.mpd.set_consume(False)

    async def apply_crossfade(self) -> None:
        enabled = bool(self.settings.get("crossfade_enabled"))
        seconds = int(self.settings.get("crossfade_seconds") or 0)
        await self.mpd.set_crossfade(seconds if enabled else 0)

    # ------------------------------------------------ ランダム（重複あり）

    async def _enter_random_mode(self) -> None:
        """現在の曲を残したままキューをランダムモードの形へ組み替える。"""
        if not self._random.pool:
            tracks = await asyncio.to_thread(
                self.repo.resolve_scope, self.scope.kind, self.scope.id
            )
            self._random.pool = [t["path"] for t in tracks]
        if not self._random.pool:
            return

        status = await self.mpd.status()
        current = await self.mpd.current_song()
        current_path = current.get("file")
        was_playing = status.get("state") == "play"

        await self.mpd.clear()
        await self._fill_random_queue(first=current_path)
        if was_playing:
            await self.mpd.play(0)
            if current_path:
                # 途中まで聴いていた位置を復元する
                elapsed = float(status.get("elapsed") or 0)
                if elapsed > 1.0:
                    await self.mpd.seek(elapsed)

    async def _leave_random_mode(self) -> None:
        """スコープの全曲をキューへ戻す（通常／シャッフルへの復帰）。"""
        tracks = await asyncio.to_thread(
            self.repo.resolve_scope, self.scope.kind, self.scope.id
        )
        if not tracks:
            return
        current = await self.mpd.current_song()
        current_path = current.get("file")
        await self.mpd.clear()
        await self.mpd.add_many(t["path"] for t in tracks)
        if current_path:
            index = next((i for i, t in enumerate(tracks) if t["path"] == current_path), None)
            if index is not None:
                await self.mpd.play(index)

    async def _fill_random_queue(self, *, first: str | None = None) -> None:
        if first:
            await self.mpd.add_id(first)
            self._remember(first)
        while True:
            queue = await self.mpd.queue()
            if len(queue) >= 1 + RANDOM_LOOKAHEAD:
                break
            pick = self._pick_random()
            if pick is None:
                break
            await self.mpd.add_id(pick)
            self._remember(pick)

    async def _top_up_random_queue(self, status: dict[str, Any]) -> None:
        """曲が切り替わったら、消費済みを削って次の 1 曲を補充する。"""
        try:
            position = int(status.get("song", 0))
        except (TypeError, ValueError):
            position = 0

        queue = await self.mpd.queue()
        # 再生位置より前のエントリを削除して、常に先頭が再生中になるようにする
        for entry in queue[:position]:
            try:
                await self.mpd.delete_id(int(entry["id"]))
            except Exception:  # pragma: no cover - 競合時は次回に任せる
                pass

        queue = await self.mpd.queue()
        while len(queue) < 1 + RANDOM_LOOKAHEAD:
            pick = self._pick_random()
            if pick is None:
                break
            await self.mpd.add_id(pick)
            self._remember(pick)
            queue = await self.mpd.queue()

    def _pick_random(self) -> str | None:
        pool = self._random.pool
        if not pool:
            return None
        avoid = int(self.settings.get("random_avoid_last") or 0)
        recent = set(self._random.recent[-avoid:]) if avoid else set()
        candidates = [p for p in pool if p not in recent] or pool
        return random.choice(candidates)  # noqa: S311 - 暗号用途ではない

    def _remember(self, path: str) -> None:
        self._random.recent.append(path)
        limit = max(1, int(self.settings.get("random_avoid_last") or 0)) * 4
        if len(self._random.recent) > limit:
            del self._random.recent[:-limit]

    # ============================================================ 音量

    async def set_volume(self, volume: int) -> None:
        self.user_volume = max(0, min(100, int(volume)))
        self._persist_state()
        await self._apply_loudness(await self.mpd.status())
        await self.broadcast_state()

    async def _reconcile_external_volume(self) -> None:
        """他クライアント等が音量を変えた場合に、ユーザー音量を追随させる。"""
        status = await self.mpd.status()
        try:
            observed = int(status.get("volume", -1))
        except (TypeError, ValueError):
            return
        if observed < 0 or self._expected_volume is None:
            return
        if observed == self._expected_volume:
            return
        factor = await self._current_factor()
        self.user_volume = max(0, min(100, round(observed / factor))) if factor else observed
        self._expected_volume = observed
        self._persist_state()

    async def _apply_loudness(self, status: dict[str, Any]) -> None:
        """現在の曲に合わせて実効音量を設定する。"""
        factor = await self._current_factor()
        effective = max(0, min(100, round(self.user_volume * factor)))
        try:
            current = int(status.get("volume", -1))
        except (TypeError, ValueError):
            current = -1
        if current == effective:
            self._expected_volume = effective
            return
        await self.mpd.set_volume(effective)
        self._expected_volume = effective

    async def _current_factor(self) -> float:
        """現在の曲に適用すべき音量倍率。補正が無効なら 1.0。"""
        if not self.settings.get("loudness_enabled"):
            return 1.0
        song = await self.mpd.current_song()
        path = song.get("file")
        if not path:
            return 1.0
        track = await asyncio.to_thread(self.repo.get_track_by_path, path)
        if not track:
            return 1.0

        mode = self.settings.get("loudness_mode") or "track"
        gain = track.get("album_gain_db") if mode == "album" else track.get("track_gain_db")
        peak = track.get("album_peak") if mode == "album" else track.get("track_peak")
        if gain is None:
            return 1.0

        preamp = float(self.settings.get("loudness_preamp_db") or 0.0)
        factor = 10 ** ((float(gain) + preamp) / 20.0)

        if self.settings.get("loudness_clip_prevention") and peak:
            try:
                max_factor = 1.0 / float(peak)
            except (TypeError, ValueError, ZeroDivisionError):
                max_factor = factor
            factor = min(factor, max(max_factor, 0.01))
        return max(0.01, min(factor, 4.0))

    # ============================================================ 状態

    async def build_state(self) -> dict[str, Any]:
        if not self.mpd.connected:
            return {
                "connected": False,
                "scope": self.scope.as_dict(),
                "repeat": self.repeat_mode,
                "order": self.order_mode,
                "volume": self.user_volume,
                "state": "stop",
            }
        status = await self.mpd.status()
        song = await self.mpd.current_song()
        track = None
        if song.get("file"):
            track = await asyncio.to_thread(self.repo.get_track_by_path, song["file"])

        state = {
            "connected": True,
            "state": status.get("state", "stop"),
            "elapsed": float(status.get("elapsed") or 0),
            "duration": float(status.get("duration") or (track or {}).get("duration") or 0),
            "volume": self.user_volume,
            "effective_volume": _int_or_none(status.get("volume")),
            "repeat": self.repeat_mode,
            "order": self.order_mode,
            "crossfade": _int_or_none(status.get("xfade")) or 0,
            "scope": self.scope.as_dict(),
            "queue_length": _int_or_none(status.get("playlistlength")) or 0,
            "queue_position": _int_or_none(status.get("song")),
            "audio": _parse_audio_format(status.get("audio")),
            "bitrate": _int_or_none(status.get("bitrate")),
            "track": track,
            "raw_file": song.get("file"),
        }
        self._cached_state = state
        return state

    def state_snapshot(self) -> dict[str, Any]:
        return dict(self._cached_state)

    async def broadcast_state(self) -> None:
        state = await self.build_state()
        self.events.publish("player", state)


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_audio_format(value: str | None) -> dict[str, Any] | None:
    """MPD の ``audio`` フィールド（``44100:16:2`` 等）を分解する。"""
    if not value:
        return None
    parts = str(value).split(":")
    if len(parts) < 3:
        return None
    samplerate, bits, channels = parts[0], parts[1], parts[2]
    return {
        "samplerate": _int_or_none(samplerate),
        # DSD は bits が "dsd" になる
        "bit_depth": _int_or_none(bits),
        "is_dsd": str(bits).lower().startswith("dsd"),
        "channels": _int_or_none(channels),
        "raw": value,
    }
