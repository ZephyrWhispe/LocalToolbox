"""提权脚本一致性测试。

背景：`run_elevated()` 会把命令写进 **.bat** 交给 cmd 执行，而
`run_powershell_elevated()` 才包一层 powershell。曾经 firewall / web_server
把 PowerShell 语法（`| Out-Null`、`Set-ItemProperty`）传给了 cmd 版本 →
防火墙放行静默失败（局域网访问 FTP / Web / 共享 / OpenList 实际不可用）。

本文件检查：① 相关函数用的是 PowerShell 提权执行器；② 生成的脚本本身是
合法的 PowerShell（用 PowerShell 自己的解析器校验，不提权、不改系统）；
③ 全项目静态扫描：不把 PowerShell 语法塞给 run_elevated。

运行：python test_elevated_scripts.py
"""
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from app.core import firewall, web_server  # noqa: E402


def ps_parse_errors(script_text):
    """用 PowerShell 解析器校验语法，返回错误消息列表（空 = 语法合法）。"""
    tmp = tempfile.mkdtemp(prefix="psparse-")
    path = os.path.join(tmp, "s.ps1")
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(script_text)
    ps = (
        "$errs=$null;"
        "[void][System.Management.Automation.Language.Parser]::ParseFile('%s',"
        "[ref]$null,[ref]$errs);"
        "if ($errs.Count) "
        "{ 'ERR ' + (($errs | ForEach-Object { $_.Message }) -join ' | ') } else { 'OK' }"
        % path.replace("'", "''")
    )
    try:
        # 输出按字节收（系统控制台可能是 GBK），再宽松解码，避免解码失败被误判为"无错误"
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=60,
                           creationflags=0x08000000)
    except Exception as e:                      # PowerShell 不可用 → 跳过校验
        shutil.rmtree(tmp, ignore_errors=True)
        return None, "无法调用 PowerShell：%s" % e
    raw = r.stdout or b""
    out = ""
    for enc in ("utf-8", "gbk", "latin-1"):     # 中文系统上 PowerShell 输出多为 GBK
        try:
            out = raw.decode(enc).strip()
            break
        except UnicodeDecodeError:
            continue
    shutil.rmtree(tmp, ignore_errors=True)
    if out.startswith("OK"):
        return [], ""
    if out.startswith("ERR"):
        return [x.strip() for x in out[3:].split("|") if x.strip()], ""
    return None, "无法判定 PowerShell 校验结果（输出：%r）" % out[:80]


class TestFirewallUsesPowerShell(unittest.TestCase):
    def test_01_add_ports_calls_ps_runner(self):
        calls = []
        with mock.patch.object(firewall, "run_powershell_elevated",
                               side_effect=lambda s, timeout=120: (
                                   calls.append(s),
                                   mock.Mock(ok=True, output=""))[1]):
            ok, _out = firewall.add_ports("T-", [(9001, "TCP"), (9002, "UDP")])
        self.assertTrue(ok)
        self.assertEqual(len(calls), 1)
        script = calls[0]
        self.assertIn('name="T-9001"', script)
        self.assertIn("protocol=UDP", script)
        self.assertIn("Out-Null", script)        # PowerShell 专有语法 → 必须走 PS

    def test_02_remove_calls_ps_runner(self):
        calls = []
        with mock.patch.object(firewall, "run_powershell_elevated",
                               side_effect=lambda s, timeout=120: (
                                   calls.append(s),
                                   mock.Mock(ok=True, output=""))[1]):
            firewall.remove("T-", 9001)
        self.assertEqual(len(calls), 1)
        self.assertIn('delete rule name="T-9001"', calls[0])

    def test_03_generated_script_is_valid_powershell(self):
        with mock.patch.object(firewall, "run_powershell_elevated",
                               side_effect=lambda s, timeout=120: (
                                   self.scripts.append(s),
                                   mock.Mock(ok=True, output=""))[1]):
            self.scripts = []
            firewall.add_ports("T-", [(9001, "TCP")])
            firewall.remove("T-", 9001)
        for script in self.scripts:
            errs, note = ps_parse_errors(script)
            if errs is None:
                self.skipTest(note)
            self.assertEqual(errs, [], "生成的脚本语法有误：%s\n%s" % (errs, script))


