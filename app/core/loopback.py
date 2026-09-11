"""系统声音录制：WASAPI 回环（loopback）采集，基于 comtypes + pycaw 接口定义。

原理：激活默认渲染终端的 IAudioClient（AUDCLNT_STREAMFLAGS_LOOPBACK），读取
系统混音输出（含其它应用正在播放的声音），转 16bit PCM 写入 WAV。
- 无渲染设备 / 初始化失败 → 抛 AudioLoopbackError（调用方降级为无声音轨）；
- 全程静音时写入全零帧，stop() 返回的 peak≈0 表示捕获期内无有效声音。

用法：
    rec = LoopbackRecorder()
    rec.start("out.wav")
    ...
    print(rec.stop())   # {ok, seconds, peak, frames}
"""

import ctypes
import struct
import threading
import wave
from ctypes import c_byte

from comtypes import (
    CLSCTX_ALL, COMMETHOD, GUID, HRESULT, IUnknown, POINTER,
    CoCreateInstance, cast, c_ulong, c_ulonglong, c_void_p,
)
from pycaw.api.audioclient import IAudioClient, WAVEFORMATEX
from pycaw.api.mmdeviceapi import IMMDeviceEnumerator

_CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
_IID_IAudioCaptureClient = GUID("{C8ADBD64-E71E-48A0-A4DE-185C395CD317}")

_AUDCLNT_SHAREMODE_SHARED = 0
_AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
_AUDCLNT_BUFFERFLAGS_SILENT = 0x1
_eRender = 0
_eConsole = 0

_KSDATAFORMAT_SUBTYPE_FLOAT = 3

AUDIO_HINTS = "请确认系统存在可用的播放设备且未静音"


class AudioLoopbackError(RuntimeError):
    """系统声音采集不可用（无设备 / 初始化失败）。"""


class _IAudioCaptureClient(IUnknown):
    _iid_ = _IID_IAudioCaptureClient
    _methods_ = (
        # HRESULT GetBuffer(BYTE **ppData, UINT32 *pNumFramesToRead,
        #                   DWORD *pdwFlags, UINT64 *pu64DevicePosition,
        #                   UINT64 *pu64QPCPosition);
        COMMETHOD([], HRESULT, "GetBuffer",
                  (["out"], POINTER(c_void_p), "ppData"),
                  (["out"], POINTER(c_ulong), "pNumFramesToRead"),
                  (["out"], POINTER(c_ulong), "pdwFlags"),
                  (["out"], POINTER(c_ulonglong), "pu64DevicePosition"),
                  (["out"], POINTER(c_ulonglong), "pu64QPCPosition")),
        # HRESULT ReleaseBuffer(UINT32 NumFramesRead);
        COMMETHOD([], HRESULT, "ReleaseBuffer",
                  (["in"], c_ulong, "NumFramesRead")),
        # HRESULT GetNextPacketSize(UINT32 *pNumFramesInNextPacket);
        COMMETHOD([], HRESULT, "GetNextPacketSize",
                  (["out"], POINTER(c_ulong), "pNumFramesInNextPacket")),
    )


def _open_loopback():
    """返回 (client, capture, fmt)。失败抛 AudioLoopbackError。"""
    enum = CoCreateInstance(_CLSID_MMDeviceEnumerator,
                            interface=IMMDeviceEnumerator)
    dev = enum.GetDefaultAudioEndpoint(_eRender, _eConsole)
    if dev is None:
        raise AudioLoopbackError(
            "未找到默认播放设备。%s。" % AUDIO_HINTS)
    unk = dev.Activate(IAudioClient._iid_, CLSCTX_ALL, None)
    client = cast(unk, POINTER(IAudioClient))
    fmt_ptr = client.GetMixFormat()
    wfx = fmt_ptr.contents
    if wfx.nChannels <= 0 or wfx.nSamplesPerSec <= 0:
        raise AudioLoopbackError("播放设备音频格式异常")
    sub = wfx.wFormatTag
    if sub == 0xFFFE:
        # WAVEFORMATEXTENSIBLE：SubFormat GUID 位于 WAVEFORMATEX 之后
        # (wValidBitsPerSample 2 + dwChannelMask 4 = 偏移 24)
        raw = ctypes.cast(fmt_ptr, POINTER(c_byte))
        sub = struct.unpack("<I", ctypes.string_at(
            ctypes.addressof(raw.contents), 28)[24:28])[0]
    fmt = {"channels": int(wfx.nChannels),
           "rate": int(wfx.nSamplesPerSec),
           "bits": int(wfx.wBitsPerSample),
           "subtype": int(sub)}
    hr = client.Initialize(_AUDCLNT_SHAREMODE_SHARED,
                           _AUDCLNT_STREAMFLAGS_LOOPBACK, 0, 0,
                           fmt_ptr, None)
    if hr != 0:
        raise AudioLoopbackError(
            "无法开启声音回环（0x%08X）。%s。"
            % (hr & 0xFFFFFFFF, AUDIO_HINTS))
    serv = client.GetService(_IID_IAudioCaptureClient)
    capture = cast(serv, POINTER(_IAudioCaptureClient))
    hr = client.Start()
    if hr != 0:
        raise AudioLoopbackError("声音回环启动失败（0x%08X）" % (hr & 0xFFFFFFFF))
    return client, capture, fmt


