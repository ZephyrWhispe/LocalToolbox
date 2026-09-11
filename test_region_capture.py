#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""区域截图和编辑器交互修复验证脚本"""

import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_imports():
    """测试模块导入"""
    print("[1/5] 测试模块导入...")
    try:
        from app.bridge.screenshot_api import ShotApi
        print("  [OK] ShotApi 导入成功")
        return True
    except Exception as e:
        print(f"  [FAIL] 导入失败: {e}")
        return False

def test_shot_api_init():
    """测试 ShotApi 初始化"""
    print("\n[2/5] 测试 ShotApi 初始化...")
    try:
        from app.bridge.screenshot_api import ShotApi
        api = ShotApi()
        api._init_shot()
        print("  [OK] ShotApi 初始化成功")
        print(f"  - 遮罩窗口字典: {api._ovs}")
        print(f"  - 序列号: {api._ov_seq}")
        return True
    except Exception as e:
        print(f"  [FAIL] 初始化失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def verify_js_syntax():
    """验证 JavaScript 语法，并运行圈选交互 UI 回归测试。"""
    print("\n[3/5] 验证 JavaScript 语法与圈选交互...")
    import subprocess

    files = [
        "webui/js/pages/region_capture.js",
        "webui/js/pages/editor.js",
        "webui/js/pages/screenshot.js",
    ]

    all_ok = True
    for js_file in files:
        try:
            result = subprocess.run(
                ["node", "--check", js_file],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                print(f"  [OK] {js_file} 语法正确")
            else:
                print(f"  [FAIL] {js_file} 语法错误: {result.stderr}")
                all_ok = False
        except Exception as e:
            print(f"  [WARN] {js_file} 检查失败: {e}")
            all_ok = False

    # 圈选交互回归（拖选→确认/取消、引导脚本单次启动、后端调用参数个数）
    try:
        result = subprocess.run(
            ["node", "test_region_ui.js"],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            print("  [OK] 圈选交互回归测试全部通过")
        else:
            print("  [FAIL] 圈选交互回归测试失败:")
            print(result.stdout)
            all_ok = False
    except Exception as e:
        print(f"  [WARN] 圈选交互测试未运行: {e}")
        all_ok = False

    return all_ok

def verify_main_window_flags():
    """静态校验窗口拖动配置（easy_drag）与交互元素守卫。

    约定（两项必须同时成立，缺一即回归）：
    - 主窗口 easy_drag=True：无边框窗口的拖动由 pywebview 提供（按住任意区域
      拖动整窗）。自绘标题栏旧的 win_begin_drag/WM_NCLBUTTONDOWN 在 WebView2
      上因鼠标捕获不在本进程而拖不动窗口，已停用。
    - webui/js/window_drag.js 必须存在并在 index.html 中加载：它在 document
      冒泡阶段拦截交互元素（画布/输入框/按钮…）的 mousedown，使 easy_drag
      收不到事件——否则画布绘制会被整窗拖动劫持（「一划线窗口跟着鼠标跑」）。
    - 遮罩窗口 easy_drag=False：圈选需要全屏鼠标交互。
    """
    print("\n[5/5] 校验窗口拖动配置（easy_drag + 交互元素守卫）...")
    import ast
    import os

    def _create_window_kwargs(path):
        tree = ast.parse(open(path, encoding="utf-8").read())
        out = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                if (isinstance(fn, ast.Attribute) and fn.attr == "create_window"):
                    out.append({k.arg: k.value for k in node.keywords
                                if k.arg is not None})
        return out

    ok = True
    try:
        calls = _create_window_kwargs("main.py")
        if not calls:
            print("  [FAIL] main.py 未找到 webview.create_window 调用")
            return False
        kw = calls[0]
        easy = kw.get("easy_drag")
        frameless = kw.get("frameless")
        if not (isinstance(easy, ast.Constant) and easy.value is True):
            print("  [FAIL] 主窗口必须显式 easy_drag=True"
                  "（关掉后 WebView2 上窗口将完全拖不动）")
            ok = False
        else:
            print("  [OK] 主窗口 easy_drag=True（可按住任意非交互区域拖动整窗）")
        if not (isinstance(frameless, ast.Constant) and frameless.value is True):
            print("  [WARN] 主窗口 frameless 不是 True（自绘标题栏前提）")
    except Exception as e:
        print(f"  [FAIL] 校验主窗口参数失败: {e}")
        ok = False

    # 交互元素守卫：文件存在 + index.html 已加载
    try:
        guard = "webui/js/window_drag.js"
        if not os.path.isfile(guard):
            print("  [FAIL] 缺少 webui/js/window_drag.js（画布等交互将被整窗拖动劫持）")
            ok = False
        else:
            html = open("webui/index.html", encoding="utf-8").read()
            if 'src="js/window_drag.js"' not in html:
                print("  [FAIL] index.html 未加载 js/window_drag.js")
                ok = False
            else:
                print("  [OK] window_drag.js 已加载（画布/输入框/按钮不被整窗拖动劫持）")
    except Exception as e:
        print(f"  [FAIL] 校验拖动守卫失败: {e}")
        ok = False

    try:
        ov_calls = _create_window_kwargs("app/bridge/screenshot_api.py")
        if not ov_calls:
            print("  [WARN] 未找到遮罩窗口 create_window 调用")
        else:
            for i, kw in enumerate(ov_calls):
                easy = kw.get("easy_drag")
                if not (isinstance(easy, ast.Constant) and easy.value is False):
                    print(f"  [FAIL] 遮罩窗口 #{i + 1} 必须 easy_drag=False")
                    ok = False
            if ok:
                print(f"  [OK] 遮罩窗口 easy_drag=False（{len(ov_calls)} 处）")
    except Exception as e:
        print(f"  [FAIL] 校验遮罩窗口参数失败: {e}")
        ok = False
    return ok

def verify_css_syntax():
    """验证 CSS 语法（基本括号匹配）"""
    print("\n[4/5] 验证 CSS 语法...")
    try:
        with open("webui/css/app.css", "r", encoding="utf-8") as f:
            content = f.read()
        
        open_braces = content.count("{")
        close_braces = content.count("}")
        
        if open_braces == close_braces:
            print(f"  [OK] CSS 括号匹配 ({open_braces} 对)")
            
            # 检查新增的样式类
            if ".modal-page-container" in content:
                print("  [OK] modal-page-container 样式已添加")
            else:
                print("  [WARN] modal-page-container 样式未找到")
            
            if ".tool-item" in content:
                print("  [OK] tool-item 样式已添加")
            else:
                print("  [WARN] tool-item 样式未找到")
            
            return True
        else:
            print(f"  [FAIL] CSS 括号不匹配: {open_braces} open vs {close_braces} close")
            return False
    except Exception as e:
        print(f"  [FAIL] CSS 验证失败: {e}")
        return False

def main():
    print("=" * 60)
    print("区域截图和编辑器交互修复验证")
    print("=" * 60)
    
    results = []
    results.append(test_imports())
    results.append(test_shot_api_init())
    results.append(verify_js_syntax())
    results.append(verify_css_syntax())
    results.append(verify_main_window_flags())
    
    print("\n" + "=" * 60)
    if all(results):
        print("[OK] 所有验证通过！")
        print("\n修复内容（第二轮：划定区域后无法操作/不知成败）:")
        print("  1. 工具条按钮 mousedown 不再清空选区 → 点「确认」生效")
        print("  2. overlay 引导脚本单次启动 → 不再双层遮罩叠放")
        print("  3. call() 只转发实际参数 + 后端容忍多余实参 → ESC/取消可退出")
        print("  4. 先投递事件再销毁遮罩窗口 → 截图结果不丢失")
        print("  5. 新增「正在生成截图…」状态与错误条提示 → 截图成败可见")
        print("  6. 拖动守卫：easy_drag 拖整窗 + window_drag.js 拦截交互元素")
        print("     → 画布可正常绘制，窗口也能从任意非交互区域拖动")
        return 0
    else:
        print("[FAIL] 部分验证失败，请检查上述错误")
        return 1

if __name__ == "__main__":
    sys.exit(main())