class TestWebClientAuthUsesPowerShell(unittest.TestCase):
    def test_01_calls_ps_runner(self):
        calls = []
        with mock.patch.object(web_server, "run_powershell_elevated",
                               side_effect=lambda s, timeout=120: (
                                   calls.append(s),
                                   mock.Mock(ok=True, output="OK"))[1]):
            ok, _out = web_server.set_webclient_basic_auth(True)
        self.assertTrue(ok)
        self.assertEqual(len(calls), 1)
        self.assertIn("BasicAuthLevel", calls[0])
        errs, note = ps_parse_errors(calls[0])
        if errs is None:
            self.skipTest(note)
        self.assertEqual(errs, [], "脚本语法有误：%s" % errs)

    def test_02_disable_value(self):
        calls = []
        with mock.patch.object(web_server, "run_powershell_elevated",
                               side_effect=lambda s, timeout=120: (
                                   calls.append(s),
                                   mock.Mock(ok=True, output="OK"))[1]):
            web_server.set_webclient_basic_auth(False)
        self.assertIn("-Value 1", calls[0])


class TestNoPowerShellSyntaxInCmdRunner(unittest.TestCase):
    """静态扫描：run_elevated(...) 的实参里不能出现 PowerShell 专有语法。"""

    PS_MARKERS = ("Out-Null", "Set-ItemProperty", "New-Item", "Get-NetAdapter",
                  "Set-NetFirewall", "Set-SmbServer", "Set-DnsClientServerAddress",
                  "$p=", "$errs", "-ErrorAction")

    def test_01_scan(self):
        bad = []
        for dirpath, _dirs, files in os.walk(os.path.join(ROOT, "app")):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(dirpath, fn)
                with io.open(path, encoding="utf-8") as f:
                    src = f.read()
                for m in re.finditer(r"run_elevated\(", src):
                    # 取该调用的整段（到行末或配平的右括号）
                    depth, i = 0, m.end() - 1
                    while i < len(src):
                        if src[i] == "(":
                            depth += 1
                        elif src[i] == ")":
                            depth -= 1
                            if depth == 0:
                                break
                        i += 1
                    block = src[m.start():i + 1]
                    if "powershell" in block:      # 自己包了 powershell 的调用没问题
                        continue
                    for marker in self.PS_MARKERS:
                        if marker in block:
                            line = src[:m.start()].count("\n") + 1
                            bad.append("%s:%d 传了 PowerShell 语法 %r"
                                       % (os.path.relpath(path, ROOT), line, marker))
        self.assertEqual(bad, [], "以下提权调用会因 cmd 解析失败：\n" + "\n".join(bad))


class TestElevatedBatContent(unittest.TestCase):
    """不提权也要验证生成的 .bat 内容：cmd 版直接跑命令，PS 版包 powershell。"""

    def _capture_bat(self, fn):
        from app.core import runner
        captured = {}

        def fake_exec(ptr):
            # byref(info) → CArgObject，._obj 是 SHELLEXECUTEINFOW 实例
            try:
                info = ptr._obj
                bat = info.lpFile
                if isinstance(bat, bytes):
                    bat = bat.decode("gbk", "replace")
                with io.open(bat, encoding="gbk", errors="replace") as f:
                    captured["bat"] = f.read()
            except Exception as e:
                captured["err"] = str(e)
            return 0        # 假装失败/取消，不真正提权

        with mock.patch.object(runner.ctypes.windll.shell32, "ShellExecuteExW",
                               side_effect=fake_exec):
            try:
                fn()
            except Exception:
                pass
        return captured.get("bat", "")

    def test_01_ps_variant_wraps_powershell(self):
        from app.core import runner
        bat = self._capture_bat(lambda: runner.run_powershell_elevated("'hi'"))
        self.assertTrue(bat, "未捕获到 .bat 内容")
        self.assertIn("powershell", bat.lower())
        self.assertIn("-Command", bat)

    def test_02_cmd_variant_runs_raw(self):
        from app.core import runner
        bat = self._capture_bat(lambda: runner.run_elevated("echo hi"))
        self.assertTrue(bat, "未捕获到 .bat 内容")
        self.assertNotIn("powershell", bat.lower())


if __name__ == "__main__":
    unittest.main()
