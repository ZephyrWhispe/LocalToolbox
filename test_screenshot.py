"""截图模块单元测试 + ShotApi 桥接测试。

运行：python test_screenshot.py
"""

import io
import os
import shutil
import struct
import tempfile
import time
import types
import unittest

from PIL import Image

from app.bridge.screenshot_api import ShotApi
from app.core import screenshot

TMP = tempfile.mkdtemp(prefix="shot-test-")


def _make_png(w=320, h=200, color=(200, 60, 60)):
    img = Image.new("RGB", (w, h), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestScreenshotCore(unittest.TestCase):
    def test_01_safe_label(self):
        self.assertEqual(screenshot._safe_label("保留/非法:字符*?"), "保留_非法_字符_")
        self.assertEqual(screenshot._safe_label("  "), "截图")
        self.assertEqual(screenshot._safe_label("*" * 100), "_")
        self.assertEqual(len(screenshot._safe_label("好" * 50)), 24)

    def test_02_save_png_sequence(self):
        d = os.path.join(TMP, "shots")
        p1 = screenshot.save_png(_make_png(), d, label="测试")
        p2 = screenshot.save_png(_make_png(), d, label="测试")
        self.assertNotEqual(p1, p2)
        self.assertTrue(os.path.isfile(p1))
        self.assertTrue(os.path.isfile(p2))
        # 日期子目录 + 重名序号
        self.assertTrue(p2.endswith("测试_001.png"), p2)

    def test_03_history_list_sorted(self):
        d = os.path.join(TMP, "shots2")
        screenshot.save_png(_make_png(), d, label="a")
        screenshot.save_png(_make_png(), d, label="b")
        items = screenshot.list_history(d)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["time"] >= items[1]["time"], True)
        self.assertTrue(all(i["path"] for i in items))

    def test_04_data_url_roundtrip(self):
        png = _make_png()
        url = screenshot.png_data_url(png)
        self.assertTrue(url.startswith("data:image/png;base64,"))
        self.assertEqual(screenshot.decode_data_url(url), png)

    def test_05_png_to_dib(self):
        png = _make_png(80, 60)
        dib = screenshot._png_to_dib(png)
        w, h = struct.unpack("<ii", dib[4:12])
        self.assertEqual((w, h), (80, 60))
        self.assertEqual(len(dib), 40 + 80 * 60 * 4)

    def test_06_capture_full(self):
        try:
            png = screenshot.capture_full_png()
        except Exception as e:
            self.skipTest("无桌面会话：" + str(e))
        img = Image.open(io.BytesIO(png))
        self.assertGreater(img.width, 0)

    def test_07_capture_window_tolerable(self):
        try:
            png = screenshot.capture_window_png()
        except Exception:
            png = None
        if png is None:
            self.skipTest("无活动窗口")
        self.assertTrue(len(png) > 0)


