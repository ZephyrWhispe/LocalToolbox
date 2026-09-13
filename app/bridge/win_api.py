"""主窗口控制（无边框 UI）：最小化 / 最大化切换 / 关闭到托盘 / 拖动移动。

主窗口改用 frameless（去掉系统标题栏/外框，消除浅色主题下的白色窗口边框），
窗口控制由自绘标题栏按钮触发，全部经 pywebview Window API 实现。
"""


class WindowApi:
    def _init_win(self):
        # 托盘通知回调（main.py 经 set_tray_notify 注入，兜底 None）
        if not hasattr(self, "_tray_notify"):
            self._tray_notify = None

    def open_external(self, url):
        """用系统默认浏览器打开链接。

        WebView2 会在宿主侧拦截 window.open（点「打开 Web 界面」没反应），
        统一改走这里。
        """
        url = str(url or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            return {"ok": False, "err": "只支持 http/https 链接"}
        try:
            import webbrowser
            webbrowser.open(url)
            return {"ok": True, "data": url}
        except Exception as e:
            return {"ok": False, "err": "打开浏览器失败：%s" % e}

    # -- API -----------------------------------------------------------
    def win_minimize(self):
        try:
            if self._window is not None:
                self._window.minimize()
            return {"ok": True, "data": None}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def win_toggle_max(self):
        """最大化 / 还原切换。返回 {"maximized": bool}。"""
        try:
            w = self._window
            if w is None:
                return {"ok": False, "err": "窗口尚未就绪"}
            st = str(getattr(w, "state", "") or "")
            if "maximized" in st:
                w.restore()
                return {"ok": True, "data": {"maximized": False}}
            w.maximize()
            return {"ok": True, "data": {"maximized": True}}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def win_hide_to_tray(self):
        """关闭按钮：默认隐藏到托盘；配置 tray_close_exit 时直接退出（v3.5d）。"""
        try:
            cfg = getattr(self, "cfg", None)
            if cfg is not None and bool(cfg.get("tray_close_exit", False)):
                quit_cb = getattr(self, "_tray_quit", None)
                if callable(quit_cb):
                    quit_cb()  # 走托盘统一退出链路（quitting 置位 → 窗口销毁）
                    return {"ok": True, "data": {"exit": True}}
            if self._window is not None:
                self._window.hide()
            try:
                if callable(self._tray_notify):
                    self._tray_notify(
                        "LocalToolbox",
                        "程序仍在后台运行。\n右键托盘图标可选择「退出程序」。")
            except Exception:
                pass
            return {"ok": True, "data": None}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def win_set_on_top(self, on):
        """主窗口置顶切换（v3.5e）：SetWindowPos HWND_TOPMOST / HWND_NOTOPMOST。"""
        try:
            on = bool(on)
            native = getattr(self._window, "native", None) if self._window else None
            h = getattr(native, "Handle", None)
            hwnd = h if isinstance(h, int) else int(h.ToInt64()) if h else 0
            if not hwnd:
                return {"ok": False, "err": "窗口尚未就绪"}
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            user32.SetWindowPos.argtypes = [
                wintypes.HWND, wintypes.HWND,
                wintypes.INT, wintypes.INT, wintypes.INT, wintypes.INT,
                wintypes.UINT,
            ]
            # SWP_NOMOVE | SWP_NOSIZE
            user32.SetWindowPos(
                wintypes.HWND(hwnd),
                wintypes.HWND(-1 if on else -2),  # HWND_TOPMOST / HWND_NOTOPMOST
                0, 0, 0, 0, 0x3,
            )
            return {"ok": True, "data": on}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def win_begin_drag(self):
        """开始拖动窗口：把事件交给系统标题栏拖拽逻辑（HTCAPTION）。

        由系统完成移动与坐标换算，彻底避免自算增量造成的抖动/乱飘。
        此调用会阻塞到鼠标释放（拖动期间由系统接管）。
        """
        try:
            import ctypes
            from ctypes import wintypes
            native = getattr(self._window, "native", None)
            h = getattr(native, "Handle", None)
            hwnd = h if isinstance(h, int) else int(h.ToInt64()) if h else 0
            if not hwnd:
                return {"ok": False, "err": "窗口尚未就绪"}
            user32 = ctypes.windll.user32
            # 竞态防护：桥接调用是异步的，快速点击时到这里左键可能已抬起。
            # 若此时仍进入 WM_NCLBUTTONDOWN 模态拖拽循环，窗口会"粘住"鼠标直到下次点击。
            # 仅当左键确实按住时才交给系统拖拽，否则直接返回。
            VK_LBUTTON = 0x01
            if not (user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000):
                return {"ok": True, "data": None}
            user32.ReleaseCapture()
            user32.SendMessageW(wintypes.HWND(hwnd), 0xA1, 2, 0)  # WM_NCLBUTTONDOWN/HTCAPTION
            return {"ok": True, "data": None}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def win_move_by(self, dx, dy):
        """按增量移动窗口（保留接口；正常拖动走 win_begin_drag）。"""
        try:
            w = self._window
            if w is None:
                return {"ok": False, "err": "窗口尚未就绪"}
            try:
                dx = int(dx or 0)
                dy = int(dy or 0)
            except (TypeError, ValueError):
                return {"ok": False, "err": "增量需为数字"}
            if not dx and not dy:
                return {"ok": True, "data": None}
            w.move((w.x or 0) + dx, (w.y or 0) + dy)
            return {"ok": True, "data": None}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def win_resize_by(self, dw, dh):
        """按增量调整窗口大小（v5.3，与 win_move_by 配对用于边缘缩放）。

        背景：主窗口是 frameless（FormBorderStyle.None），WinForms 不提供缩放
        边框，**用户此前无法拖边调整窗口大小**。这里不走 WM_NCLBUTTONDOWN
        （那条路在 WebView2 上因鼠标捕获不在本进程而失效，见 win_begin_drag 注释），
        而是复用 pywebview 自身 easy_drag 已验证可行的路径：前端逐次 mousemove
        调桥接、后端用 pywebview 的 window.resize/move 落地。用增量而非绝对坐标，
        绕开 DPI 与 pywebview 坐标系的换算差异。

        注意：本方法按帧调用（拖动时每秒数十次），故 **不要**把 "win_" 加入
        app/bridge/base.py 的 _JS_API_PREFIXES 自动日志白名单，否则刷屏。
        """
        try:
            w = self._window
            if w is None:
                return {"ok": False, "err": "窗口尚未就绪"}
            try:
                dw = int(dw or 0)
                dh = int(dh or 0)
            except (TypeError, ValueError):
                return {"ok": False, "err": "增量需为数字"}
            if not dw and not dh:
                return {"ok": True, "data": None}
            nw = max(680, int(w.width or 0) + dw)
            nh = max(460, int(w.height or 0) + dh)
            w.resize(nw, nh)
            return {"ok": True, "data": {"width": nw, "height": nh}}
        except Exception as e:
            return {"ok": False, "err": str(e)}
