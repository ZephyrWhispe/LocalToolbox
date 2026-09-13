"""UI 能力探测与窗口材质（v5.3）。

设计：前端所有新视觉都"能力驱动" —— 启动早期调 `cap_get` 拿到本机支持项，
不支持的部分自动走原有路径。这是"改造不影响功能使用"的技术底座：
老系统/老 WebView2 上表现与改造前完全一致，不需要分支代码。

材质模式的运行时切换见 `material_set`：窗口是否透明在创建时即已确定，
因此从"不透明"切到"模糊/Mica"需要重启主窗口（返回值里的 needs_restart 会说明）。
"""

from ..core import win_shell

MATERIAL_MODES = ("auto", "off", "blur", "mica")


class CapApi:
    def _init_cap(self):
        # 由 main.py 在窗口 loaded 后回填真实结果（apply 的返回值）
        if not hasattr(self, "_material_state"):
            self._material_state = {"applied": "none", "mode": "auto", "reason": "尚未应用"}
        if not hasattr(self, "_win_transparent"):
            self._win_transparent = False

    # -- 供 main.py 回填（非 js_api） -----------------------------------
    def set_material_state(self, st):
        if isinstance(st, dict):
            self._material_state = dict(st)

    def set_win_transparent(self, flag):
        self._win_transparent = bool(flag)

    # -- API -----------------------------------------------------------
    def cap_get(self):
        """本机 UI 能力（前端 boot 早期调用，据此决定启用哪些新视觉）。"""
        cfg = getattr(self, "cfg", None)
        mode = "auto"
        if cfg is not None:
            m = str(cfg.get("ui_material") or "auto").strip().lower()
            mode = m if m in MATERIAL_MODES else "auto"
        p = win_shell.probe()
        st = dict(self._material_state)
        return {"ok": True, "data": {
            "os": {"build": p["build"], "win11": p["build"] >= 22000},
            "material": {
                "mode": mode,
                "supported": bool(p["mica"] or p["blur"]),
                "applied": st.get("applied", "none"),
                "reason": st.get("reason", ""),
            },
            "window": {"transparent": bool(self._win_transparent)},
            "remote": p["remote"],
        }}

    def material_set(self, mode):
        """切换材质模式：写配置 + 立即尝试应用（透明窗口已就绪时即时生效）。"""
        mode = str(mode or "").strip().lower()
        if mode not in MATERIAL_MODES:
            return {"ok": False, "err": "材质模式需为 auto / off / blur / mica"}
        cfg = getattr(self, "cfg", None)
        if cfg is not None:
            cfg.set("ui_material", mode)
        dark = True
        if cfg is not None:
            dark = str(cfg.get("theme") or "dark") != "light"
        st = win_shell.apply(win_shell.hwnd_of(getattr(self, "_window", None)), mode, dark=dark)
        st["needs_restart"] = not self._win_transparent
        self._material_state = st
        self.emit("material_state", st)
        return {"ok": True, "data": st}

    def material_state(self):
        """当前材质状态（设置页显示"当前材质 + 原因"）。"""
        st = dict(self._material_state)
        st["build"] = win_shell.build_no()
        st["transparent"] = bool(self._win_transparent)
        return {"ok": True, "data": st}
