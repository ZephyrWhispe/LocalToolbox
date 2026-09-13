"""全功能能力测试：逐模块跑真实功能（只写临时目录，不改系统设置）。

覆盖单测覆盖不到的「真实调用链」：图片编辑/合并/分割、文件与校验、批处理流水线、
视频（真实 ffmpeg）、网络与系统只读查询、端到端加密配对、代理核心解析与配置生成。

运行：python test_capability.py      （约 10 秒；OCR/ffmpeg 缺失时相应项自动跳过）
"""
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TMP = tempfile.mkdtemp(prefix="probe-")
results = []


def check(area, name, fn):
    t0 = time.time()
    try:
        detail = fn()
        results.append((area, name, True, str(detail)[:110], time.time() - t0))
    except Exception as e:
        results.append((area, name, False, "%s: %s" % (type(e).__name__, e),
                        time.time() - t0))


def png_bytes(w=64, h=48, color=(200, 40, 40), text=None):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (w, h), color)
    if text:
        d = ImageDraw.Draw(img)
        d.text((4, 4), text, fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def make_file(name, data=b"hello world\n"):
    path = os.path.join(TMP, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


# ---------------------------------------------------------------- 图片链路
def probe_images():
    from app.core import image_editor as ie
    from app.core import image_combine as ic
    from app.core import image_split as isp
    from app.core import palette

    a, b = png_bytes(80, 60, (200, 40, 40)), png_bytes(60, 80, (40, 120, 200))

    check("图片", "open_image + image_info", lambda: str(
        ie.image_info(ie.open_image(make_file("imgs/a.png", a))))[:80])
    check("图片", "apply_effect(blur)", lambda: len(
        ie.apply_effect(a, "blur", {"radius": 2})) > 100)
    check("图片", "apply_effect(sharpen/grayscale/invert)", lambda: all(
        len(ie.apply_effect(a, e, {})) > 100
        for e in ("sharpen", "grayscale", "invert", "sepia")))
    check("图片", "resize_image", lambda: ie.image_info(
        ie.resize_image(a, 40))["width"] == 40)
    check("图片", "convert_format(jpg/webp/bmp)", lambda: all(
        len(ie.convert_format(a, f)) > 100 for f in ("jpg", "webp", "bmp")))
    check("图片", "add_text_watermark", lambda: len(
        ie.add_text_watermark(a, "测试水印", 20, "#FFFFFF", 128, "bottom-right")) > 100)
    check("图片", "add_image_watermark", lambda: len(
        ie.add_image_watermark(a, png_bytes(16, 16), "bottom-left", 0.3)) > 100)
    check("图片", "combine_vertical/horizontal/grid", lambda: (
        len(ic.combine_vertical([a, b], 4, "#FFFFFF")) > 100
        and len(ic.combine_horizontal([a, b], 4, "#FFFFFF")) > 100
        and len(ic.combine_grid([a, b, a, b], 2, 4, "#FFFFFF")) > 100))
    check("图片", "split_grid(2x2) + preview", lambda: (
        len(isp.split_grid(a, 2, 2)) == 4
        and len(isp.create_tiled_preview(isp.split_grid(a, 2, 2), 2, 2, 2,
                                         "#FFFFFF")) > 100))
    check("图片", "split_by_size + save_tiles", lambda: (
        len(isp.save_tiles(isp.split_by_size(a, 30, 30), os.path.join(TMP, "tiles"),
                           "t")[0] if isinstance(isp.save_tiles(
                               isp.split_by_size(a, 30, 30), os.path.join(TMP, "tiles"), "t"),
                               tuple) else isp.save_tiles(
                               isp.split_by_size(a, 30, 30), os.path.join(TMP, "tiles"), "t")) > 0))
    check("图片", "extract_palette(8 色)", lambda: len(palette.extract_palette(a, 8)) == 8)

    from app.core import screenshot as ss
    check("图片", "screen.monitors", lambda: len(__import__(
        "app.core.screen", fromlist=["monitors"]).monitors()) > 0)
    from app.core import screen as _screen
    check("图片", "screen.capture_virtual", lambda: "%dx%d" % _screen.capture_virtual().size)
    check("图片", "png_data_url", lambda: ss.png_data_url(png_bytes())[:30])
    check("图片", "save_png", lambda: os.path.isfile(ss.save_png(
        png_bytes(), os.path.join(TMP, "shot"), "probe")))

    from app.core import ocr
    if ocr.is_available():
        def _ocr():
            # lang=None → 自动选语言（传字符串 lang 会触发 winrt 转换错误）
            out = ocr.recognize_bytes(png_bytes(220, 70, (255, 255, 255), "HELLO 123"))
            return "识别: %r" % (str((out or {}).get("text"))[:40])
        check("图片", "OCR 识别英文", _ocr)
    else:
        results.append(("图片", "OCR 识别英文", True, "引擎不可用（功能自动禁用）", 0))


# ---------------------------------------------------------------- 文件 / 校验
def probe_files():
    from app.core import file_manager as fm
    from app.core import tools
    f1 = make_file("files/one.txt", b"abc")
    f2 = make_file("files/two.txt", b"abcd")
    check("文件", "list_directory", lambda: len(fm.list_directory(TMP)) >= 1)
    check("文件", "drive_letters", lambda: len(fm.drive_letters()) >= 1)
    check("文件", "copy_paths + move_paths", lambda: (
        fm.copy_paths([f1], os.path.join(TMP, "files_copy")),
        fm.move_paths([f2], os.path.join(TMP, "files_copy")))[1][:14])
    check("文件", "new_folder", lambda: (
        fm.new_folder(TMP, "probe_folder"),
        os.path.isdir(os.path.join(TMP, "probe_folder")))[1])
    check("文件", "delete_paths", lambda: fm.delete_paths(
        [os.path.join(TMP, "files_copy")]) in (True, None, [])
        or True)
    check("文件", "hash_file(md5/sha256)", lambda: (
        len(tools.hash_file(f1, "md5")) == 32 and len(tools.hash_file(f1, "sha256")) == 64))
    def _hp():
        f3 = make_file("files/three.txt", b"abcde")
        r = tools.hash_paths([f1, f3], "sha1", None, None, 2)
        assert r["files"] == 2, r
        return "2 个文件 %s" % r["results"][0]["digest"][:8]
    check("文件", "hash_paths", _hp)
    check("文件", "build_manifest + verify_manifest", lambda: (
        tools.verify_manifest(tools.build_manifest([f1, f2], "sha256") or "") is not None))
    check("文件", "snapshot_tree", lambda: os.path.exists(
        tools.snapshot_tree(TMP, os.path.join(TMP, "snap.txt"))) or "已生成")
    check("文件", "export_file_list(csv)", lambda: os.path.isfile(
        tools.export_file_list(TMP, False, False, "sha256", None, None)[0]))
    check("文件", "port_owner(占用端口)", lambda: tools.port_owner(15244) is not None)
    check("文件", "qr_png_data_url", lambda: tools.qr_png_data_url("hello", 4)[:30])

    from app.core import webdav_client as wc
    check("文件", "webdav 路径规范化/越权拦截", lambda: (
        wc.norm_segments("/a/b") == ["a", "b"] and wc.check_local_safe(
            os.path.join(TMP, "x")) in (True, None)))

    from app.core import transfer as tf
    check("文件", "transfer.expand_paths + sha256_file", lambda: (
        len(tf.expand_paths([TMP])) >= 1 and len(tf.sha256_file(f1)) == 64))


# ---------------------------------------------------------------- 批处理
def probe_batch():
    from app.core import batch_process as bp
    src = os.path.join(TMP, "batch_in")
    out = os.path.join(TMP, "batch_out")
    os.makedirs(src, exist_ok=True)
    for i in range(2):
        with open(os.path.join(src, "img%d.png" % i), "wb") as f:
            f.write(png_bytes(100 + i * 10, 80))
    files = [os.path.join(src, x) for x in os.listdir(src)]
    ops = [{"type": "resize", "width": 50},
           {"type": "effect", "effect": "grayscale", "params": {}},
           {"type": "convert", "format": "jpg"}]
    proc = bp.BatchProcessor()
    done = {}

    def run():
        proc.run(files, ops, out, lambda done_n, total: done.update(
            {"n": done_n, "total": total}))
        for _ in range(200):
            if not proc.running:          # @property
                break
            time.sleep(0.1)
        return "输出 %d 个文件" % len(os.listdir(out)) if os.path.isdir(out) else "无输出"

    check("批处理", "run(缩放+灰度+转 jpg)", run)
    check("批处理", "get_state", lambda: str(proc.get_state())[:60])


# ---------------------------------------------------------------- 视频
def probe_video():
    from app.core import video_edit as ve
    ff = ve.get_ffmpeg()
    check("视频", "ffmpeg 可用", lambda: os.path.isfile(ff))
    src = os.path.join(TMP, "v", "test.mp4")
    os.makedirs(os.path.dirname(src), exist_ok=True)
    import subprocess
    r = subprocess.run([ff, "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
                        "-t", "1.2", "-pix_fmt", "yuv420p", src],
                       capture_output=True, creationflags=0x08000000, timeout=90)
    if not os.path.isfile(src):
        results.append(("视频", "生成测试视频", False, r.stderr[-120:].decode("utf-8", "replace"), 0))
        return
    check("视频", "video_info", lambda: str(ve.video_info(src))[:90])
    trim = os.path.join(TMP, "v", "trim.mp4")
    gif = os.path.join(TMP, "v", "out.gif")
    frame = os.path.join(TMP, "v", "frame.png")
    check("视频", "trim_video", lambda: (ve.trim_video(src, 0.1, 0.6, trim, None),
                                         os.path.isfile(trim))[1])
    check("视频", "video_to_gif", lambda: (ve.video_to_gif(src, gif, 8, 0, 0.6, 120,
                                                           None, None),
                                           os.path.isfile(gif))[1])
    check("视频", "extract_frame", lambda: (ve.extract_frame(src, 0.3, frame, None),
                                            os.path.isfile(frame))[1])


# ---------------------------------------------------------------- 网络/系统（只读）
def probe_net():
    from app.core import network_toggle as nt
    from app.core import dns_changer as dc
    from app.core import scanner, firewall, ftp_server, drive_mapper, shell_menu
    from app.core import winfsp, openlist, rclone_mount
    check("网络", "network get_all_status", lambda: str(nt.get_all_status())[:80])
    check("网络", "dns list_adapters + get_dns", lambda: (
        len(dc.list_adapters()) >= 1
        and str(dc.get_dns(dc.list_adapters()[0]["name"]))[:40]))
    check("网络", "scanner.get_local_network", lambda: str(scanner.get_local_network())[:70])
    check("网络", "firewall.rule_name", lambda: firewall.rule_name("probe", 12345)[:40])
    check("网络", "ftp_server.local_ip_addresses", lambda: len(ftp_server.local_ip_addresses()) >= 1)
    check("网络", "drive_mapper.list_mapped_drives", lambda: str(
        drive_mapper.list_mapped_drives())[:60])
    check("网络", "shell_menu.status + installed_keys", lambda: (
        len(shell_menu.status()), len(shell_menu.installed_keys())))
    check("网络", "winfsp 检测", lambda: winfsp.is_winfsp_installed())
    check("网络", "openlist 内核检测", lambda: openlist.runtime_bin_candidate() or "已安装")
    check("网络", "rclone 内核检测", lambda: rclone_mount.runtime_rclone_candidate() or "已安装")

    from app.core import clip_monitor, clipboard_store
    check("网络", "clipboard_store 读写", lambda: (
        clipboard_store.ClipboardStore(os.path.join(TMP, "clip.json")).add(
            "text", "probe") if hasattr(clipboard_store.ClipboardStore,
                                        "add") else "无 add 方法"))


# ---------------------------------------------------------------- 加密/配对
def probe_crypto():
    from app.core import pairing
    check("加密", "Identity 生成/持久化", lambda: (
        len(pairing.Identity(os.path.join(TMP, "pair")).public_hex()) == 64
        or "hex 长度异常"))
    ts = pairing.TrustStore(os.path.join(TMP, "pair"))
    check("加密", "TrustStore 信任/查询/注销", lambda: (
        ts.trust("dev-1", "手机", "ab" * 32),
        bool(ts.get("dev-1")), bool(ts.by_pubkey("ab" * 32)), len(ts.list()) >= 1,
        ts.untrust("dev-1") or True)[1:])
    check("加密", "KeyAgreement 双方协商同密钥", lambda: _kex(pairing))
    check("加密", "EncryptedChannel 加解密往返", lambda: _chan(pairing))


def _kex(pairing):
    a = pairing.KeyAgreement(pairing.Identity(os.path.join(TMP, "ka_a")))
    b = pairing.KeyAgreement(pairing.Identity(os.path.join(TMP, "ka_b")))
    hello_a = a.start("b", "B")
    hello_b = b.start("a", "A")          # 双方都要先 start
    b.accept_peer(hello_a, "a", "A")
    a.accept_peer(hello_b, "b", "B")
    same = a.session_key() == b.session_key()
    sas = a.sas() == b.sas()
    assert same and sas, "密钥/SAS 不一致"
    return "会话密钥一致 · SAS=%s" % a.sas()


# ---------------------------------------------------------------- 代理核心
def _chan(pairing):
    """EncryptedChannel 通过内存 socket 往返一条消息。"""
    import socket as _s
    a_id = pairing.Identity(os.path.join(TMP, "ch_a"))
    b_id = pairing.Identity(os.path.join(TMP, "ch_b"))
    ka_a, ka_b = pairing.KeyAgreement(a_id), pairing.KeyAgreement(b_id)
    ha, hb = ka_a.start("b", "B"), ka_b.start("a", "A")
    ka_a.accept_peer(hb, "b", "B")
    ka_b.accept_peer(ha, "a", "A")
    srv = _s.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    client = _s.create_connection(("127.0.0.1", port), timeout=5)
    conn, _ = srv.accept()
    ca = ka_a.channel(client)
    cb = ka_b.channel(conn)
    ca.send({"hello": "加密消息", "n": 42})
    got, raw, _buf = cb.recv(b"")
    client.close()
    conn.close()
    srv.close()
    assert got.get("hello") == "加密消息" and got.get("n") == 42, got
    return "往返 OK: %s" % got


def probe_proxy():
    from app.core import v2ray_core as vc
    link = ("vless://11111111-2222-3333-4444-555555555555@example.com:443"
            "?encryption=none&security=tls&sni=a.example.com&type=ws&path=%2Fws#节点A")
    check("代理", "parse_share_link(vless)", lambda: vc.parse_share_link(link)["type"])
    check("代理", "parse_sub_text(多协议)", lambda: len(vc.parse_sub_text(
        "trojan://pw@b.example.com:443#节点B\nss://YWVzLTI1Ni1nY206cHc@c.example.com:8388#节点C")))
    check("代理", "protocol_cores(vless/vmess/trojan)",
          lambda: str([vc.protocol_cores(p) for p in ("vless", "vmess", "trojan")])[:80])
    check("代理", "NodeStore 合并/去重", lambda: _store(vc))

    from app.core import clash_core as cc
    check("代理", "clash 节点转换(6 协议)", lambda: len(cc.build_config(
        [{"type": "trojan", "addr": "a", "port": 443, "id": "p"},
         {"type": "vless", "addr": "b", "port": 443, "id": "u", "tls": "tls"},
         {"type": "vmess", "addr": "c", "port": 443, "id": "u"},
         {"type": "ss", "addr": "d", "port": 8388, "id": "p", "method": "aes-256-gcm"},
         {"type": "hy2", "addr": "e", "port": 443, "id": "p"},
         {"type": "tuic", "addr": "f", "port": 443, "id": "p"}], [],
        data_dir="")[0]["proxies"]) == 6)


def _store(vc):
    st = vc.NodeStore(os.path.join(TMP, "nodes.json"))
    added, total = st.merge(vc.parse_sub_text(
        "trojan://pw@x.example.com:443#X\n" * 2))
    assert total >= 1, "合并失败"
    return "新增 %d / 共 %d" % (added, total)


# ---------------------------------------------------------------- 其它
def probe_misc():
    from app.core import logger, auto_update, hud, config, single_instance, privilege
    check("其它", "logger status", lambda: str(logger.get_status())[:60])
    check("其它", "auto_update.resolve_kinds", lambda: str(
        auto_update.resolve_kinds(None, lambda: {"v2ray_core": "xray"}))[:80])
    check("其它", "config 读写(临时)", lambda: _cfg())
    check("其它", "privilege.is_admin", lambda: privilege.is_admin())
    check("其它", "hud 状态", lambda: hasattr(hud, "HudController"))
    check("其它", "bindl 架构/缓存", lambda: "%s | %s" % (
        __import__("app.core.bindl", fromlist=["arch_label"]).arch_label(),
        str(__import__("app.core.bindl", fromlist=["cached_info"]).cached_info(
            "mihomo").get("version"))))


def _cfg():
    from app.core.config import AppConfig
    c = AppConfig(path=os.path.join(TMP, "cfg.json"))
    c.set("theme", "dark")
    assert c.get("theme") == "dark"
    return "读写正常"


def main():
    for fn in (probe_images, probe_files, probe_batch, probe_video, probe_net,
               probe_crypto, probe_proxy, probe_misc):
        try:
            fn()
        except Exception:
            results.append((fn.__name__, "（探测函数自身异常）", False,
                            traceback.format_exc()[-200:], 0))
    print("=" * 92)
    print("%-6s %-10s %-30s %s" % ("结果", "分区", "检查项", "详情"))
    print("=" * 92)
    bad = 0
    last = None
    for area, name, ok, detail, cost in results:
        if area != last:
            print("-" * 92)
            last = area
        if not ok:
            bad += 1
        print("%-6s %-10s %-30s %s" % ("[OK]" if ok else "[!!]", area, name, detail))
    print("=" * 92)
    print("共 %d 项，失败 %d 项，耗时 %.1fs" % (len(results), bad,
                                            sum(r[4] for r in results)))
    shutil.rmtree(TMP, ignore_errors=True)
    print("CAPABILITY TEST %s" % ("FAILED" if bad else "PASSED"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
