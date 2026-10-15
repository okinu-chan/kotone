"""Bluetooth 機器の管理（BlueZ を D-Bus 経由で操作する）。

Pi から BT イヤホン・ヘッドホン・スピーカーへ音を送るための機能。
``bluetoothctl`` を叩く方法もあるが、対話前提の作りで出力の解析も不安定
だったため（エージェント登録の取りこぼしを実機で確認）、BlueZ の D-Bus API
を直接使う。

音の経路
--------
``MPD → ALSA(bluealsa) → BlueZ → BT 機器`` の順に流れる。bluealsa の
``bluealsa`` PCM は「最後に接続した BT 機器」を指すので、機器を替えても
MPD の設定は書き換えなくてよい。出力の切り替えは MPD の出力の有効・無効
（:mod:`kotone.outputs`）で行う。

音質について
------------
A2DP は必ず再圧縮が入る（可逆ではない）。使えるコーデックは bluealsa の
起動オプションと相手機器の対応の重なりで決まる。install.sh は SBC / aptX /
aptX HD / LDAC を有効にする（AAC は有効にしていない）。サンプリングレートは
接続時に機器どうしが決め、MPD の出力をそのレートへ合わせて変換する
（:meth:`kotone.playback.PlaybackController._match_bluetooth_stream`）。
そのため USB DAC のようなビットパーフェクト再生にはならない。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

BLUEZ = "org.bluez"
BLUEALSA = "org.bluealsa"
BLUEALSA_PCM_IFACE = "org.bluealsa.PCM1"
ADAPTER_IFACE = "org.bluez.Adapter1"
DEVICE_IFACE = "org.bluez.Device1"
PROPS_IFACE = "org.freedesktop.DBus.Properties"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"

#: 探索を回す既定の秒数。長すぎると接続中の音が途切れやすい
DEFAULT_SCAN_SECONDS = 12.0

#: 音を出せる機器だけを出すためのサービス UUID。
#: A2DP Sink（イヤホン・スピーカー側）を持つ機器が対象。
A2DP_SINK_UUID = "0000110b-0000-1000-8000-00805f9b34fb"

#: 機器の種別（Class of Device）の大分類「Audio/Video」。
#:
#: 探索の応答にサービス UUID を載せない機器がある（Technics EAH-AZ100 など。
#: UUID はペアリング後の問い合わせで初めて分かる）。そうした機器でも種別は
#: 載せているので、UUID が無くても Audio/Video なら音を出せる機器とみなす。
COD_MAJOR_AUDIO_VIDEO = 0x04

#: 音質の優先順（前ほど優先）。
#:
#: 実際に使えるのは「Pi 側が対応しているもの」と「機器が提示したもの」の
#: 重なりだけ。ここに無い名前は順位付けの対象外で、機器任せになる。
CODEC_PRIORITY: tuple[str, ...] = ("LDAC", "aptX HD", "aptX", "AAC", "SBC")

#: 接続中のコーデックを読み直す間隔（秒）。
#: 再生状態は毎秒のように配信されるので、その都度 D-Bus を叩かない。
CODEC_CACHE_SECONDS = 5.0


class BluetoothError(RuntimeError):
    """利用者に見せる想定のエラー。"""


@dataclass
class BluetoothDevice:
    address: str
    name: str
    paired: bool
    bonded: bool
    trusted: bool
    connected: bool
    icon: str | None
    rssi: int | None
    #: 音を受け取れる機器か（A2DP Sink を持つか、種別が Audio/Video）
    audio: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "name": self.name,
            "paired": self.paired,
            "bonded": self.bonded,
            "trusted": self.trusted,
            "connected": self.connected,
            "icon": self.icon,
            "rssi": self.rssi,
            "audio": self.audio,
        }


class BluetoothManager:
    """BlueZ の薄いラッパー。

    D-Bus の接続は使うたびに張り直す。常時つないでおくと、bluetooth
    サービスの再起動でハンドルが腐ったまま気づけない。
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._scanning = False
        #: 直近に読んだコーデック情報と、その時刻（毎回 D-Bus を叩かないため）
        self._codec_cache: tuple[float, dict[str, Any] | None] = (0.0, None)

    # ------------------------------------------------------------ 基本

    @property
    def scanning(self) -> bool:
        return self._scanning

    async def available(self) -> bool:
        """BlueZ が動いていて、アダプタがあるか。"""
        try:
            async with _bus() as bus:
                return await _find_adapter(bus) is not None
        except Exception:
            return False

    async def adapter_info(self) -> dict[str, Any]:
        async with _bus() as bus:
            path = await _find_adapter(bus)
            if path is None:
                return {"available": False, "powered": False, "name": None}
            props = await _get_all(bus, path, ADAPTER_IFACE)
            # D-Bus の値は Variant で包まれている。剥がさないと JSON にできない
            return {
                "available": True,
                "powered": bool(_value(props.get("Powered"), False)),
                "name": _value(props.get("Alias")) or _value(props.get("Name")),
                "address": _value(props.get("Address")),
                "discovering": bool(_value(props.get("Discovering"), False)),
            }

    async def set_powered(self, powered: bool) -> None:
        async with _bus() as bus:
            path = await _require_adapter(bus)
            await _set(bus, path, ADAPTER_IFACE, "Powered", "b", powered)

    # ------------------------------------------------------------ 一覧

    async def devices(self, *, audio_only: bool = True) -> list[BluetoothDevice]:
        """既知の機器を返す。接続中・ペアリング済みを先に並べる。"""
        async with _bus() as bus:
            found = []
            for _path, props in await _devices(bus):
                device = _to_device(props)
                if audio_only and not device.audio and not device.paired:
                    continue   # 音を出せない周辺機器は隠す（時計・タグなど）
                found.append(device)

        found.sort(
            key=lambda d: (
                not d.connected,
                not d.paired,
                -(d.rssi if d.rssi is not None else -999),
                d.name.lower(),
            )
        )
        return found

    # ------------------------------------------------------------ 探索

    async def scan(self, seconds: float = DEFAULT_SCAN_SECONDS) -> list[BluetoothDevice]:
        """近くの機器を探す。終わったら一覧を返す。

        探索中は電波を使うので音が途切れやすい。既定の十数秒で打ち切る。
        """
        async with self._lock:
            self._scanning = True
            try:
                async with _bus() as bus:
                    path = await _require_adapter(bus)
                    await _call(bus, path, ADAPTER_IFACE, "StartDiscovery")
                    try:
                        await asyncio.sleep(max(1.0, min(seconds, 60.0)))
                    finally:
                        try:
                            await _call(bus, path, ADAPTER_IFACE, "StopDiscovery")
                        except Exception:
                            log.debug("探索の停止に失敗しました", exc_info=True)
            finally:
                self._scanning = False
        return await self.devices()

    # ------------------------------------------------------ ペアリング・接続

    async def pair(self, address: str) -> BluetoothDevice:
        """ペアリングして信頼済みにし、接続する。

        イヤホンの多くは暗証番号の入力が要らない（Just Works）。番号の確認を
        求める機器には対応していない。
        """
        async with _bus() as bus:
            path = await _device_path(bus, address)
            props = await _get_all(bus, path, DEVICE_IFACE)
            if not _value(props.get("Paired"), False):
                try:
                    await _call(bus, path, DEVICE_IFACE, "Pair", timeout=60.0)
                except Exception as exc:
                    raise BluetoothError(_pair_hint(exc)) from exc
            # 信頼済みにしておくと、次からは自動でつながる
            await _set(bus, path, DEVICE_IFACE, "Trusted", "b", True)

        await self.connect(address)
        return await self.device(address)

    async def connect(self, address: str) -> BluetoothDevice:
        async with _bus() as bus:
            path = await _device_path(bus, address)
            try:
                await _call(bus, path, DEVICE_IFACE, "Connect", timeout=40.0)
            except Exception as exc:
                raise BluetoothError(
                    f"接続できませんでした。機器の電源が入っているか確認してください（{exc}）"
                ) from exc
        return await self.device(address)

    async def disconnect(self, address: str) -> BluetoothDevice:
        async with _bus() as bus:
            path = await _device_path(bus, address)
            await _call(bus, path, DEVICE_IFACE, "Disconnect", timeout=20.0)
        return await self.device(address)

    async def forget(self, address: str) -> None:
        """ペアリングを解除する（機器側でも解除が必要なことがある）。"""
        async with _bus() as bus:
            adapter = await _require_adapter(bus)
            path = await _device_path(bus, address)
            await _call(bus, adapter, ADAPTER_IFACE, "RemoveDevice", "o", path)

    async def device(self, address: str) -> BluetoothDevice:
        async with _bus() as bus:
            path = await _device_path(bus, address)
            return _to_device(await _get_all(bus, path, DEVICE_IFACE))

    # ------------------------------------------------------------ コーデック

    async def codec_info(self, *, fresh: bool = False) -> dict[str, Any] | None:
        """いま接続中の機器で使われているコーデック。

        接続していなければ None。値は数秒キャッシュする（再生状態の配信の
        たびに D-Bus を叩かないため）。
        """
        now = asyncio.get_running_loop().time()
        cached_at, cached = self._codec_cache
        if not fresh and now - cached_at < CODEC_CACHE_SECONDS:
            return cached

        info = await self._read_codec()
        self._codec_cache = (now, info)
        return info

    async def _read_codec(self) -> dict[str, Any] | None:
        try:
            async with _bus() as bus:
                path = await _pcm_path(bus)
                if path is None:
                    return None
                props = await _get_all(bus, path, BLUEALSA_PCM_IFACE, service=BLUEALSA)
                available = await _available_codecs(bus, path)
                return {
                    "codec": str(_value(props.get("Codec"), "")) or None,
                    "available": available,
                    "samplerate": int(_value(props.get("Sampling"), 0)) or None,
                    "channels": int(_value(props.get("Channels"), 0)) or None,
                }
        except Exception:
            log.debug("コーデックを読めませんでした", exc_info=True)
            return None

    async def select_best_codec(self) -> str | None:
        """使えるうちで一番音質のよいコーデックへ切り替える。

        切り替えるのは、いま使っているものより上位が実際に使える場合だけ。
        使えるコーデックは相手機器が提示したものとの重なりで決まるので、
        機器が LDAC を出していなければ何もしない。
        """
        info = await self.codec_info(fresh=True)
        if not info:
            return None
        available = info.get("available") or []
        current = info.get("codec")
        best = next((c for c in CODEC_PRIORITY if c in available), None)
        if best is None or best == current:
            return current
        try:
            async with _bus() as bus:
                path = await _pcm_path(bus)
                if path is None:
                    return current
                pcm = await _interface(bus, path, BLUEALSA_PCM_IFACE, service=BLUEALSA)
                await asyncio.wait_for(pcm.call_select_codec(best, {}), timeout=20.0)
        except Exception as exc:
            log.warning("コーデックを %s へ切り替えられませんでした: %s", best, exc)
            return current
        log.info("Bluetooth のコーデックを %s へ切り替えました", best)
        self._codec_cache = (0.0, None)
        return best

    async def connected_device(self) -> BluetoothDevice | None:
        """いま音を出せる状態でつながっている機器（無ければ None）。"""
        for device in await self.devices():
            if device.connected and device.audio:
                return device
        return None


