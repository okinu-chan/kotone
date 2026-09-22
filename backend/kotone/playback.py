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
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from kotone.db import Database
from kotone.events import EventBus
from kotone.mpdclient import MPDConnection, MPDUnavailable
from kotone.outputs import BLUETOOTH_OUTPUT_NAME
from kotone.repository import LibraryRepository
from kotone.settings import SettingsStore

log = logging.getLogger(__name__)

RepeatMode = Literal["off", "one", "range"]
OrderMode = Literal["normal", "shuffle", "random"]

REPEAT_MODES: tuple[str, ...] = ("off", "one", "range")
ORDER_MODES: tuple[str, ...] = ("normal", "shuffle", "random")

#: ランダムモードで先読みしておく曲数（gapless のために 1 曲だけ先行させる）
RANDOM_LOOKAHEAD = 1

#: 一時停止からの再開時、これ以上ずれていたら止めた位置へ戻す（秒）
RESUME_DRIFT_TOLERANCE = 0.4

#: 曲が変わったときに実効音量を移す時間（秒）。
#:
#: ラウドネス補正はソフトウェアミキサーの音量で当てているため、曲が変わった
#: 瞬間に setvol で切り替えると出力に段差ができ、「プツッ」というノイズになる。
#: 実ライブラリには隣り合う曲で 29dB 差（音量 100 → 41 の跳び）という例もある。
#: 曲間が無音なら気づかないが、ギャップレス再生でつながっているライブ盤などでは
#: 段差がそのまま聴こえる。数百ミリ秒かけて移せば段差が無くなる。
SONG_CHANGE_FADE_SECONDS = 0.35

#: 音量スライダー操作を反映する時間（秒）。追従が遅れない範囲で段差を消す
USER_VOLUME_FADE_SECONDS = 0.12

#: フェード中に setvol を送る間隔（秒）。
#:
#: 細かいほど段差が小さくなる。1 段ごとに mixer イベントが返ってくるが、
#: フェード中の配信は止めてあるので 50 回/秒でも負荷にはならない。
#: 音量は 0〜100 の整数なので、これ以上細かくしても同じ値を送るだけになる。
VOLUME_FADE_STEP_SECONDS = 0.02


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
        outputs: Any = None,
        bluetooth: Any = None,
    ) -> None:
        self.mpd = mpd
        self.db = db
        self.repo = repo
        self.settings = settings
        self.events = events
        #: 出力先（USB DAC / Bluetooth）と BT の情報。再生中画面に出すために持つ
        self.outputs = outputs
        self.bluetooth = bluetooth

        self.scope = PlayScope()
        self.repeat_mode: RepeatMode = "off"
        self.order_mode: OrderMode = "normal"
        self.user_volume = 60

        self._random = _RandomState()
        self._expected_volume: int | None = None
        #: 実行中の音量フェード。新しいフェードが始まったら前のものは打ち切る
        self._fade_task: asyncio.Task[None] | None = None
        #: 最後に MPD へ送った音量（フェードを途中から引き継ぐときの起点）
        self._last_sent_volume: int | None = None
        self._last_song_id: str | None = None
        # 一時停止した位置と、そのときの曲（再開時のズレ補正に使う）
        self._paused_at: float | None = None
        self._paused_song_id: str | None = None
        # 直前の再生状態と、明示的な停止かどうか（範囲を鳴らし終えた判定に使う）
        self._last_play_state: str | None = None
        self._user_stopped = False
        self._returning_to_top = False
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
                await self._disable_crossfade()
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
                # 再接続時にモードを入れ直す
                await self._apply_mode_flags()
                await self._disable_crossfade()
            if "mixer" in subsystems:
                await self._reconcile_external_volume()
            if "player" in subsystems:
                await self._on_song_changed()

            # フェード中の mixer イベントは自分の setvol が生んだもの。UI に出る
            # 音量（user_volume）は動いていないので、1 段ごとに状態を配信しても
            # 無駄に status を引いて WebSocket を流すだけになる。
            notify = subsystems & {"player", "options", "playlist", "connection"}
            if "mixer" in subsystems and not self._fading:
                notify = True
            if notify:
                await self.broadcast_state()
        except MPDUnavailable:
            self.events.publish("player", {"connected": False})
        except Exception:
            log.exception("MPD イベントの処理でエラーが発生しました")

    async def _on_song_changed(self) -> None:
        status = await self.mpd.status()
        if await self._return_to_scope_top(status):
            return

        song_id = status.get("songid")
        if song_id == self._last_song_id:
            return
        self._last_song_id = song_id

        await self._apply_loudness(status, fade=SONG_CHANGE_FADE_SECONDS)
        if self.order_mode == "random":
            await self._top_up_random_queue(status)

    async def _return_to_scope_top(self, status: dict[str, Any]) -> bool:
        """範囲を鳴らし終えたら、その先頭で操作待ちにする（NW-ZX300 に倣う）。

        MPD はキューを使い切ると停止するだけなので、画面から曲もアルバムも
        消えてしまう。聴き終えたアルバムをもう一度鳴らすにも、ライブラリから
        探し直すことになる。先頭の曲を一時停止の状態で構えておけば、再生
        ボタンひとつで頭から聴き直せる。

        戻したときは True を返す（続きの処理は次のイベントに任せる）。
        """
        state = status.get("state")
        was_playing = self._last_play_state == "play"
        self._last_play_state = state

        if state != "stop":
            # 停止ボタンの印が次の停止まで残らないよう、鳴り始めたら降ろす
            self._user_stopped = False
            return False
        if not was_playing or self._returning_to_top:
            return False
        if self._user_stopped:
            self._user_stopped = False   # 停止ボタンによる停止はそのまま
            return False
        # リピート中は巻き戻るので鳴らし終わらない。ランダムは曲が足され続ける
        if self.repeat_mode != "off" or self.order_mode != "normal":
            return False
        if (_int_or_none(status.get("playlistlength")) or 0) == 0:
            return False

        self._returning_to_top = True
        try:
            # 先頭を「一時停止・0 秒」に構える。play したままだと鳴ってしまう
            # ので、いったん無音にしてから止める（ごく短くても音は出る）。
            self._cancel_fade()
            # 期待値も合わせて記録する。合わせないと、自分で下げた 0 を
            # 他クライアントによる変更と誤認して user_volume が 0 になる
            self._expected_volume = 0
            await self._send_volume(0)
            await self.mpd.play(0)
            await self.mpd.pause(True)
            await self.mpd.seek(0)
            self._paused_at = None
            self._paused_song_id = None
            self._last_song_id = (await self.mpd.status()).get("songid")
            # 止まっている間に、先頭の曲に合った音量へ戻しておく
            await self._apply_loudness(await self.mpd.status())
        except MPDUnavailable:
            log.warning("範囲の先頭へ戻せませんでした")
        finally:
            self._returning_to_top = False
        log.info("範囲を鳴らし終えたので先頭で待機します: %s", self.scope.label)
        return True

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
                await self._start_with_warmup(0)
            else:
                await self.mpd.add_many(t["path"] for t in tracks)
                index = 0
                if start_track_id is not None:
                    index = next(
                        (i for i, t in enumerate(tracks) if t["id"] == start_track_id), 0
                    )
                await self._apply_mode_flags()
                await self._start_with_warmup(index)

            self._persist_state()

        await self._on_song_changed()
        await self.broadcast_state()
        return self.state_snapshot()

    async def resync_scope(self, kind: str, scope_id: str | int) -> bool:
        """再生中の範囲の中身が変わったら、MPD のキューを合わせ直す。

        プレイリストを聴いている最中に曲を足したり消したり並べ替えたりしても、
        MPD のキューは読み込んだときのまま。画面に出ている一覧と実際に鳴る
        順番が食い違い、次の曲が消したはずの曲になる。

        いま鳴っている曲がまだ範囲に残っていれば、その曲は触らずに周りだけ
        入れ替える（曲を消しただけで音が途切れないように）。鳴っている曲
        自体が消された場合は作り直すしかないので、その位置から続ける。

        入れ替えたときだけ True を返す。
        """
        if self.scope.kind != kind or str(self.scope.id) != str(scope_id):
            return False   # 別の範囲を聴いているなら触らない

        async with self._lock:
            tracks = await asyncio.to_thread(self.repo.resolve_scope, kind, scope_id)
            paths = [t["path"] for t in tracks]
            # ランダム（重複あり）はキューに 2 曲しか持たない。抽選の元だけ替える
            self._random.pool = paths
            if self.order_mode == "random":
                return True

            queue = await self.mpd.queue()
            if [item.get("file") for item in queue] == paths:
                return False   # 中身が同じなら触らない（並び替え以外の変更など）

            status = await self.mpd.status()
            song = await self.mpd.current_song()
            current_path = song.get("file")
            current_id = status.get("songid")
            was_playing = status.get("state") == "play"

            if not paths:
                await self.mpd.clear()
                return True

            if current_path in paths and current_id is not None:
                await self._rebuild_around_current(queue, paths, current_path, current_id)
            else:
                # 鳴っている曲が範囲から消えた。作り直して、その位置から続ける
                position = _int_or_none(status.get("song")) or 0
                await self.mpd.clear()
                await self.mpd.add_many(paths)
                await self.mpd.play(min(position, len(paths) - 1))
                if not was_playing:
                    await self.mpd.pause(True)
            return True

    async def _rebuild_around_current(
        self, queue: list[dict[str, Any]], paths: list[str], current_path: str, current_id: str
    ) -> None:
        """再生中の曲だけ残して、その周りを新しい並びに入れ替える。

        MPD は再生中でない曲をキューから消しても鳴っている音に影響しない。
        消して足すだけなので、曲の変わり目を待たずに反映できる。
        """
        for item in queue:
            song_id = item.get("id")
            if song_id is not None and str(song_id) != str(current_id):
                await self.mpd.delete_id(int(song_id))

        # 残った 1 曲を挟むように、前から順に入れていく
        keep = paths.index(current_path)
        for index, path in enumerate(paths):
            if index == keep:
                continue
            await self.mpd.add_id(path, position=index)

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
            await self._start_with_warmup(min(start_index, len(paths) - 1))
            self._persist_state()
        await self.broadcast_state()

    # ============================================================ 基本操作

    async def toggle(self) -> None:
        status = await self.mpd.status()
        state = status.get("state")
        if state == "play":
            await self.pause()
            return
        await self._prepare_output()
        if state == "pause":
            await self._resume()
        else:
            await self.mpd.play()

    async def play(self) -> None:
        await self._prepare_output()
        status = await self.mpd.status()
        if status.get("state") == "pause":
            await self._resume()
        else:
            await self.mpd.play()

    async def pause(self) -> None:
        # 再開時に同じ位置へ戻せるよう、止めた位置を控えておく
        status = await self.mpd.status()
        self._paused_at = _float_or_none(status.get("elapsed"))
        self._paused_song_id = status.get("songid")
        await self.mpd.pause(True)

    async def _resume(self) -> None:
        """一時停止から再開する。止めた位置とのズレを補正する。

        MPD は出力バッファに溜めた分を破棄して再開するため、止めた位置より
        数秒先から鳴り始めることがある（実測で 1〜3 秒）。バッファ量に依存する
        挙動なので、止めた位置を控えておいて再開後に戻す。
        """
        await self.mpd.pause(False)
        if self._paused_at is None:
            return
        status = await self.mpd.status()
        if _did_not_start(status):
            # 出力を開けず、一時停止へ戻った。ここでシークすると MPD はエラーを
            # 消してしまい、画面に理由が届かない（実機で確認）。止めた位置は
            # 次にうまく再開できたときに合わせる
            return
        if status.get("songid") != self._paused_song_id:
            self._paused_at = None
            return
        elapsed = _float_or_none(status.get("elapsed"))
        if elapsed is not None and abs(elapsed - self._paused_at) > RESUME_DRIFT_TOLERANCE:
            await self.mpd.seek(self._paused_at)
        self._paused_at = None

    async def stop(self) -> None:
        # 自分で止めたのか、キューを使い切って止まったのかを見分ける印
        self._user_stopped = True
        await self.mpd.stop()

    async def next(self) -> None:
        """次の曲へ進む。終端では何もしない。

        リピートもシャッフルも設定していない状態で最後の曲から進めると、MPD は
        キューを使い切って再生を止める。画面は「再生していません」になり、
        聴いていたアルバムへ戻る手がかりまで消えてしまう。アートワークの
        スワイプは指が滑っただけでも発生するので、終端では受け付けない。
        """
        status = await self.mpd.status()
        if await self.at_queue_end(status):
            return
        # 次に鳴る曲は MPD が決めている（シャッフルでも nextsong で分かる）。
        # 曲送りは出力バッファを捨てるので、送る前に音量を合わせておく
        await self._preset_loudness(_int_or_none(status.get("nextsong")))
        # ランダム（重複あり）ではキューに次の 1 曲が入っているので、通常の
        # next で進める。進んだ後のイベントで補充される。
        await self.mpd.next()

    async def at_queue_end(self, status: dict[str, Any] | None = None) -> bool:
        """次に進む先が無い状態か（曲送りを受け付けない条件）。

        リピート中なら巻き戻るので終端ではなく、ランダム（重複あり）なら
        次の曲が足されるので尽きない。シャッフルは MPD の random に任せて
        いるため、ここでは終端と見なさない。

        ``status`` を渡せば MPD への問い合わせを省ける（状態の配信は頻繁に
        起きるので、そのたびに status を引き直さない）。
        """
        if self.repeat_mode != "off" or self.order_mode != "normal":
            return False
        if status is None:
            status = await self.mpd.status()
        if status.get("state") == "stop":
            return False    # 止まっているなら再生開始を邪魔しない
        position = _int_or_none(status.get("song"))
        length = _int_or_none(status.get("playlistlength"))
        if position is None or length is None:
            return False
        return position >= length - 1

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
        if self.order_mode != "shuffle":
            # シャッフルでは MPD がどの曲へ戻すか分からないので先回りしない。
            # 曲戻しも出力バッファを捨てるので、送る前に音量を合わせておく
            position = _int_or_none(status.get("song"))
            if position is not None:
                target = position - 1
                if target < 0:
                    # 先頭からの曲戻しが末尾へ回るのは範囲リピート中だけ
                    length = _int_or_none(status.get("playlistlength")) or 0
                    target = length - 1 if self.repeat_mode == "range" else -1
                await self._preset_loudness(target)
        await self.mpd.previous()

    async def seek(self, seconds: float) -> None:
        # 手動シーク後は、一時停止位置の記録が古くなるので捨てる
        self._paused_at = None
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

    async def _disable_crossfade(self) -> None:
        """MPD 側のクロスフェードを必ず切る。

        kotone はクロスフェードを提供しない（:mod:`kotone.settings` の
        冒頭に理由を書いてある）。MPD は state_file に crossfade を残すので、
        以前の設定や他のクライアントの操作が生き残らないようにする。
        """
        await self.mpd.set_crossfade(0)

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
        """スコープの全曲をキューへ戻す（通常／シャッフルへの復帰）。

        キューを作り直すため再生が止まる。再生位置を控えておき、
        同じ曲の同じ位置から続けられるようにする（モードを一巡させても
        曲の先頭に巻き戻らないようにするため）。
        """
        tracks = await asyncio.to_thread(
            self.repo.resolve_scope, self.scope.kind, self.scope.id
        )
        if not tracks:
            return

        status = await self.mpd.status()
        current = await self.mpd.current_song()
        current_path = current.get("file")
        elapsed = float(status.get("elapsed") or 0)
        was_playing = status.get("state") == "play"

        await self.mpd.clear()
        await self.mpd.add_many(t["path"] for t in tracks)
        if not current_path:
            return

        index = next((i for i, t in enumerate(tracks) if t["path"] == current_path), None)
        if index is None:
            return

        await self.mpd.play(index)
        if elapsed > 1.0:
            await self.mpd.seek(elapsed)
        if not was_playing:
            await self.mpd.pause(True)

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

    # ==================================================== 再生開始の欠落対策

    async def _start_with_warmup(self, position: int) -> None:
        """曲頭が欠けないように再生を開始する（要件 3.1）。

        MPD が停止状態から再生を始めると ALSA デバイスを開き直すため、
        USB DAC がサンプリングレートを補足し終えるまでの数百 ms は音が出ない。
        プリギャップの短い曲では、この間に曲の先頭が失われてしまう。

        そこで「無音のまま先行再生してデバイスと DAC を起こし、
        ロックが済んでから曲頭へ戻す」ことで、先頭を必ず鳴らす。
        既にデバイスが開いている（再生中／一時停止中）場合は不要なので行わない。

        Bluetooth では行わない。A2DP にサンプリングレートを追従させる必要は
        無く、逆に曲頭へ戻すシークがストリームの取り直し（bluealsa の
        "PCM drop"）を招いて、鳴り始めの数秒が途切れる。実機のログで、
        再生開始の 1〜2 秒後に必ず PCM drop が出ていることを確認した。
        """
        await self._prepare_output()
        # 出力バッファは play の直後に一気に埋まる。埋まる前に音量を入れる
        await self._preset_loudness(position)

        warmup_ms = int(self.settings.get("startup_warmup_ms") or 0)
        if warmup_ms <= 0 or await self._bluetooth_output():
            await self.mpd.play(position)
            return

        status = await self.mpd.status()
        if status.get("state") == "play":
            # デバイスは既に開いている。曲間は MPD の gapless に任せる
            await self.mpd.play(position)
            return

        try:
            await self.mpd.set_volume(0)          # ウォームアップ中は無音にする
            self._expected_volume = 0
            await self.mpd.play(position)
            await asyncio.sleep(warmup_ms / 1000)
            # デバイスが開いた状態で曲頭へ戻す。開けなかったときは戻さない
            # （シークすると MPD がエラーを消し、画面に理由が届かなくなる）
            if not _did_not_start(await self.mpd.status()):
                await self.mpd.seek(0)
        finally:
            # 音量は「元の値」ではなく、いま鳴っている曲に対する実効音量を
            # 計算し直して設定する。ウォームアップ中の sleep で idle イベントが
            # 走り、_apply_loudness が既に正しい値を入れていることがあるため、
            # 元の値へ戻すとそれを上書きしてしまう。
            await self._apply_loudness(await self.mpd.status())

    # ============================================================ 音量

    @property
    def external_volume(self) -> bool:
        """音量をアンプ側で操作する構成か（UI のスライダーを出さない）。"""
        return self.settings.get("volume_control") == "external"

    def _base_volume(self) -> int:
        """ラウドネス補正をかける前の基準音量。

        外部アンプで音量を取る構成では MPD 側を最大に保ち、補正による
        減衰だけを効かせる（デジタル減衰を最小限にするため）。
        """
        return 100 if self.external_volume else self.user_volume

    async def set_volume(self, volume: int) -> None:
        self.user_volume = max(0, min(100, int(volume)))
        self._persist_state()
        # スライダーを大きく動かしたときの段差を消す。追従が遅れない程度に短く
        await self._apply_loudness(
            await self.mpd.status(), fade=USER_VOLUME_FADE_SECONDS
        )
        await self.broadcast_state()

    async def refresh_volume(self) -> None:
        """音量に関わる設定の変更を、いま鳴っている音量へすぐ反映する。

        音量の操作方法（スライダーの有効・無効）やラウドネス補正を変えても、
        これまでは次の曲へ移るまで MPD の音量が変わらなかった。スライダーで
        下げたまま無効にすると、下げた音量のまま残っていた。
        """
        await self._apply_loudness(await self.mpd.status(), fade=USER_VOLUME_FADE_SECONDS)

    async def _reconcile_external_volume(self) -> None:
        """他クライアント等が音量を変えた場合に、その値を新しいユーザー基準にする。

        ここでラウドネス係数で割り戻すと、直前に再生していたトラックのゲインに
        依存して user_volume が変動（例: ゲイン +3.95dB のトラック後に外部で 20 に
        すると 20÷1.58≈13 になる）してしまうため、割り戻さずに観測値をそのまま
        採用する。ラウドネス補正は次のトラック切り替え時に改めて適用される。
        """
        if self.external_volume:
            return  # 音量はアンプ側で取る構成なので追随しない
        if self._fading:
            # フェード中は自分の setvol が次々と mixer イベントを生む。観測値が
            # 1 つ前の段の値になることがあり、外部からの変更と見分けられない。
            return
        status = await self.mpd.status()
        try:
            observed = int(status.get("volume", -1))
        except (TypeError, ValueError):
            return
        if observed < 0 or self._expected_volume is None:
            return
        if observed == self._expected_volume:
            return  # 自分の setvol による mixer イベントは無視する
        self.user_volume = max(0, min(100, observed))
        self._expected_volume = observed
        self._persist_state()

    async def _apply_loudness(
        self, status: dict[str, Any], *, fade: float = 0.0
    ) -> None:
        """現在の曲に合わせて実効音量を設定する。

        ``fade`` を指定すると、その秒数をかけて今の音量から移す。曲の変わり目で
        一気に変えると段差がノイズになるため（:data:`SONG_CHANGE_FADE_SECONDS`）。
        """
        factor = await self._current_factor()
        effective = max(0, min(100, round(self._base_volume() * factor)))
        try:
            current = int(status.get("volume", -1))
        except (TypeError, ValueError):
            current = -1

        # フェードの途中なら、status の値より「最後に送った値」のほうが実際の
        # 音量に近い（status は 1 つ前の段のことがある）。そこから続けて動かす。
        if self._fading and self._last_sent_volume is not None:
            current = self._last_sent_volume
        self._cancel_fade()

        # 期待値は setvol より先に記録する。set_volume が発する mixer idle
        # イベントが _reconcile_external_volume に「外部変更」と誤認されて
        # 音量がドリフトするのを防ぐ（記録前にイベントが処理されるレース対策）。
        self._expected_volume = effective
        if current == effective:
            return
        if fade <= 0 or current < 0:
            await self._send_volume(effective)
            return
        self._fade_task = asyncio.create_task(self._fade_volume(current, effective, fade))

    async def _preset_loudness(self, position: int | None) -> None:
        """これから鳴らす曲の音量を、MPD が音を作り始める前に入れておく。

        再生開始・曲送り・曲戻しでは、MPD は出力バッファを捨てて新しい曲で
        埋め直す。埋め直しは一瞬で終わるので、鳴り始めてから setvol しても
        間に合わない。既に埋まった分は補正前の音量のまま出ていく。

        Raspberry Pi 4B + bluez-alsa の実測でバッファは約 1.1 秒あり、曲送りの
        直後に「1 秒ほど前の曲の音量で鳴ってから補正値へ滑る」形になる。
        USB DAC もバッファが浅いだけで同じことが起きている。

        曲の自然な変わり目（ギャップレス）ではバッファを捨てないので、この
        先回りは要らない。書き込み位置と再生位置が同じだけずれているため、
        変わり目に送った setvol はそのまま新しい曲の頭に当たる（実測で確認）。
        """
        if position is None or position < 0:
            return
        try:
            song = await self.mpd.song_at(position)
        except MPDUnavailable:
            return
        path = (song or {}).get("file")
        if not path:
            return
        factor = await self._factor_for_path(path)
        effective = max(0, min(100, round(self._base_volume() * factor)))
        if effective == self._expected_volume:
            return
        self._cancel_fade()
        self._expected_volume = effective
        await self._send_volume(effective)

    async def _send_volume(self, value: int) -> None:
        self._last_sent_volume = value
        await self.mpd.set_volume(value)

    def _cancel_fade(self) -> None:
        if self._fade_task is not None and not self._fade_task.done():
            self._fade_task.cancel()
        self._fade_task = None

    @property
    def _fading(self) -> bool:
        """音量フェードの実行中か（外部音量への追随を止める判断に使う）。"""
        return self._fade_task is not None and not self._fade_task.done()

    async def _fade_volume(self, start: int, target: int, duration: float) -> None:
        """音量を段階的に動かす。

        setvol はそのたびに mixer イベントを生む。フェード中は観測値が
        1 つ前の段の値であることがあり、外部からの変更と見分けられないため、
        :meth:`_reconcile_external_volume` 側で追随を止めている。
        """
        # 音量は整数なので、差の分より細かく刻んでも同じ値を送るだけになる。
        # 長いフェードでは刻みのほうを広げて、無駄な setvol を出さない。
        distance = abs(target - start)
        steps = max(1, min(round(duration / VOLUME_FADE_STEP_SECONDS), distance))
        interval = duration / steps
        try:
            for step in range(1, steps + 1):
                await self._send_volume(round(start + (target - start) * step / steps))
                if step < steps:
                    await asyncio.sleep(interval)
        except MPDUnavailable:
            log.debug("音量フェード中に MPD との接続が切れました")

    async def _current_factor(self) -> float:
        """現在の曲に適用すべき音量倍率。補正が無効なら 1.0。"""
        if not self.settings.get("loudness_enabled"):
            return 1.0
        song = await self.mpd.current_song()
        path = song.get("file")
        if not path:
            return 1.0
        return await self._factor_for_path(path)

    async def _factor_for_path(self, path: str) -> float:
        """指定した曲に適用すべき音量倍率。補正が無効なら 1.0。"""
        if not self.settings.get("loudness_enabled"):
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
                "volume_control": self.settings.get("volume_control"),
                "state": "stop",
                "can_next": False,
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
            "volume_control": self.settings.get("volume_control"),
            "effective_volume": _int_or_none(status.get("volume")),
            "repeat": self.repeat_mode,
            "order": self.order_mode,
            "scope": self.scope.as_dict(),
            "queue_length": _int_or_none(status.get("playlistlength")) or 0,
            "queue_position": _int_or_none(status.get("song")),
            # 終端では曲送りを受け付けない。押しても効かない理由が分かるよう、
            # 画面側でボタンを無効に見せるために返す
            "can_next": not await self.at_queue_end(status),
            "audio": _parse_audio_format(status.get("audio")),
            "bitrate": _int_or_none(status.get("bitrate")),
            "track": track,
            "raw_file": song.get("file"),
            # 出力を開けなかったときなど。MPD は黙って一時停止するだけなので、
            # 理由を画面に出すために返す
            "error": describe_mpd_error(status.get("error")),
            **await self._output_state(),
        }
        self._cached_state = state
        return state

    async def _prepare_output(self) -> None:
        """鳴らし始める前に出力を整える。

        * 前回の失敗（status の error）を消す。残っていると、今回うまく
          鳴っても画面に古いエラーが出続ける。
        * Bluetooth なら、送る形式をいまの接続のサンプリングレートに固定する。
        """
        await self.mpd.clear_error()
        await self._match_bluetooth_stream()

    async def _match_bluetooth_stream(self) -> None:
        """Bluetooth へ送る形式を、いまの接続のサンプリングレートに合わせる。

        bluez-alsa の ALSA プラグインは、再生側が求めるサンプリングレートが
        接続中のものと違うと、コーデックを設定し直す（A2DP の再構成）。
        NW-ZX300 の Bluetooth 受信はこの再構成を受け付けず（AVDTP の OPEN を
        Bad State で拒否する）、両方の画面に「LDAC で接続中」と出たまま、
        音を送れなくなる。ZX300 とは LDAC 48 kHz で繋がるので、44.1 kHz の
        曲を鳴らすたびにこれが起きていた（2026-09-23、実機で再現）。

        接続を曲に合わせるのをやめ、MPD の出力を接続に合わせる（変換は MPD
        が行う）。接続のレートは機器ごと・接続ごとに違いうるので、鳴らし
        始めるたびに読み直す。
        """
        if self.outputs is None or self.bluetooth is None:
            return
        try:
            active = await self.outputs.active()
            if not active or not active.get("bluetooth"):
                return
            info = await self.bluetooth.codec_info(fresh=True)
            rate = (info or {}).get("samplerate")
            if not rate:
                return   # 繋がっていない。開けなかったことは MPD の error で知らせる
            channels = (info or {}).get("channels") or 2
            await self.outputs.pin_format(int(active["id"]), f"{rate}:*:{channels}")
        except MPDUnavailable:
            raise
        except Exception:
            # 合わせられなくても再生は試す（接続とレートが一致していれば鳴る）
            log.warning("Bluetooth へ送る形式を合わせられませんでした", exc_info=True)

    async def _bluetooth_output(self) -> bool:
        """いま音を出している先が Bluetooth か。"""
        if self.outputs is None:
            return False
        try:
            active = await self.outputs.active()
        except Exception:
            return False
        return bool(active and active.get("bluetooth"))

    async def _output_state(self) -> dict[str, Any]:
        """いまどこへ音を出しているか。Bluetooth ならコーデックも添える。

        再生中画面に「SBC で接続中」のように出すために使う。コーデックの
        読み出しは数秒キャッシュされるので、状態を配信するたびに D-Bus を
        叩くことにはならない。
        """
        if self.outputs is None:
            return {"output": None, "bluetooth_codec": None}
        try:
            active = await self.outputs.active()
        except Exception:
            log.debug("出力先を取得できませんでした", exc_info=True)
            return {"output": None, "bluetooth_codec": None}

        codec = None
        if active and active.get("bluetooth") and self.bluetooth is not None:
            info = await self.bluetooth.codec_info()
            codec = (info or {}).get("codec")
        return {"output": active, "bluetooth_codec": codec}

    def state_snapshot(self) -> dict[str, Any]:
        return dict(self._cached_state)

    async def broadcast_state(self) -> None:
        state = await self.build_state()
        self.events.publish("player", state)


_OPEN_FAILED = re.compile(r'Failed to open "([^"]+)"')


def _did_not_start(status: dict[str, Any]) -> bool:
    """鳴らし始めようとして、始まらなかったか（出力を開けなかったなど）。"""
    return bool(status.get("error")) or status.get("state") != "play"


def describe_mpd_error(error: str | None) -> str | None:
    """MPD の status の error を、画面に出せる文にする。

    MPD は出力を開けないと、エラーを status に残して一時停止するだけで、
    画面からは「押しても再生が始まらない」としか見えない。
    """
    if not error:
        return None
    opened = _OPEN_FAILED.search(error)
    if opened and opened.group(1) == BLUETOOTH_OUTPUT_NAME:
        return ("Bluetooth 機器へ音を送れませんでした。"
                "機器との接続を切って、つなぎ直してください。")
    if opened:
        return f"「{opened.group(1)}」を開けませんでした。機器の電源と接続を確認してください。"
    return f"再生できませんでした（{error}）"


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
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
