#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""验证关键修复是否正确应用。"""

import sys
import os

# 设置控制台编码为 UTF-8
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

def test_imports():
    """测试核心模块导入。"""
    print("[1/5] 测试模块导入...")
    try:
        from app.bridge.bridge import Bridge
        print("  [OK] Bridge 类导入成功（MRO 冲突已修复）")
        
        from app.core.scroll_capture import _capture_via_printwindow
        print("  [OK] PrintWindow API 可用（滚动截图兼容性修复）")
        
        from app.bridge.video_api import VideoFileHandler
        print("  [OK] Video HTTP 服务器可用（视频播放器修复）")
        
        return True
    except Exception as e:
        print(f"  [FAIL] 导入失败: {e}")
        return False

def test_scroll_capture_methods():
    """测试滚动截图方法。"""
    print("\n[2/5] 测试滚动截图方法...")
    try:
        from app.core import scroll_capture
        assert hasattr(scroll_capture, '_capture_via_printwindow')
        assert hasattr(scroll_capture, '_capture_via_gdi')
        print("  [OK] PrintWindow 和 GDI 双方法存在")
        return True
    except Exception as e:
        print(f"  [FAIL] 测试失败: {e}")
        return False

def test_video_server():
    """测试视频服务器类。"""
    print("\n[3/5] 测试视频服务器...")
    try:
        from app.bridge.video_api import VideoEditApi
        api = VideoEditApi()
        assert hasattr(api, '_start_video_server')
        assert hasattr(api, '_stop_video_server')
        print("  [OK] 视频 HTTP 服务器方法存在")
        return True
    except Exception as e:
        print(f"  [FAIL] 测试失败: {e}")
        return False

def test_editor_annotate():
    """测试编辑器标注模式代码。"""
    print("\n[4/5] 测试编辑器标注模式...")
    try:
        with open('webui/js/pages/editor.js', 'r', encoding='utf-8') as f:
            content = f.read()
        
        checks = [
            ('mode: "filter"', "滤镜模式"),
            ('toggleMode', "模式切换函数"),
            ('onCanvasMouseDown', "画布鼠标事件"),
            ('drawBrush', "画笔工具"),
            ('executeCrop', "裁剪功能"),
        ]
        
        for code, desc in checks:
            if code in content:
                print(f"  [OK] {desc}")
            else:
                print(f"  [FAIL] 缺少: {desc}")
                return False
        
        return True
    except Exception as e:
        print(f"  [FAIL] 测试失败: {e}")
        return False

def test_window_management():
    """测试窗口管理修复。"""
    print("\n[5/5] 测试窗口管理修复...")
    try:
        with open('app/bridge/screenshot_api.py', 'r', encoding='utf-8') as f:
            content = f.read()
        
        if 'PostMessageW' in content and 'WM_CLOSE' in content:
            print("  [OK] 遮罩窗口强制关闭逻辑存在")
        else:
            print("  [FAIL] 缺少 WM_CLOSE 逻辑")
            return False
            
        if 'SetForegroundWindow' in content:
            print("  [OK] 窗口激活逻辑存在")
        else:
            print("  [FAIL] 缺少 SetForegroundWindow")
            return False
        
        return True
    except Exception as e:
        print(f"  [FAIL] 测试失败: {e}")
        return False

def main():
    print("=" * 50)
    print("  LocalToolbox - 修复验证")
    print("=" * 50)
    
    results = [
        test_imports(),
        test_scroll_capture_methods(),
        test_video_server(),
        test_editor_annotate(),
        test_window_management(),
    ]
    
    print("\n" + "=" * 50)
    if all(results):
        print("[PASS] 所有修复验证通过！")
        print("\n现在可以运行 run.bat 启动应用进行测试。")
        return 0
    else:
        print("[FAIL] 部分修复未通过，请检查代码。")
        return 1

if __name__ == "__main__":
    sys.exit(main())