# ---------------------------------------------------------------- D-Bus 補助


class _bus:
    """システムバスへの接続を使い捨てで開く。"""

    async def __aenter__(self):
        from dbus_fast import BusType  # noqa: PLC0415
        from dbus_fast.aio import MessageBus  # noqa: PLC0415

        try:
            self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        except Exception as exc:
            raise BluetoothError(f"Bluetooth に接続できません: {exc}") from exc
        return self._bus

    async def __aexit__(self, *_exc: object) -> None:
        self._bus.disconnect()


async def _managed_objects(bus: Any) -> dict[str, dict[str, dict[str, Any]]]:
    introspection = await bus.introspect(BLUEZ, "/")
    proxy = bus.get_proxy_object(BLUEZ, "/", introspection)
    manager = proxy.get_interface(OBJECT_MANAGER)
    return await manager.call_get_managed_objects()


async def _find_adapter(bus: Any) -> str | None:
    for path, interfaces in (await _managed_objects(bus)).items():
        if ADAPTER_IFACE in interfaces:
            return path
    return None


async def _require_adapter(bus: Any) -> str:
    path = await _find_adapter(bus)
    if path is None:
        raise BluetoothError("Bluetooth アダプタが見つかりません")
    return path


async def _devices(bus: Any) -> list[tuple[str, dict[str, Any]]]:
    return [
        (path, interfaces[DEVICE_IFACE])
        for path, interfaces in (await _managed_objects(bus)).items()
        if DEVICE_IFACE in interfaces
    ]


