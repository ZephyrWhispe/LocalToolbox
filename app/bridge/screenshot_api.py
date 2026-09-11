"""截图桥接：捕获（全屏/窗口/区域遮罩弹窗）、保存、复制剪贴板、历史、目录设置。"""

import ctypes
import json
import os
import sys
import threading
import time
from ctypes import wintypes

import webview

from ..core import logger as applog
from ..core import screen as screen_mod
from ..core import screenshot

log = applog.get_logger("shot")


class ShotApi:
    def _init_shot(self):
        self._ovs = {}   # token → {"win","mode","mon","box","token"}（多显示器各一）
        self._ov_seq = 0

    # -- 区域遮罩弹窗（ShareX 式逐屏圈选） -------------------------------
    def _shot_ui_base(self):
        if getattr(sys, "frozen", False):
            return sys._MEIPASS
        return os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))

    def _build_overlay_html(self, img_data_url, mode, token, box):
        base = self._shot_ui_base()
        with open(os.path.join(base, "webui", "overlay.html"),
                  "r", encoding="utf-8") as f:
            html = f.read()
        with open(os.path.join(base, "webui", "js", "pages", "region_capture.js"),
                  "r", encoding="utf-8") as f:
            rc_js = f.read()
        html = html.replace("/*__REGION_JS__*/", rc_js)
        html = html.replace("/*__CFG_JSON__*/", json.dumps(
            {"img": img_data_url, "mode": mode, "token": str(token),
             "box": [int(box[0]), int(box[1])] if box else [0, 0]},
            ensure_ascii=False))
        return html

    def _overlay_cancel_event(self, mode):
        return "shot_pick_cancel" if mode == "shot" else "shot_region_cancel"

    def _destroy_ovs(self, ovs=None):
        """销毁遮罩窗口集合（缺省用当前 _ovs；done/cancel 路径须显式传入）。

        优先使用 Win32 API 强制关闭（更可靠），再调用 pywebview destroy。
        """
        user32 = ctypes.windll.user32
        WM_CLOSE = 0x0010
        for ov in (self._ovs if ovs is None else ovs).values():
            win = ov.get("win")
            if not win:
                continue
            # 先尝试通过 Win32 API 发送 WM_CLOSE（强制关闭窗口）
            try:
                native = getattr(win, "native", None)
                hwnd = None
                if native is not None:
                    hwnd = (getattr(native, "Handle", None)
                            or getattr(native, "hwnd", None)
                            or (native if isinstance(native, int) else None))
                if hwnd:
                    user32.PostMessageW(wintypes.HWND(hwnd), WM_CLOSE, 0, 0)
                    time.sleep(0.05)  # 给系统一点时间处理消息
            except Exception:
                pass
            # 再调用 pywebview 的 destroy 作为兜底
            try:
                win.destroy()
            except Exception:
                pass

    def _restore_main_window(self):
        """遮罩关闭后唤回主窗口（截图前会先隐藏，避免截进 App 自身界面）。

        仅做 show/restore + 一次干净激活；不再反复 SetForegroundWindow，
        避免激活抖动被误看成"窗口跟随鼠标"。
        """
        try:
            if self._window is None:
                return
            time.sleep(0.12)   # 等遮罩窗口 WM_CLOSE 处理完
            self._window.show()
            self._window.restore()
            try:
                native = getattr(self._window, "native", None)
                h = getattr(native, "Handle", None) if native is not None else None
                hwnd = h if isinstance(h, int) else (int(h.ToInt64()) if h else 0)
                if hwnd:
                    user32 = ctypes.windll.user32
                    user32.SetWindowPos(wintypes.HWND(hwnd), wintypes.HWND(0),
                                        0, 0, 0, 0, 0x0001 | 0x0002)  # NOSIZE|NOMOVE
                    user32.SetForegroundWindow(wintypes.HWND(hwnd))
            except Exception:
                pass
        except Exception:
            pass

    def _raise_overlay_window(self, win):
        """弹窗创建后置顶（frameless 弹窗可能被其它窗口压住时的兜底）。"""
        try:
            time.sleep(0.4)
            native = getattr(win, "native", None)
            hwnd = None
            if native is not None:
                hwnd = (getattr(native, "Handle", None)
                        or getattr(native, "hwnd", None)
                        or (native if isinstance(native, int) else None))
            if not hwnd:
                return
            user32 = ctypes.windll.user32
            HWND_TOPMOST = -1
            SWP_NOSIZE = 0x0001
            SWP_NOMOVE = 0x0002
            SWP_NOACTIVATE = 0x0010
            user32.SetWindowPos(wintypes.HWND(hwnd), HWND_TOPMOST,
                                0, 0, 0, 0,
                                SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)
            user32.SetForegroundWindow(wintypes.HWND(hwnd))
        except Exception:
            pass

    def shot_region_popup(self, mode="shot", scope="all"):
        """逐显示器弹出全屏圈选遮罩（ShareX 式）。

        mode: "shot"=截屏（确认后回主窗口编辑器）；"record"=选录制区域
        （确认后仅回传区域矩形，事件 shot_region_rect）。
        scope: "all"=每个显示器各一个遮罩（默认，多屏全部可选）；
               "cursor"=仅光标所在显示器。
        任一遮罩确认 / 取消即关闭全部。
        """
        mode = str(mode or "shot")
        if mode not in ("shot", "record", "ruler"):
            return {"ok": False, "err": "未知的遮罩模式：%s" % mode}
        scope = str(scope or "all")
        if self._ovs:
            return {"ok": False, "err": "已有一个选区遮罩在打开，请先完成或按 ESC 取消"}
        # 截图前隐藏主窗口：避免 App 自身界面被截进冻结背景（done/cancel/失败时唤回）
        try:
            if self._window is not None:
                self._window.hide()
                time.sleep(0.15)   # 等窗口真正消失再抓屏
        except Exception:
            pass
        try:
            if scope == "cursor":
                mons = [m for m in [screen_mod.monitor_at()] if m]
            else:
                mons = screen_mod.monitors()
                if not mons:
                    m = screen_mod.monitor_at()
                    mons = [m] if m else []
            if not mons:
                self._restore_main_window()
                return {"ok": False, "err": "未检测到显示器"}
            import io as _io
            try:
                for mon in mons:
                    img = screen_mod.capture_monitor(mon)
                    # 原始 PNG 留在遮罩状态里（确认时后端无损裁剪）；
                    # 遮罩背景用 JPEG（4K 下 base64 体积约为 PNG 的 1/8，弹出明显更快）
                    png_buf = _io.BytesIO()
                    img.convert("RGB").save(png_buf, format="PNG")
                    jpg_buf = _io.BytesIO()
                    img.convert("RGB").save(jpg_buf, format="JPEG", quality=80)
                    self._ov_seq += 1
                    token = str(self._ov_seq)
                    html = self._build_overlay_html(
                        screenshot.png_data_url(jpg_buf.getvalue()), mode, token,
                        screen_mod.monitor_phys_box(mon))
                    try:
                        win = webview.create_window(
                            "", html=html, frameless=True, shadow=False,
                            easy_drag=False,
                            x=int(mon["left"]), y=int(mon["top"]),
                            width=int(mon["width"]), height=int(mon["height"]),
                            js_api=self, background_color="#05070b", on_top=True)
                    except TypeError:
                        win = webview.create_window(
                            "", html=html, frameless=True, shadow=False,
                            easy_drag=False,
                            x=int(mon["left"]), y=int(mon["top"]),
                            width=int(mon["width"]), height=int(mon["height"]),
                            js_api=self, background_color="#05070b")
                    if win is None:
                        raise RuntimeError("遮罩窗口创建失败")
                    self._ovs[token] = {
                        "win": win, "mode": mode, "mon": mon, "token": token,
                        "box": screen_mod.monitor_phys_box(mon),
                        "png": png_buf.getvalue(),
                    }

                    def on_closed(tok=token):
                        # 用户/系统关闭遮罩（非 done/cancel 显式流程）→ 按取消处理
                        if tok in self._ovs:
                            del self._ovs[tok]
                            if not self._ovs:
                                self._restore_main_window()
                            try:
                                self.emit(self._overlay_cancel_event(mode), None)
                            except Exception:
                                pass

                    try:
                        win.events.closed += on_closed
                    except Exception:
                        pass
                    threading.Thread(target=self._raise_overlay_window,
                                     args=(win,), daemon=True).start()
            except Exception:
                self._destroy_ovs()
                self._ovs = {}
                raise
            return {"ok": True, "data": {"mode": mode, "screens": len(mons)}}
        except Exception as e:
            # 主窗口已在截图前隐藏：遮罩创建失败且未遗留任何遮罩时唤回，避免 App 隐身
            if not self._ovs:
                self._restore_main_window()
            return {"ok": False, "err": "打开选区遮罩失败：%s" % e}

    def shot_overlay_done(self, data_url, x, y, w, h, token=None, *_extra):
        """遮罩确认：关闭全部遮罩并按模式投递结果。

        data_url 可为空（遮罩只回传选区矩形）：此时从遮罩留存的原始 PNG
        后端无损裁剪——遮罩背景是 JPEG 压缩图，前端裁剪会引入画质损失。

        关键顺序：先 emit 事件再销毁遮罩窗口（finally），避免遮罩窗口销毁
        时干扰 pywebview API 调用线程导致事件丢失。

        容忍多余实参（*_extra）：pywebview 会把 JS 传入的 undefined 序列化
        为 null 一并转来，旧版遮罩页固定补足 6 个参数，多收参数不应报错。
        """
        ovs = self._ovs
        self._ovs = {}
        if not ovs:
            return {"ok": False, "err": "没有进行中的选区遮罩"}
        ov = ovs.get(str(token or "")) or next(iter(ovs.values()))
        mode = ov.get("mode", "shot")
        try:
            x, y, w, h = int(x or 0), int(y or 0), int(w or 0), int(h or 0)
            if w <= 0 or h <= 0:
                raise ValueError("空选区")
            if mode == "shot":
                # 先恢复主窗口（截图前已隐藏），再处理截图结果
                self._restore_main_window()
                # 记录上次区域（虚拟桌面物理坐标）供「重拍上次区域」复用
                bx = ov.get("box") or (0, 0, 0, 0)
                self.cfg.set("shot_last_region",
                             {"x": bx[0] + x, "y": bx[1] + y, "w": w, "h": h})
                if not data_url:
                    from PIL import Image
                    import io as _io
                    src = ov.get("png")
                    if not src:
                        raise ValueError("遮罩未留存原始截图")
                    cropped = Image.open(_io.BytesIO(src)).crop(
                        (x, y, x + w, y + h))
                    out = _io.BytesIO()
                    cropped.save(out, format="PNG")
                    data_url = screenshot.png_data_url(out.getvalue())
                after = self._after_shot(data_url)
                # 重要：在主窗口恢复后 emit，遮罩窗口销毁前发送事件
                # 这样即使遮罩销毁有延迟或异常，事件也已成功投递
                self.emit("shot_pick", {
                    "img": data_url, "w": w, "h": h, "rect": None,
                    "saved": after.get("saved"),
                    "copied": after.get("copied"),
                    "edit": self._shot_after_cfg().get("edit", True),
                })
            else:
                self._restore_main_window()
                bx = ov.get("box") or (0, 0, 0, 0)
                self.emit("shot_region_rect", {
                    "rect": {"x": bx[0] + x, "y": bx[1] + y, "w": w, "h": h},
                })
            return {"ok": True, "data": {"mode": mode}}
        except Exception as e:
            # 失败路径同样要把主窗口唤回（截图前已隐藏）并告知前端，
            # 否则用户面对的是"窗口消失且无任何提示"的假死状态
            log.warning("遮罩截图处理失败：%s", e)
            self._restore_main_window()
            try:
                evt = ("shot_pick_cancel" if mode == "shot"
                       else "shot_region_cancel")
                self.emit(evt, None)
            except Exception:
                pass
            return {"ok": False, "err": str(e)}
        finally:
            # 确保遮罩窗口最终被销毁（即使上面发生异常）
            self._destroy_ovs(ovs)

    def shot_overlay_cancel(self, token=None, *_extra):
        """遮罩取消（ESC / 取消按钮）：关闭全部遮罩。

        容忍多余实参（*_extra）：pywebview 会把 JS 传入的 undefined 序列化
        为 null 一并转来——旧版遮罩页固定补足 6 个参数，曾导致
        ``takes from 1 to 2 positional arguments but 7 were given``，
        ESC 完全失效、遮罩卡死无法退出。
        """
        ovs = self._ovs
        self._ovs = {}
        if ovs:
            mode = next(iter(ovs.values())).get("mode", "shot")
            self._restore_main_window()
            try:
                self.emit(self._overlay_cancel_event(mode), None)
            except Exception:
                pass
        self._destroy_ovs(ovs)
        return {"ok": True, "data": None}

    def _ov_hwnds(self):
        """当前活动遮罩窗口的 Win32 句柄集合（int），供窗口吸附查询排除。"""
        hwnds = set()
        for ov in self._ovs.values():
            win = ov.get("win")
            if not win:
                continue
            try:
                native = getattr(win, "native", None)
                if native is None:
                    continue
                h = (getattr(native, "Handle", None)
                     or getattr(native, "hwnd", None)
                     or (native if isinstance(native, int) else None))
                if h is None:
                    continue
                if isinstance(h, int):
                    hwnds.add(h)
                elif hasattr(h, "ToInt64"):
                    hwnds.add(int(h.ToInt64()))
            except Exception:
                continue
        return hwnds

    def shot_window_rect_at(self, sx, sy):
        """遮罩窗口吸附：屏幕物理坐标处顶层窗口矩形（虚拟桌面物理坐标）。

        自动排除当前遮罩窗口（全屏置顶会拦下所有命中）与桌面背景；
        并过滤“整屏窗口”（最大化/全屏应用）——面积覆盖任一遮罩显示器
        95% 以上的窗口不参与吸附，避免单击即全屏。无法识别返回
        data=None，遮罩层回退自由圈选。
        """
        try:
            r = screenshot.window_rect_at(sx, sy, skip=self._ov_hwnds())
            if not r:
                return {"ok": True, "data": None}
            for ov in self._ovs.values():
                mb = ov.get("box") or (0, 0, 0, 0)
                mon_area = int(mb[2]) * int(mb[3])
                if mon_area > 0 and int(r[2]) * int(r[3]) >= mon_area * 0.95:
                    return {"ok": True, "data": None}
            return {"ok": True, "data": {"x": r[0], "y": r[1],
                                          "w": r[2], "h": r[3]}}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def shot_capture_last_region(self):
        """重拍上次区域（ShareX CaptureLastRegion 思想）：按 cfg
        shot_last_region 在整个虚拟桌面重新捕获同一直接区域并走任务链。
        无记录时返回错误提示先做一次区域截图。
        """
        try:
            r = self.cfg.get("shot_last_region")
            if not (isinstance(r, dict) and r.get("w", 0) > 0 and r.get("h", 0) > 0):
                return {"ok": False, "err": "还没有历史选区，请先做一次区域截图"}
            x, y = int(r["x"]), int(r["y"])
            w, h = int(r["w"]), int(r["h"])
            img = screen_mod.capture_virtual().crop((x, y, x + w, y + h))
            import io as _io
            buf = _io.BytesIO()
            img.save(buf, format="PNG")
            data_url = screenshot.png_data_url(buf.getvalue())
            from PIL import Image as _Img
            wv, hv = _Img.open(_io.BytesIO(buf.getvalue())).size
            after = self._after_shot(data_url)
            return {"ok": True, "data": {
                "img": data_url, "w": wv, "h": hv, "mode": "region",
                "saved": after.get("saved"), "copied": after.get("copied"),
                "path_copied": after.get("path_copied", False),
                "revealed": after.get("revealed", False),
                "edit": self._shot_after_cfg().get("edit", True),
            }}
        except Exception as e:
            return {"ok": False, "err": "重拍上次区域失败：%s" % e}

    def shot_paste_clipboard(self):
        """读取系统剪贴板图片 → 编辑器（无图时 ok=False 提示）。"""
        try:
            png = screenshot.read_clipboard_png()
            if not png:
                return {"ok": False, "err": "剪贴板中没有图片"}
            from PIL import Image
            import io as _io
            w, h = Image.open(_io.BytesIO(png)).size
            return {"ok": True, "data": {
                "img": screenshot.png_data_url(png), "w": w, "h": h,
                "mode": "clipboard",
            }}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def shot_restore_window(self):
        """恢复因截图被隐藏的主窗口（全屏热键路径：截图完成后前端回调）。"""
        if getattr(self, "_shot_restore_pending", False):
            self._shot_restore_pending = False
            try:
                if self._window is not None:
                    self._window.show()
                    self._window.restore()
            except Exception:
                pass
        return {"ok": True, "data": None}

    def _shot_after_cfg(self):
        """截图后自动任务配置（缺省：保存+复制+进编辑器；reveal/copy_path 关）。"""
        c = self.cfg.get("shot_after")
        if not isinstance(c, dict):
            c = {}
        return {"save": bool(c.get("save", True)),
                "copy": bool(c.get("copy", True)),
                "edit": bool(c.get("edit", True)),
                "copy_path": bool(c.get("copy_path", False)),
                "reveal": bool(c.get("reveal", False))}

    def _after_shot(self, data_url):
        """After Capture 任务链（ShareX 式）：保存文件 / 复制剪贴板 /
        复制文件路径 / 在资源管理器中定位。

        按声明顺序执行，单步失败不阻断其它任务。返回结果字典。
        """
        after = self._shot_after_cfg()
        out = {"saved": None, "copied": False, "path_copied": False,
               "revealed": False}
        if after.get("save"):
            try:
                png = screenshot.decode_data_url(str(data_url))
                out["saved"] = screenshot.save_png(png, self._shot_dir(),
                                                   label="截图")
            except Exception as e:
                log.warning("截图自动保存失败：%s", e)
        if after.get("copy"):
            try:
                png = screenshot.decode_data_url(str(data_url))
                out["copied"] = screenshot.copy_png_to_clipboard(png)
            except Exception as e:
                log.warning("截图自动复制失败：%s", e)
        path = out.get("saved")
        if path and after.get("copy_path"):
            try:
                import pyperclip
                pyperclip.copy(path)
                out["path_copied"] = True
            except Exception as e:
                log.warning("复制文件路径失败：%s", e)
        if path and after.get("reveal"):
            try:
                ok, msg = self._open_path(os.path.dirname(path))
                out["revealed"] = bool(ok)
                if not ok:
                    log.warning("定位截图文件失败：%s", msg)
            except Exception as e:
                log.warning("定位截图文件失败：%s", e)
        return out

    def shot_set_after(self, save=None, copy=None, edit=None,
                       copy_path=None, reveal=None):
        """配置截图后自动任务（传 None 保持该项不变），返回生效配置。"""
        try:
            cur = self._shot_after_cfg()
            for key, val in (("save", save), ("copy", copy), ("edit", edit),
                             ("copy_path", copy_path), ("reveal", reveal)):
                if val is not None:
                    cur[key] = bool(val)
            self.cfg.set("shot_after", cur)
            return {"ok": True, "data": cur}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def _shot_dir(self):
        d = str(self.cfg.get("screenshot_dir") or "").strip()
        if not d:
            d = screenshot.DEFAULT_DIR
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
        return d

    # -- API -----------------------------------------------------------
    def shot_capture(self, mode="full", delay=0):
        """全屏（虚拟桌面）/活动窗口截图，自动执行 After Capture 任务链。

        delay>0：延迟 N 秒截图（期间自动隐藏主窗口避免截到自己，完成后恢复），
        后台线程完成后 emit ``shot_capture_done``（同结构 data，含 err 时为失败），
        本调用立即返回 {queued: True}。
        delay=0：同步捕获并返回 {img,w,h,mode,saved,copied,edit}。
        """
        try:
            mode = "window" if str(mode) == "window" else "full"
            try:
                delay = max(0.0, min(float(delay or 0), 60.0))
            except (TypeError, ValueError):
                delay = 0.0
            if delay > 0:
                threading.Thread(target=self._delayed_capture,
                                 args=(mode, delay), daemon=True,
                                 name="shot-delay").start()
                return {"ok": True,
                        "data": {"queued": True, "delay": delay, "mode": mode}}
            data = self._do_capture(mode)
            after = self._after_shot(data["img"])
            data.update(saved=after.get("saved"), copied=after.get("copied"),
                        edit=self._shot_after_cfg().get("edit", True))
            return {"ok": True, "data": data}
        except Exception as e:
            return {"ok": False, "err": "截图失败：%s" % e}

    def _do_capture(self, mode):
        if mode == "window":
            png = screenshot.capture_window_png()
            if not png:
                raise RuntimeError("无法获取活动窗口（可能已最小化或无前台窗口）")
        else:
            png = screenshot.capture_full_png()
        from PIL import Image
        import io as _io
        w, h = Image.open(_io.BytesIO(png)).size
        return {"img": screenshot.png_data_url(png), "w": w, "h": h, "mode": mode}

    def _delayed_capture(self, mode, delay):
        """延迟截图线程：隐藏主窗口 → 等待 → 捕获+任务链 → 恢复窗口 → 推送。"""
        hidden = False
        try:
            if self._window is not None:
                self._window.hide()
                hidden = True
        except Exception:
            hidden = False
        try:
            time.sleep(delay)
            data = self._do_capture(mode)
            after = self._after_shot(data["img"])
            data.update(saved=after.get("saved"), copied=after.get("copied"),
                        edit=self._shot_after_cfg().get("edit", True))
            self.emit("shot_capture_done", data)
        except Exception as e:
            self.emit("shot_capture_done", {"err": "截图失败：%s" % e})
        finally:
            if hidden and self._window is not None:
                try:
                    time.sleep(0.1)  # 给系统一点时间处理窗口状态变化
                    self._window.show()
                    self._window.restore()
                    # 显式激活主窗口，确保正确聚焦
                    try:
                        native = getattr(self._window, "native", None)
                        hwnd = None
                        if native is not None:
                            hwnd = (getattr(native, "Handle", None)
                                    or getattr(native, "hwnd", None)
                                    or (native if isinstance(native, int) else None))
                        if hwnd:
                            user32 = ctypes.windll.user32
                            HWND_TOP = 0
                            SWP_NOSIZE = 0x0001
                            SWP_NOMOVE = 0x0002
                            user32.SetWindowPos(wintypes.HWND(hwnd), HWND_TOP,
                                                0, 0, 0, 0,
                                                SWP_NOSIZE | SWP_NOMOVE)
                            user32.SetForegroundWindow(wintypes.HWND(hwnd))
                    except Exception:
                        pass
                except Exception:
                    pass

    def shot_capture_region(self):
        """捕获全屏 PNG（区域裁剪在前端遮罩完成），返回 img/w/h。保留兼容接口。"""
        try:
            png = screenshot.capture_full_png()
            from PIL import Image
            import io as _io
            w, h = Image.open(_io.BytesIO(png)).size
            return {"ok": True, "data": {
                "img": screenshot.png_data_url(png),
                "w": w, "h": h, "mode": "region",
            }}
        except Exception as e:
            return {"ok": False, "err": "截图失败：%s" % e}

    def shot_ocr(self, data_url):
        """识别画布图片（data URL）中的文字，返回全文与逐行坐标。"""
        try:
            from ..core import ocr
            if not ocr.is_available():
                return {"ok": False, "err": ocr._UNAVAILABLE_MSG}
            png = screenshot.decode_data_url(str(data_url))
            result = ocr.recognize_bytes(png)
            if not (result["text"] or "").strip():
                return {"ok": True, "data": {"text": "", "lines": [], "empty": True}}
            return {"ok": True, "data": result}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def shot_save(self, data_url, label="截图"):
        """保存编辑后的图片（data URL 入参），返回保存路径。"""
        try:
            png = screenshot.decode_data_url(str(data_url))
            path = screenshot.save_png(png, self._shot_dir(), label=str(label or "截图"))
            return {"ok": True, "data": {"path": path}}
        except Exception as e:
            return {"ok": False, "err": "保存失败：%s" % e}

    def shot_copy(self, data_url):
        """把图片写入系统剪贴板。"""
        try:
            png = screenshot.decode_data_url(str(data_url))
            if screenshot.copy_png_to_clipboard(png):
                return {"ok": True, "data": None}
            return {"ok": False, "err": "写入剪贴板失败"}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def shot_history(self):
        try:
            return {"ok": True, "data": screenshot.list_history(self._shot_dir())}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def shot_open(self, path):
        """读取历史截图，返回 PNG data URL（供画布加载）。"""
        try:
            path = str(path or "")
            if not os.path.isfile(path):
                return {"ok": False, "err": "文件不存在：%s" % path}
            with open(path, "rb") as f:
                png = f.read()
            from PIL import Image
            import io as _io
            w, h = Image.open(_io.BytesIO(png)).size
            return {"ok": True, "data": {"img": screenshot.png_data_url(png), "w": w, "h": h}}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def shot_reveal(self, path=None):
        """在资源管理器中定位文件（未指定则打开截图目录）。"""
        try:
            p = str(path or "").strip() or self._shot_dir()
            if os.path.isfile(p):
                ok, msg = self._open_path(os.path.dirname(p))
            else:
                ok, msg = self._open_path(p)
            return {"ok": ok, "err": msg} if not ok else {"ok": True, "data": None}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def shot_delete(self, path):
        """删除历史截图文件（仅允许删除截图目录内的文件）。"""
        try:
            path = str(path or "")
            d = os.path.realpath(self._shot_dir())
            real = os.path.realpath(path)
            if not os.path.isfile(real):
                return {"ok": False, "err": "文件不存在"}
            if not (real == d or real.startswith(d + os.sep)):
                return {"ok": False, "err": "不允许删除该文件"}
            os.remove(real)
            log.info("已删除截图：%s", real)
            return {"ok": True, "data": None}
        except OSError as e:
            return {"ok": False, "err": str(e)}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def shot_set_dir(self, path=None):
        """设置截图目录（传路径或弹文件夹选择框）。"""
        try:
            if path:
                path = str(path)
            else:
                if self._window is None:
                    return {"ok": False, "err": "窗口尚未就绪。"}
                result = self._window.create_file_dialog(webview.FOLDER_DIALOG, directory="")
                dirs = list(result or [])
                if not dirs:
                    return {"ok": False, "err": "未选择目录"}
                path = dirs[0]
            if not os.path.isdir(path):
                return {"ok": False, "err": "目录不存在：%s" % path}
            self.cfg.set("screenshot_dir", path)
            return {"ok": True, "data": path}
        except Exception as e:
            return {"ok": False, "err": str(e)}