class StubShotApi(ShotApi):
    """最小桩：仅注入 cfg，不启动窗口；emit 事件收集到列表供断言。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self._window = None
        self.emits = []
        self._init_shot()

    def emit(self, event, data=None):
        self.emits.append((event, data))


class TestShotApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.core.config import AppConfig
        cfg_path = os.path.join(TMP, "config.json")
        cls.cfg = AppConfig(cfg_path)
        cls.cfg.set("screenshot_dir", os.path.join(TMP, "api-shots"))
        cls.api = StubShotApi(cls.cfg)
        cls.api._open_path = lambda p: (True, "ok")

    def test_08_save_open_delete(self):
        a = self.api
        url = screenshot.png_data_url(_make_png())
        r1 = a.shot_save(url, "接口")
        self.assertTrue(r1["ok"], r1)
        path = r1["data"]["path"]
        self.assertTrue(os.path.isfile(path))

        r2 = a.shot_open(path)
        self.assertTrue(r2["ok"])
        self.assertTrue(r2["data"]["img"].startswith("data:image/png;base64,"))
        self.assertGreater(r2["data"]["w"], 0)

        r3 = a.shot_history()
        self.assertTrue(r3["ok"])
        self.assertTrue(any(i["path"] == path for i in r3["data"]))

        r4 = a.shot_delete(path)
        self.assertTrue(r4["ok"], r4)
        self.assertFalse(os.path.exists(path))

    def test_09_delete_out_of_dir_denied(self):
        outside = os.path.join(TMP, "outside.txt")
        with open(outside, "wb") as f:
            f.write(b"x")
        r = self.api.shot_delete(outside)
        self.assertFalse(r["ok"])
        self.assertIn("不允许", r["err"])
        os.remove(outside)

    def test_10_save_bad_label_and_set_dir(self):
        png = _make_png()
        r = self.api.shot_save(screenshot.png_data_url(png), "a/b*c")
        self.assertTrue(r["ok"], r)
        name = os.path.basename(r["data"]["path"])
        self.assertNotIn("/", name)
        self.assertNotIn("*", name)
        os.remove(r["data"]["path"])

        r2 = self.api.shot_set_dir(os.path.join(TMP, "api-shots"))
        self.assertTrue(r2["ok"])
        self.assertEqual(r2["data"], self.api._shot_dir())

    def test_11_capture_region(self):
        """区域截图接口：返回非空 PNG data URL 与原生尺寸（无桌面会话跳过）。"""
        try:
            r = self.api.shot_capture_region()
        except Exception as e:
            self.skipTest("无桌面会话：" + str(e))
        if not r.get("ok"):
            self.skipTest("无桌面会话：%s" % r.get("err"))
        self.assertTrue(r["data"]["img"].startswith("data:image/png;base64,"))
        self.assertGreater(r["data"]["w"], 0)
        self.assertGreater(r["data"]["h"], 0)

    def test_12_save_frames_gif(self):
        """保存帧序列：3 帧（内容不同）→ GIF；1 帧 → PNG。"""
        d = os.path.join(TMP, "frames")
        a = Image.new("RGB", (8, 8), (10, 10, 10))
        b = Image.new("RGB", (8, 8), (20, 20, 20))
        c = Image.new("RGB", (8, 8), (30, 30, 30))
        p = screenshot.save_frames(d, [a, b, c], 5)
        self.assertTrue(p.endswith(".gif"), p)
        with Image.open(p) as im:
            self.assertEqual(getattr(im, "n_frames", 1), 3)
        self.assertTrue(screenshot.save_frames(d, [a], 5).endswith(".png"))

    def test_13_window_rect_at_mapping(self):
        """窗口吸附接口：物理矩形映射；识别失败返回 data=None。"""
        from unittest import mock
        with mock.patch.object(screenshot, "window_rect_at",
                               return_value=(100, 200, 640, 480)):
            r = self.api.shot_window_rect_at(150, 260)
        self.assertTrue(r["ok"])
        self.assertEqual(r["data"], {"x": 100, "y": 200, "w": 640, "h": 480})
        with mock.patch.object(screenshot, "window_rect_at", return_value=None):
            r2 = self.api.shot_window_rect_at(0, 0)
        self.assertTrue(r2["ok"])
        self.assertIsNone(r2["data"])

    def test_14_after_shot_chain(self):
        """After Capture 任务链：保存开 → 落盘；全关 → 无文件无副作用。"""
        url = screenshot.png_data_url(_make_png(64, 48))
        self.cfg.set("shot_after", {"save": True, "copy": False, "edit": True})
        a = self.api._after_shot(url)
        self.assertTrue(a["saved"] and os.path.isfile(a["saved"]), a)
        self.assertFalse(a["copied"])

        self.cfg.set("shot_after", {"save": False, "copy": False, "edit": False})
        b = self.api._after_shot(url)
        self.assertIsNone(b["saved"])
        self.assertFalse(b["copied"])

    def test_15_shot_capture_sync_and_delayed(self):
        """同步截图含任务链字段；延迟截图走后台线程并推 shot_capture_done。"""
        self.cfg.set("shot_after", {"save": False, "copy": False, "edit": True})
        r = self.api.shot_capture("full", 0)
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["data"]["img"].startswith("data:image/png;base64,"))
        self.assertIn("saved", r["data"])
        self.assertIn("copied", r["data"])
        self.assertTrue(r["data"]["edit"])

        r2 = self.api.shot_capture("full", 0.05)
        self.assertTrue(r2["ok"])
        self.assertTrue(r2["data"].get("queued"))
        deadline = time.time() + 5
        while time.time() < deadline:
            evs = [e for e in self.api.emits if e[0] == "shot_capture_done"]
            if evs:
                d = evs[0][1]
                self.assertTrue(d["img"].startswith("data:image/"), d)
                break
            time.sleep(0.02)
        else:
            self.fail("延迟截图未推送 shot_capture_done")

        bad = self.api.shot_capture("full", "abc")   # delay 非法 → 当作 0 同步
        self.assertTrue(bad["ok"], bad)

    def test_16_history_thumb(self):
        """历史列表携带缩略图 data URL（读取失败时省略而非报错）。"""
        d = os.path.join(TMP, "thumbs")
        p = screenshot.save_png(_make_png(40, 40), d, label="t")
        items = screenshot.list_history(d)
        self.assertTrue(items)
        self.assertTrue(items[0]["thumb"].startswith("data:image/png;base64,"))
        os.remove(p)

    def test_17_shot_set_after_roundtrip(self):
        self.cfg.set("shot_after", {"save": True, "copy": True, "edit": True})
        r = self.api.shot_set_after(save=False, edit=False)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["data"], {"save": False, "copy": True, "edit": False,
                                     "copy_path": False, "reveal": False})
        # 恢复默认，避免影响其它用例
        self.api.cfg.set("shot_after", {"save": True, "copy": True, "edit": True})

    def test_18_region_popup_multi_monitor(self):
        """多屏遮罩：N 个显示器弹 N 个遮罩，确认后全部关闭并投递结果。"""
        from types import SimpleNamespace
        from unittest import mock
        from app.bridge import screenshot_api as sa
        from app.core import screen as screen_mod
        from PIL import Image as _Img
        import io as _io

        def _img():
            buf = _io.BytesIO()
            _Img.new("RGB", (80, 60), (10, 20, 30)).save(buf, format="PNG")
            return _Img.open(buf)

        class _FakeWin:
            def __init__(self):
                self.destroyed = False
                self.events = SimpleNamespace(closed=[])

            def destroy(self):
                self.destroyed = True

        self.cfg.set("shot_after", {"save": False, "copy": False, "edit": True})
        self.api._ovs = {}
        mons = [
            {"left": 0, "top": 0, "width": 800, "height": 600, "primary": True},
            {"left": 800, "top": 0, "width": 640, "height": 480, "primary": False},
        ]
        made = []
        with mock.patch.object(screen_mod, "monitors", return_value=mons), \
                mock.patch.object(screen_mod, "capture_monitor",
                                  side_effect=lambda m: _img()), \
                mock.patch.object(screen_mod, "monitor_phys_box",
                                  side_effect=lambda m: (m["left"], m["top"],
                                                         m["width"], m["height"])), \
                mock.patch.object(sa.webview, "create_window",
                                  side_effect=lambda *a, **k: made.append(_FakeWin()) or made[-1]):
            r = self.api.shot_region_popup("shot", "all")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["data"]["screens"], 2)
        self.assertEqual(len(self.api._ovs), 2)
        tokens = sorted(self.api._ovs.keys())

        # 已有遮罩时拒绝再开
        r_busy = self.api.shot_region_popup("shot", "all")
        self.assertFalse(r_busy["ok"])

        # 第二屏确认（带 token）：mode=shot → shot_pick（含 edit 标志），全部遮罩销毁
        url = screenshot.png_data_url(_make_png(30, 20))
        tok2 = tokens[1]
        done = self.api.shot_overlay_done(url, 5, 6, 30, 20, tok2)
        self.assertTrue(done["ok"], done)
        self.assertFalse(self.api._ovs)
        picks = [e for e in self.api.emits if e[0] == "shot_pick"]
        self.assertTrue(picks)
        self.assertEqual(picks[0][1]["w"], 30)
        self.assertTrue(picks[0][1]["edit"])
        self.assertTrue(all(w.destroyed for w in made))

        # 取消路径：cancel 事件（mode=record → shot_region_cancel）
        self.api.emits.clear()
        with mock.patch.object(screen_mod, "monitors", return_value=mons[:1]), \
                mock.patch.object(screen_mod, "capture_monitor",
                                  side_effect=lambda m: _img()), \
                mock.patch.object(screen_mod, "monitor_phys_box",
                                  side_effect=lambda m: (m["left"], m["top"],
                                                         m["width"], m["height"])), \
                mock.patch.object(sa.webview, "create_window",
                                  side_effect=lambda *a, **k: made.append(_FakeWin()) or made[-1]):
            r2 = self.api.shot_region_popup("record", "all")
        self.assertTrue(r2["ok"], r2)
        cancel = self.api.shot_overlay_cancel()
        self.assertTrue(cancel["ok"])
        cancels = [e for e in self.api.emits if e[0] == "shot_region_cancel"]
        self.assertTrue(cancels)

    def test_19_window_rect_at_live(self):
        """真实桌面窗口识别（无桌面会话/识别失败均容忍）。"""
        try:
            r = screenshot.window_rect_at(1, 1)
        except Exception as e:
            self.skipTest("无桌面会话：" + str(e))
        self.assertTrue(r is None or (r[2] > 0 and r[3] > 0))

    def test_20_dib_roundtrip(self):
        """DIB → PNG 往返：尺寸与像素一致；自上而下（biHeight<0）同样正确。"""
        png = _make_png(24, 18, (10, 200, 30))
        img = Image.open(io.BytesIO(png)).convert("RGBA")
        dib = screenshot._png_to_dib(png)
        back = Image.open(io.BytesIO(screenshot._dib_to_png(dib))).convert("RGBA")
        self.assertEqual(back.size, (24, 18))
        self.assertEqual(back.getpixel((5, 5)), img.getpixel((5, 5)))

        # 人工构造 top-down DIB（biHeight = -18），像素行序不再翻转
        header = struct.pack("<LiiHHIIiiII", 40, 24, -18, 1, 32,
                             0, 24 * 18 * 4, 0, 0, 0, 0)
        rows = b"".join(bytes([30, 200, 10, 255]) * 24 for _ in range(18))
        back2 = Image.open(io.BytesIO(
            screenshot._dib_to_png(header + rows))).convert("RGBA")
        self.assertEqual(back2.getpixel((5, 5)), (10, 200, 30, 255))

    def test_21_overlay_done_backend_crop(self):
        """遮罩 rect-only 路径：后端从留存原始 PNG 无损裁剪并走任务链。"""
        from types import SimpleNamespace
        from PIL import Image as _Img
        import io as _io

        class _FakeWin:
            def __init__(self):
                self.events = SimpleNamespace(closed=[])

            def destroy(self):
                pass

        # 80x60 红图，裁剪 (10,20,30,15) → 检查尺寸与左上角像素
        src = _Img.new("RGB", (80, 60), (200, 30, 40))
        buf = _io.BytesIO()
        src.save(buf, format="PNG")
        self.api._ovs = {"1": {"win": _FakeWin(), "mode": "shot",
                               "mon": {}, "token": "1", "box": (0, 0),
                               "png": buf.getvalue()}}
        self.cfg.set("shot_after", {"save": False, "copy": False, "edit": True})
        self.api.emits.clear()
        r = self.api.shot_overlay_done(None, 10, 20, 30, 15, "1")
        self.assertTrue(r["ok"], r)
        picks = [e for e in self.api.emits if e[0] == "shot_pick"]
        self.assertTrue(picks)
        d = picks[0][1]
        self.assertEqual((d["w"], d["h"]), (30, 15))
        back = Image.open(io.BytesIO(screenshot.decode_data_url(d["img"])))
        self.assertEqual(back.getpixel((0, 0)), (200, 30, 40))

        # 缺少留存 PNG 且 data_url 为空 → 报错而非崩溃
        self.api._ovs = {"2": {"win": _FakeWin(), "mode": "shot",
                               "mon": {}, "token": "2", "box": (0, 0)}}
        r2 = self.api.shot_overlay_done(None, 0, 0, 5, 5, "2")
        self.assertFalse(r2["ok"])
        self.assertIn("留存", r2["err"])

    def test_22_paste_clipboard_no_image(self):
        """剪贴板无图 → ok=False 中文提示（有图路径属真机验收）。"""
        r = self.api.shot_paste_clipboard()
        # 真机剪贴板可能恰好有图：两种结果都合法
        if r["ok"]:
            self.assertTrue(r["data"]["img"].startswith("data:image/"))
        else:
            self.assertIn("剪贴板", r["err"])

    def test_23_last_region_capture(self):
        """上次区域重捕：遮罩确认写入 cfg → 按坐标重捕虚拟桌面并走任务链。"""
        from types import SimpleNamespace
        from unittest import mock
        from app.bridge import screenshot_api as sa
        from app.core import screen as screen_mod

        class _FakeWin:
            def __init__(self):
                self.events = SimpleNamespace(closed=[])

            def destroy(self):
                pass

        def _img():
            import io as _io
            buf = _io.BytesIO()
            Image.new("RGB", (100, 80), (5, 6, 7)).save(buf, format="PNG")
            return Image.open(buf)

        self.cfg.set("shot_after", {"save": False, "copy": False, "edit": True})
        self.cfg.set("shot_last_region", None)
        # 无记录 → 报错提示
        r0 = self.api.shot_capture_last_region()
        self.assertFalse(r0["ok"])
        self.assertIn("历史选区", r0["err"])

        # 遮罩确认（shot 模式）→ 写入 last_region（虚拟桌面坐标 = box + 选区）
        self.api._ovs = {"9": {"win": _FakeWin(), "mode": "shot",
                               "mon": {}, "token": "9", "box": (800, 0),
                               "png": None}}
        url = screenshot.png_data_url(_make_png(20, 10))
        self.api.shot_overlay_done(url, 3, 4, 20, 10, "9")
        lr = self.cfg.get("shot_last_region")
        self.assertEqual((lr["x"], lr["y"], lr["w"], lr["h"]), (803, 4, 20, 10))

        # 重捕：mock 虚拟桌面抓取，裁剪尺寸与坐标一致
        captured = {}
        def _fake_virtual():
            captured["img"] = _img()
            return captured["img"]
        with mock.patch.object(screen_mod, "capture_virtual",
                               side_effect=_fake_virtual):
            r = self.api.shot_capture_last_region()
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["data"]["w"], r["data"]["h"]), (20, 10))
        self.assertEqual(r["data"]["mode"], "region")
        self.assertTrue(r["data"]["edit"])

    def test_24_after_chain_copy_path(self):
        """任务链扩展：save+copy_path → 文件落盘且路径写入剪贴板（mock pyperclip）。"""
        from unittest import mock
        import sys
        fake = types.ModuleType("pyperclip")
        fake.copy = lambda t: captured_path.setdefault("v", t)
        captured_path = {}
        sys.modules["pyperclip"] = fake
        try:
            self.cfg.set("shot_after", {"save": True, "copy": False,
                                        "edit": True, "copy_path": True,
                                        "reveal": False})
            out = self.api._after_shot(screenshot.png_data_url(_make_png(40, 30)))
            self.assertTrue(out["saved"] and os.path.isfile(out["saved"]))
            self.assertTrue(out["path_copied"])
            self.assertEqual(captured_path.get("v"), out["saved"])
        finally:
            sys.modules.pop("pyperclip", None)

    def test_25_set_after_extended(self):
        """shot_set_after 支持五项任务；None 项保持不变。"""
        self.cfg.set("shot_after", {"save": True, "copy": True, "edit": True,
                                    "copy_path": False, "reveal": False})
        r = self.api.shot_set_after(copy_path=True, reveal=True)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["data"], {"save": True, "copy": True, "edit": True,
                                     "copy_path": True, "reveal": True})
        r2 = self.api.shot_set_after(reveal=False)   # 只动 reveal
        self.assertTrue(r2["data"]["copy_path"])
        self.assertFalse(r2["data"]["reveal"])
        self.api.cfg.set("shot_after", {"save": True, "copy": True, "edit": True,
                                        "copy_path": False, "reveal": False})

    def test_26_rect_at_excludes_overlays(self):
        """窗口吸附必须排除当前遮罩窗口：查询时把遮罩句柄集合传给后端，
        否则全屏遮罩会被识别成“窗口”导致单击即全屏。"""
        from unittest import mock
        fake_hwnds = {0x1234}
        with mock.patch.object(self.api, "_ov_hwnds",
                               return_value=fake_hwnds), \
                mock.patch.object(screenshot, "window_rect_at",
                                  return_value=(100, 200, 640, 480)) as wr:
            r = self.api.shot_window_rect_at(10, 20)
        self.assertTrue(r["ok"])
        self.assertEqual(wr.call_args.args[:2], (10, 20))
        self.assertEqual(wr.call_args.kwargs.get("skip"), fake_hwnds)

    def test_27_overlay_window_no_easy_drag(self):
        """遮罩为 frameless 窗口且必须禁用 easy_drag：默认的 easy_drag 会把
        整个遮罩当标题栏拖动，导致截图画面跟着鼠标走、无法划区域。"""
        from types import SimpleNamespace
        from unittest import mock
        from app.bridge import screenshot_api as sa
        from app.core import screen as screen_mod
        import io as _io

        buf = _io.BytesIO()
        Image.new("RGB", (80, 60), (10, 20, 30)).save(buf, format="PNG")
        img = Image.open(buf)

        class _FakeWin:
            def __init__(self):
                self.events = SimpleNamespace(closed=[])

            def destroy(self):
                pass

        calls = []

        def _create(*a, **k):
            calls.append(k)
            return _FakeWin()

        self.api._ovs = {}
        mons = [{"left": 0, "top": 0, "width": 800, "height": 600,
                 "primary": True}]
        with mock.patch.object(screen_mod, "monitors", return_value=mons), \
                mock.patch.object(screen_mod, "capture_monitor",
                                  side_effect=lambda m: img), \
                mock.patch.object(screen_mod, "monitor_phys_box",
                                  side_effect=lambda m: (m["left"], m["top"],
                                                         m["width"], m["height"])), \
                mock.patch.object(sa.webview, "create_window",
                                  side_effect=_create):
            r = self.api.shot_region_popup("shot", "all")
        self.assertTrue(r["ok"], r)
        self.assertTrue(calls and calls[0].get("easy_drag") is False)
        self.api._ovs = {}

    def test_28_rect_at_filters_fullscreen_windows(self):
        """整屏窗口不参与吸附：命中覆盖遮罩显示器 95% 以上的窗口时返回
        data=None，遮罩回退自由圈选，避免单击把最大化窗口当成全屏截图。"""
        from unittest import mock
        self.api._ovs = {"1": {"win": None, "mode": "shot", "mon": {},
                               "token": "1", "box": (0, 0, 1920, 1080)}}
        try:
            with mock.patch.object(self.api, "_ov_hwnds", return_value=set()), \
                    mock.patch.object(screenshot, "window_rect_at",
                                      return_value=(0, 0, 1920, 1080)):
                r = self.api.shot_window_rect_at(100, 100)
            self.assertTrue(r["ok"])
            self.assertIsNone(r["data"])

            with mock.patch.object(self.api, "_ov_hwnds", return_value=set()), \
                    mock.patch.object(screenshot, "window_rect_at",
                                      return_value=(100, 200, 640, 480)):
                r2 = self.api.shot_window_rect_at(150, 250)
            self.assertTrue(r2["ok"])
            self.assertEqual(r2["data"],
                             {"x": 100, "y": 200, "w": 640, "h": 480})
        finally:
            self.api._ovs = {}


    def test_29_overlay_confirm_emits_before_destroy(self):
        """遮罩确认：先投递 shot_pick 事件、后销毁遮罩窗口（顺序回归）。

        历史缺陷：先 win.destroy() 再 emit，销毁窗口与 js_api 调用线程相互
        干扰，事件偶尔丢失，表现为「确认后界面无任何反应」。"""
        from types import SimpleNamespace
        from PIL import Image as _Img

        order = []

        class _Win:
            def __init__(self):
                self.events = SimpleNamespace(closed=[])

            def destroy(self):
                order.append("destroy")

        src = _Img.new("RGB", (80, 60), (10, 20, 30))
        buf = io.BytesIO()
        src.save(buf, format="PNG")

        orig_emit = self.api.emit

        def probe(event, data=None):
            order.append("emit:" + str(event))
            return orig_emit(event, data)

        self.cfg.set("shot_after", {"save": False, "copy": False, "edit": True})
        self.api._ovs = {"7": {"win": _Win(), "mode": "shot", "mon": {},
                               "token": "7", "box": (0, 0), "png": buf.getvalue()}}
        self.api.emits.clear()
        self.api.emit = probe
        try:
            r = self.api.shot_overlay_done(None, 0, 0, 20, 10, "7")
        finally:
            self.api.emit = orig_emit
        self.assertTrue(r["ok"], r)
        self.assertIn("emit:shot_pick", order)
        self.assertIn("destroy", order)
        self.assertLess(order.index("emit:shot_pick"), order.index("destroy"),
                        "事件必须先于遮罩窗口销毁投递")
        self.api._ovs = {}

    def test_30_overlay_error_restores_window_and_notifies(self):
        """遮罩确认失败：仍唤回主窗口并投递取消事件，避免「窗口消失且无提示」。"""
        restored = []
        orig_restore = self.api._restore_main_window
        self.api._restore_main_window = lambda: restored.append(True)
        # 遮罩缺留存 PNG → crop 前抛「遮罩未留存原始截图」
        self.api._ovs = {"8": {"win": None, "mode": "shot", "mon": {},
                               "token": "8", "box": (0, 0)}}
        self.api.emits.clear()
        try:
            r = self.api.shot_overlay_done(None, 0, 0, 5, 5, "8")
        finally:
            self.api._restore_main_window = orig_restore
        self.assertFalse(r["ok"])
        self.assertIn("留存", r["err"])
        self.assertTrue(restored, "失败路径必须唤回主窗口")
        self.assertTrue([e for e in self.api.emits if e[0] == "shot_pick_cancel"])
        self.api._ovs = {}

    def test_31_overlay_cancel_tolerates_extra_args(self):
        """遮罩取消：容忍前端补足的 null 实参（旧遮罩页固定传 6 个）。

        历史缺陷：``shot_overlay_cancel() takes from 1 to 2 positional
        arguments but 7 were given`` → ESC/取消按钮抛 TypeError，遮罩卡死。"""
        from types import SimpleNamespace
        destroyed = []
        restored = []

        class _Win:
            def __init__(self):
                self.events = SimpleNamespace(closed=[])

            def destroy(self):
                destroyed.append(True)

        orig_restore = self.api._restore_main_window
        self.api._restore_main_window = lambda: restored.append(True)
        self.api._ovs = {"9": {"win": _Win(), "mode": "shot", "mon": {},
                               "token": "9", "box": (0, 0)}}
        self.api.emits.clear()
        try:
            # token + 5 个 None（模拟 pywebview 把 undefined 序列化为 null）
            r = self.api.shot_overlay_cancel("9", None, None, None, None, None)
        finally:
            self.api._restore_main_window = orig_restore
        self.assertTrue(r["ok"], r)
        self.assertTrue(destroyed, "取消必须销毁遮罩窗口")
        self.assertTrue(restored, "取消必须唤回主窗口")
        self.assertTrue([e for e in self.api.emits if e[0] == "shot_pick_cancel"])
        self.api._ovs = {}

    def test_32_overlay_done_tolerates_extra_args(self):
        """遮罩确认同样容忍多余实参（前端约定 6 个，多传不报错）。"""
        from types import SimpleNamespace
        from PIL import Image as _Img

        class _Win:
            def __init__(self):
                self.events = SimpleNamespace(closed=[])

            def destroy(self):
                pass

        src = _Img.new("RGB", (40, 30), (200, 60, 60))
        buf = io.BytesIO()
        src.save(buf, format="PNG")
        self.cfg.set("shot_after", {"save": False, "copy": False, "edit": True})
        self.api._ovs = {"10": {"win": _Win(), "mode": "shot", "mon": {},
                                "token": "10", "box": (0, 0), "png": buf.getvalue()}}
        self.api.emits.clear()
        r = self.api.shot_overlay_done(None, 0, 0, 10, 10, "10", None)
        self.assertTrue(r["ok"], r)
        self.assertTrue([e for e in self.api.emits if e[0] == "shot_pick"])
        self.api._ovs = {}


if __name__ == "__main__":
    unittest.main(verbosity=2, exit=False)
    shutil.rmtree(TMP, ignore_errors=True)