async def _device_path(bus: Any, address: str) -> str:
    wanted = address.upper()
    for path, props in await _devices(bus):
        if str(_value(props.get("Address"), "")).upper() == wanted:
            return path
    raise BluetoothError(f"機器が見つかりません: {address}")


async def _interface(bus: Any, path: str, iface: str, *, service: str = BLUEZ) -> Any:
    introspection = await bus.introspect(service, path)
    proxy = bus.get_proxy_object(service, path, introspection)
    return proxy.get_interface(iface)


async def _get_all(bus: Any, path: str, iface: str, *, service: str = BLUEZ) -> dict[str, Any]:
    props = await _interface(bus, path, PROPS_IFACE, service=service)
    return await props.call_get_all(iface)


async def _pcm_path(bus: Any) -> str | None:
    """bluealsa が公開している、送り出し側の PCM のパス。

    接続中の機器が無ければ None。複数つながっている場合は最初の 1 つ。

    bluealsa の Manager1 には一覧を返すメソッドが無い（プロパティだけ）ので、
    標準の ObjectManager から拾う。BlueZ と違い、ObjectManager が居るのは
    ルートではなく ``/org/bluealsa``。
    """
    root = "/org/bluealsa"
    introspection = await bus.introspect(BLUEALSA, root)
    proxy = bus.get_proxy_object(BLUEALSA, root, introspection)
    manager = proxy.get_interface(OBJECT_MANAGER)
    for path, interfaces in (await manager.call_get_managed_objects()).items():
        if BLUEALSA_PCM_IFACE in interfaces and "a2dpsrc" in path:
            return path
    return None