class LoopbackRecorder:
    """WASAPI 回环录音：start(path) 后系统输出声音持续写入 WAV，stop() 收尾。

    stop() 返回 {"ok", "seconds", "peak", "frames"}：
    peak 为采样峰值（0~1），接近 0 表示捕获期内没有任何有效声音。
    """

    def __init__(self):
        self._thread = None
        self._stop = threading.Event()
        self.result = None

    @property
    def running(self):
        t = self._thread
        return t is not None and t.is_alive()

    def start(self, path):
        if self.running:
            raise AudioLoopbackError("录音已在进行中")
        self._stop.clear()
        self._path = str(path)
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="wasapi-loopback")
        self._thread.start()

    def stop(self):
        if not self.running:
            return self.result or {"ok": False, "err": "没有进行中的录音"}
        self._stop.set()
        self._thread.join(timeout=8.0)
        return self.result or {"ok": False, "err": "录音线程异常退出"}

    # -- 采集线程 ------------------------------------------------------
    def _run(self):
        client = None
        capture = None
        rate = 1
        frames_total = 0
        peak = 0.0
        try:
            client, capture, fmt = _open_loopback()
            ch = fmt["channels"]
            rate = fmt["rate"]
            bits = fmt["bits"]
            is_float = fmt["subtype"] == _KSDATAFORMAT_SUBTYPE_FLOAT
            with wave.open(self._path, "wb") as wf:
                wf.setnchannels(ch)
                wf.setsampwidth(2)
                wf.setframerate(rate)
                while not self._stop.is_set():
                    packets = int(capture.GetNextPacketSize() or 0)
                    if not packets:
                        self._stop.wait(0.005)
                        continue
                    data, frames, flags, _devpos, _qpcpos = capture.GetBuffer()
                    if data is None or not frames:
                        capture.ReleaseBuffer(packets)
                        continue
                    buf = ctypes.cast(data, POINTER(c_byte))
                    raw = ctypes.string_at(ctypes.addressof(buf.contents),
                                           frames * ch * (bits // 8))
                    if flags & _AUDCLNT_BUFFERFLAGS_SILENT:
                        pcm = b"\x00\x00" * (frames * ch)
                    else:
                        pcm = _to_pcm16(raw, ch, bits, is_float, frames)
                    wf.writeframes(pcm)
                    frames_total += frames
                    p = _peak16(pcm)
                    if p > peak:
                        peak = p
                    capture.ReleaseBuffer(frames)
            if client is not None:
                try:
                    client.Stop()
                except Exception:
                    pass
            self.result = {
                "ok": True,
                "frames": frames_total,
                "seconds": round(frames_total / max(1, rate), 2),
                "peak": round(peak, 4),
            }
        except AudioLoopbackError as e:
            self.result = {"ok": False, "err": str(e)}
        except Exception as e:
            self.result = {"ok": False, "err": "声音采集异常：%s" % e}


def _to_pcm16(raw, ch, bits, is_float, frames):
    """源样本 → 16bit PCM 小端（支持 8/16/24/32bit 与 float32）。"""
    n = frames * ch
    if bits == 16 and not is_float:
        return raw
    if is_float and bits == 32:
        vals = struct.unpack("<%df" % n, raw[: n * 4])
        return struct.pack("<%dh" % n,
                           *(max(-32768, min(32767, int(v * 32767))) for v in vals))
    if bits == 32:
        vals = struct.unpack("<%di" % n, raw[: n * 4])
        return struct.pack("<%dh" % n, *(v >> 16 for v in vals))
    if bits == 24:
        out = bytearray()
        for i in range(n):
            v = int.from_bytes(raw[i * 3:i * 3 + 3], "little", signed=True) >> 8
            out += struct.pack("<h", v)
        return bytes(out)
    if bits == 8:
        vals = struct.unpack("<%dB" % n, raw[:n])
        return struct.pack("<%dh" % n, *((v - 128) << 8 for v in vals))
    return raw


def _peak16(pcm):
    n = len(pcm) // 2
    if not n:
        return 0.0
    vals = struct.unpack("<%dh" % n, pcm)
    return max((abs(v) for v in vals), default=0) / 32768.0
