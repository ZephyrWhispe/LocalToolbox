"""键鼠共享页桥接：被控端监听 / 控制端连接 / 允许被控制开关 / 状态推送。

旧 Qt 页用 1.5s QTimer 轮询刷新状态；Web 版改为后台线程轮询快照，
状态变化时通过 ``km_state`` 事件推送（可捕获热键切换、后台断线等
非本线程发起的状态变化）。
"""

import json
import threading
import time

from ..core import firewall
from ..core.km_share import KM_PORT_DEFAULT, KmController, KmTarget, set_cursor_pos

FW_PREFIX = "KmHelper-"
KM_POLL_INTERVAL = 1.5


class KmApi:
    def _init_km(self):
        self._km_fw_open = False
        cfg = self.cfg.data
        self._km_edges = {
            "left": str(cfg.get("km_edges", {}).get("left") or ""),
            "right": str(cfg.get("km_edges", {}).get("right") or ""),
            "top": str(cfg.get("km_edges", {}).get("top") or ""),
            "bottom": str(cfg.get("km_edges", {}).get("bottom") or ""),
        }
        self._km_target = KmTarget(
            discovery=self.discovery, log=lambda m: self.emit("km_log", str(m))
        )
        self._km_target.enabled = bool(cfg.get("km_allow", True))
        self._km_target.set_lock_input(bool(cfg.get("km_lock_input", False)))
        self._km_controller = KmController(log=lambda m: self.emit("km_log", str(m)))
        self._km_controller.edge_margin = int(cfg.get("km_edge_margin", 4))
        self._km_controller.on_edge = self._km_edge_hit
        self._km_controller.on_edge_back = self._km_edge_back
        self._km_last_state = None
        threading.Thread(target=self._km_poll_loop, name="km-state", daemon=True).start()

    def _km_edge_hit(self, direction, frac=0.0):
        """鼠标滑出本机屏幕边缘 → 按邻居表切换控制目标（T-03/T-04）。

        v5.1b：本回调在低级鼠标钩子线程内触发，attach() 含最长 3s 网络
        I/O——必须移入工作线程，否则全系统鼠标停顿且钩子可能被系统摘除。
        """
        if not self.cfg.data.get("km_edge_switch", True):
            return
        ip = self._km_edges.get(direction, "") or ""
        if not ip:
            return
        threading.Thread(target=self._km_edge_switch, args=(ip, direction, frac),
                         daemon=True, name="km-edge").start()

    def _km_edge_switch(self, ip, direction, frac=0.0):
        """边缘切换的实际执行（工作线程）。"""
        c = self._km_controller
        try:
            if ip not in c._targets:
                c.attach(ip)
            c.set_active(ip)
            if not c.active:
                c.set_control(True)
            c.set_entry(direction, frac)
            self.emit("km_log", f"边缘穿越（{direction}）→ 已控制 {c.peer}")
            self._km_push_state()
        except Exception as e:
            self.emit("km_log", f"边缘切换失败：{e}")

    def _km_edge_back(self, direction, frac=0.0):
        """控制中远端光标滑回入口对侧边 → 切回本机。

        释放控制（目标保持挂载，可再穿）→ 本机光标归位到出口边 frac 处 →
        置 _edge_fired 防止立即重穿。
        """
        c = self._km_controller
        c._edge_fired = True  # 先武装防重穿：释放瞬间钩子可能立刻收到边缘事件
        c.set_control(False)
        # 本机光标归位：放在出口边 direction 的 frac 处（缩进 margin 像素防重触）
        try:
            from ..core.km_share import _virtual_screen
            x0, y0, w, h = _virtual_screen()
            m = c.edge_margin + 2
            if direction == "left":
                x, y = x0 + m, y0 + int(frac * h)
            elif direction == "right":
                x, y = x0 + w - 1 - m, y0 + int(frac * h)
            elif direction == "top":
                x, y = x0 + int(frac * w), y0 + m
            else:
                x, y = x0 + int(frac * w), y0 + h - 1 - m
            set_cursor_pos(x, y)
        except Exception:
            pass
        c._edge_fired = True

    # -- 状态快照 -------------------------------------------------------
    def _km_snapshot(self):
        t, c = self._km_target, self._km_controller
        devices = []
        if not c.connected and t.discovery is not None:
            devices = [
                {"name": d.get("name", ""), "ip": d.get("ip", ""), "km_port": d.get("km_port")}
                for d in t.discovery.devices()
                if d.get("km_port")
            ]
        return {
            "target": {
                "running": t.running,
                "controlled": t.controlled,
                "controller_name": t.controller_name,
                "port": t.port,
                "allow": t.enabled,
            },
            "controller": {
                "connected": c.connected,
                "active": c.active,
                "peer": c.peer,
                "active_ip": c.active_ip,
                "targets": c.targets_list(),
            },
            "edges": dict(self._km_edges),
            "options": {
                "edge_switch": bool(self.cfg.data.get("km_edge_switch", True)),
                "edge_margin": int(self.cfg.data.get("km_edge_margin", 4)),
                "lock_input": bool(self.cfg.data.get("km_lock_input", False)),
                "allow": bool(self.cfg.data.get("km_allow", True)),
            },
            "devices": devices,
        }

    def _km_push_state(self):
        snap = self._km_snapshot()
        self._km_last_state = json.dumps(snap, sort_keys=True, ensure_ascii=False)
        self.emit("km_state", snap)

    def _km_poll_loop(self):
        while True:
            time.sleep(KM_POLL_INTERVAL)
            try:
                snap = self._km_snapshot()
                key = json.dumps(snap, sort_keys=True, ensure_ascii=False)
                if key != self._km_last_state:
                    self._km_last_state = key
                    self.emit("km_state", snap)
            except Exception:
                continue

    # -- API ------------------------------------------------------------
    def km_get_state(self):
        return {"ok": True, "data": self._km_snapshot()}

    def km_set_allow(self, enabled):
        """「允许被控制」总开关（v4.7 起持久化到 cfg，启动时自动恢复）。"""
        r = self.cfg_set("km_allow", enabled)
        self._km_push_state()
        return r

    def km_start_listen(self, fw=True):
        t = self._km_target
        if fw:
            ok, msg = firewall.add(FW_PREFIX, t.port)
            if ok:
                self._km_fw_open = True
                self.emit("km_log", "已放行防火墙入站端口 %d（TCP）" % t.port)
            else:
                self.emit("km_log", "防火墙放行失败：" + ((msg or "").strip() or "未授权或命令出错"))
        t.start()
        self._km_push_state()
        return {"ok": True, "data": t.running}

    def km_stop_listen(self):
        t = self._km_target
        t.stop()
        # 停止监听时回收本应用打开的防火墙规则（净退出）
        if self._km_fw_open:
            from ..core import firewall
            ok, msg = firewall.remove(FW_PREFIX, t.port)
            if ok:
                self._km_fw_open = False
                self.emit("km_log", "已回收防火墙入站规则（端口 %d）" % t.port)
            else:
                self.emit("km_log", "防火墙规则回收失败：%s" % ((msg or "").strip() or "未授权"))
        self._km_push_state()
        return {"ok": True, "data": True}

    def _km_autostart(self):
        """v5.1b：按 cfg 随应用自启被控端监听（不自动放防火墙，避免开机 UAC）。"""
        try:
            if not self.cfg.get("km_allow", True):
                return
            t = self._km_target
            if t.running:
                return
            t.start()
            if t.running:
                self.emit("km_log", "被控端监听已随应用自动开启（端口 %d）。"
                          "如需局域网连接，请到键鼠共享页放行防火墙。" % t.port)
                self._km_push_state()
        except Exception as e:
            self.emit("km_log", "被控端监听自启失败：%s" % e)

    def km_connect(self, ip, port=None):
        try:
            port = int(port) if port else KM_PORT_DEFAULT
            self._km_controller.connect(str(ip).strip(), port)
            self._km_push_state()
            return {"ok": True, "data": True}
        except Exception as e:
            return {"ok": False, "err": "连接失败：%s" % e}

    def km_disconnect(self):
        self._km_controller.disconnect()
        self._km_push_state()
        return {"ok": True, "data": True}

    def km_toggle_control(self):
        c = self._km_controller
        c.toggle()
        self._km_push_state()
        return {"ok": True, "data": c.active}

    # -- 多目标直连拓扑（T-04） ----------------------------------------
    def km_attach(self, ip, port=None):
        """额外挂载一个被控端目标（保留既有目标）。"""
        try:
            port = int(port) if port else KM_PORT_DEFAULT
            self._km_controller.attach(str(ip).strip(), port, activate=False)
            self._km_push_state()
            return {"ok": True, "data": True}
        except Exception as e:
            return {"ok": False, "err": "连接失败：%s" % e}

    def km_detach(self, ip):
        self._km_controller.detach(str(ip or ""))
        self._km_push_state()
        return {"ok": True, "data": True}

    def km_set_active(self, ip):
        """手动切换当前控制目标（也可通过边缘滑动）。"""
        self._km_controller.set_active(str(ip or ""))
        self._km_push_state()
        return {"ok": True, "data": True}

    # -- 边缘邻居配置（T-03，v4.7 起持久化） ----------------------------
    def km_edge_set(self, direction, ip):
        direction = str(direction or "").strip()
        if direction not in self._km_edges:
            return {"ok": False, "err": "方向须为 left/right/top/bottom"}
        self._km_edges[direction] = str(ip or "").strip()
        self.cfg.set("km_edges", dict(self._km_edges))
        self._km_push_state()
        return {"ok": True, "data": self._km_edges[direction]}