async def _available_codecs(bus: Any, path: str) -> list[str]:
    """相手機器と共通で使えるコーデックの名前。"""
    pcm = await _interface(bus, path, BLUEALSA_PCM_IFACE, service=BLUEALSA)
    try:
        return list((await pcm.call_get_codecs()).keys())
    except Exception:
        log.debug("使えるコーデックの一覧を取れませんでした", exc_info=True)
        return []


async def _set(bus: Any, path: str, iface: str, name: str, signature: str, value: Any) -> None:
    from dbus_fast import Variant  # noqa: PLC0415

    props = await _interface(bus, path, PROPS_IFACE)
    await props.call_set(iface, name, Variant(signature, value))


async def _call(
    bus: Any, path: str, iface: str, method: str,
    signature: str | None = None, value: Any = None, *, timeout: float = 30.0,
) -> Any:
    target = await _interface(bus, path, iface)
    name = "call_" + _snake(method)
    func = getattr(target, name)
    coro = func(value) if signature else func()
    return await asyncio.wait_for(coro, timeout=timeout)


def _snake(name: str) -> str:
    out = []
    for index, char in enumerate(name):
        if char.isupper() and index:
            out.append("_")
        out.append(char.lower())
    return "".join(out)


def _value(raw: Any, default: Any = None) -> Any:
    """dbus_fast の Variant から中身を取り出す。"""
    return getattr(raw, "value", raw) if raw is not None else default


def _to_device(props: dict[str, Any]) -> BluetoothDevice:
    address = str(_value(props.get("Address"), ""))
    uuids = [str(u).lower() for u in _value(props.get("UUIDs"), []) or []]
    return BluetoothDevice(
        address=address,
        name=str(_value(props.get("Alias")) or _value(props.get("Name")) or address),
        paired=bool(_value(props.get("Paired"), False)),
        bonded=bool(_value(props.get("Bonded"), False)),
        trusted=bool(_value(props.get("Trusted"), False)),
        connected=bool(_value(props.get("Connected"), False)),
        icon=_value(props.get("Icon")),
        rssi=_value(props.get("RSSI")),
        audio=A2DP_SINK_UUID in uuids or _is_audio_video_class(_value(props.get("Class"))),
    )


def _is_audio_video_class(cod: Any) -> bool:
    """機器の種別（Class of Device）の大分類が Audio/Video か。

    大分類は 8〜12 ビット目。種別を載せない機器（BLE だけの機器など）は False。
    """
    try:
        return (int(cod) >> 8) & 0x1F == COD_MAJOR_AUDIO_VIDEO
    except (TypeError, ValueError):
        return False


def _pair_hint(exc: Exception) -> str:
    """ペアリングの失敗を、次に何をすればよいかが分かる文言にする。"""
    text = str(exc)
    if "AuthenticationCanceled" in text or "AuthenticationFailed" in text:
        return (
            "ペアリングを断られました。機器をペアリングモードにしてから、"
            "もう一度お試しください"
        )
    if "AlreadyExists" in text:
        return "すでにペアリング済みです"
    if "TimeoutError" in text or isinstance(exc, TimeoutError):
        return (
            "ペアリングが時間内に終わりませんでした。機器をペアリングモードに"
            "してから、もう一度お試しください"
        )
    return f"ペアリングできませんでした（{text}）"
