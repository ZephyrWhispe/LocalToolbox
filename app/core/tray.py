"""系统托盘常驻（Windows）：pystray + Pillow 动态图标。

- 关闭主窗口时隐藏到托盘而非退出；
- 托盘菜单：显示主窗口 / 服务快捷开关子菜单（v5.4 O5，可选）/ 退出程序；
- 左键单击图标也显示主窗口；
- 菜单为动态构建：每次打开时重新求值（服务状态实时刷新）。

pystray 的 win32 后端在独立线程内自建消息循环，可在非主线程运行。
"""

import threading

from . import logger as applog

log = applog.get_logger("tray")

try:
    import pystray
    from PIL import Image, ImageDraw
    _AVAILABLE = True
except Exception:  # pragma: no cover - 环境缺库时降级为无托盘
    _AVAILABLE = False


def _make_icon():
    """生成 64x64 托盘图标：深蓝圆角底 + 白色双向箭头（与窗口 logo 一致）。"""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([4, 4, size - 4, size - 4], radius=14, fill=(15, 20, 25, 255))
    d.rounded_rectangle([4, 4, size - 4, size - 4], radius=14, outline=(76, 141, 255, 255), width=2)

    def arrow(x1, y1, x2, y2, head):
        # 主线
        d.line([(x1, y1), (x2, y2)], fill=(255, 255, 255, 255), width=5)
        # 箭头头部（两条短线）
        d.line([(x2, y2), (x2 - head, y2 - head // 2)], fill=(255, 255, 255, 255), width=5)
        d.line([(x2, y2), (x2 - head // 2, y2 + head)], fill=(255, 255, 255, 255), width=5)

    # 向右箭头（上）
    d.line([(16, 22), (40, 22)], fill=(255, 255, 255, 255), width=5)
    d.line([(40, 22), (32, 15)], fill=(255, 255, 255, 255), width=5)
    d.line([(40, 22), (32, 29)], fill=(255, 255, 255, 255), width=5)
    # 向左箭头（下）
    d.line([(48, 42), (24, 42)], fill=(76, 141, 255, 255), width=5)
    d.line([(24, 42), (32, 35)], fill=(76, 141, 255, 255), width=5)
    d.line([(24, 42), (32, 49)], fill=(76, 141, 255, 255), width=5)
    return img


def service_submenu(label, on_action, off_action, is_on):
    """v5.4 O5 服务快捷开关子菜单：启/停两项按 is_on() 互斥置灰。

    交互同构 openlist-desktop 托盘（Submenu + 互斥启用/禁用）；
    label 为服务名；on_action/off_action 为无参动作；is_on 为无参状态查询
    （托盘菜单动态重建时求值，保证与后端实时一致）。pystray 缺库返回 None。
    """
    if not _AVAILABLE:
        return None
    return pystray.MenuItem(label, pystray.Menu(
        pystray.MenuItem("开启", lambda icon=None, item=None: on_action(),
                         enabled=lambda item: not is_on()),
        pystray.MenuItem("停止", lambda icon=None, item=None: off_action(),
                         enabled=lambda item: is_on()),
    ))


class TrayController:
    def __init__(self, on_show=None, on_quit=None, title="LocalToolbox",
                 extra_menu=None):
        self.on_show = on_show or (lambda: None)
        self.on_quit = on_quit or (lambda: None)
        self.title = title
        # v5.4 O5：服务快捷开关子菜单（callable -> list[pystray.MenuItem]）；
        # 动态求值——pystray 每次打开菜单时调用，保证服务状态实时刷新。
        self._extra_menu = extra_menu
        self._icon = None
        self._thread = None
        self._quitting = False

    @property
    def available(self):
        return _AVAILABLE

    def _build_menu(self, icon=None):
        """菜单项列表（动态）：显示主窗口 + 服务子菜单（可选）+ 退出程序。"""
        if not _AVAILABLE:
            return []
        items = [pystray.MenuItem("显示主窗口", self._show, default=True)]
        if self._extra_menu is not None:
            try:
                extra = self._extra_menu() or []
                if extra:
                    items.append(pystray.Menu.SEPARATOR)
                    items.extend(extra)
            except Exception as e:
                log.warning("托盘服务子菜单构建失败: %s", e)
        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("退出程序", self._quit))
        return items

    def start(self):
        if not _AVAILABLE or self._icon is not None:
            if not _AVAILABLE:
                log.warning("pystray 不可用，托盘功能停用")
            return
        log.info("系统托盘已启动")
        menu = pystray.Menu(lambda icon=None: self._build_menu(icon))
        self._icon = pystray.Icon(
            "localtoolbox", _make_icon(), self.title, menu
        )
        self._thread = threading.Thread(target=self._icon.run, name="tray", daemon=True)
        self._thread.start()

    def _show(self, icon=None, item=None):
        try:
            self.on_show()
        except Exception:
            pass

    def _quit(self, icon=None, item=None):
        self._quitting = True
        log.info("托盘菜单退出程序")
        try:
            self.on_quit()
        finally:
            self.stop()

    @property
    def quitting(self):
        return self._quitting

    def notify(self, message, title=None):
        """托盘气泡通知（收到文件/配对请求时用）。"""
        if self._icon is not None:
            try:
                self._icon.notify(str(message), str(title or self.title))
            except Exception:
                pass

    def stop(self):
        icon, self._icon = self._icon, None
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass
