# -*- coding: utf-8 -*-
# core_engine.py
# 描述:自动化宏的核心功能引擎
# 版本:1.7.3
# # 变更:(修复) 新增 MacroStopException，实现快捷键即时中断

# ======================================================================
# 即时中断异常
# ======================================================================
class MacroStopException(BaseException):
    """快捷键触发时注入到执行线程的异常，强制立刻中断宏。
    继承 BaseException 而非 Exception，确保不被 except Exception 误吞。
    """
    pass

class LoopConditionCheckError(RuntimeError):
    """Raised when a loop exit condition cannot be checked safely."""
    pass

class ScreenshotUnavailableError(RuntimeError):
    """Raised when the desktop cannot be captured."""
    pass

import pyautogui
import time
from PIL import Image, ImageGrab, ImageStat
import re
import pyperclip
import json
import os
import sys
import subprocess
import shlex
import ctypes
import functools
import threading
import ast
import math
import operator as operator_module
from decimal import Decimal, InvalidOperation

if sys.platform == 'win32':
    ctypes.windll.shell32.CommandLineToArgvW.argtypes = (ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int))
    ctypes.windll.shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    ctypes.windll.kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
    ctypes.windll.kernel32.LocalFree.restype = ctypes.c_void_p

# ======================================================================
# 7. 宏文件持久化工具 (从 MacroAssistant.py 迁移)
# ======================================================================
class MacroPersistence:
    @staticmethod
    def convert_to_native(obj):
        """递归转换所有值为 Python 原生类型 (处理 numpy 等类型)"""
        try:
            import numpy as np
            numpy_types = (np.integer, np.floating)
        except ImportError:
            numpy_types = ()
            
        if isinstance(obj, dict):
            return {k: MacroPersistence.convert_to_native(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [MacroPersistence.convert_to_native(item) for item in obj]
        elif numpy_types and isinstance(obj, numpy_types):
            return obj.item()
        else:
            return obj

    @staticmethod
    def save(file_path, steps):
        native_steps = MacroPersistence.convert_to_native(steps)
        tmp_path = file_path + '.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as f:
            f.write('[\n')
            for i, step in enumerate(native_steps):
                step_str = json.dumps(step, ensure_ascii=False)
                if i < len(native_steps) - 1:
                    f.write(f'    {step_str},\n')
                else:
                    f.write(f'    {step_str}\n')
            f.write(']\n')
        os.replace(tmp_path, file_path)

    @staticmethod
    def load(file_path):
        """从 JSON 文件加载宏"""
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data



try:
    import pygetwindow as gw
    PYGETWINDOW_AVAILABLE = True
except ImportError:
    PYGETWINDOW_AVAILABLE = False
    print("[配置] FAIL 未找到 pygetwindow 库 (pip install pygetwindow)。'激活窗口' 功能将不可用。")

# ======================================================================
# 全局配置
# ======================================================================
FORCE_OCR_ENGINE = None 
ENABLE_GLOBAL_FALLBACK = True
# 条件循环检测间隔 (秒) - 平衡流畅度与准确率
LOOP_CHECK_INTERVAL = 0.2  # 优化: 从 0.5s 降低到 0.2s (平衡流畅度与准确率)
# 性能与缓存相关常量
LOOP_PHYSICAL_COOLDOWN = 0.05  # 循环物理冷却时间（秒），防止队列瞬间爆炸
CACHE_BOX_PADDING = 50  # 缓存区域扩展边距（像素）
TEMPLATE_CACHE_SIZE = 200  # 模板缓存大小，限制大图/多缩放场景的内存占用
QUICK_CHECK_SCALES = (1.0, 0.9, 1.1)  # 快速检查尝试的缩放比例
GOTO_LABEL_DEFAULT_MAX_JUMPS = 100


try:
    import ocr_engine
except ImportError:
    print("[严重错误] 未找到 'ocr_engine.py'。")
    class ocr_engine:
        def find_text_location(*args, **kwargs): return None
        WINOCR_AVAILABLE = False
        TESSERACT_AVAILABLE = False
        RAPIDOCR_AVAILABLE = False

try:
    import vlm_engine
except ImportError:
    print("[配置] FAIL 未找到 'vlm_engine.py'。AI 自然语言指令功能将不可用。")
    class vlm_engine:
        def find_location_by_vlm(*args, **kwargs): return None
        VLM_AVAILABLE = False

try:
    import cv2
    import numpy as np 
    OPENCV_AVAILABLE = True
    print("[CONFIG] OpenCV engine ready")
except ImportError:
    OPENCV_AVAILABLE = False
    print("[CONFIG] OpenCV not found, fallback to slower image matching")

# ======================================================================
# 快捷键工具模块
# ======================================================================
class HotkeyUtils:
    PYNPUT_TO_VK = {
        'f1': 0x70, 'f2': 0x71, 'f3': 0x72, 'f4': 0x73, 'f5': 0x74, 'f6': 0x75,
        'f7': 0x76, 'f8': 0x77, 'f9': 0x78, 'f10': 0x79, 'f11': 0x7A, 'f12': 0x7B,
        'a': 0x41, 'b': 0x42, 'c': 0x43, 'd': 0x44, 'e': 0x45, 'f': 0x46, 'g': 0x47,
        'h': 0x48, 'i': 0x49, 'j': 0x4A, 'k': 0x4B, 'l': 0x4C, 'm': 0x4D, 'n': 0x4E,
        'o': 0x4F, 'p': 0x50, 'q': 0x51, 'r': 0x52, 's': 0x53, 't': 0x54, 'u': 0x55,
        'v': 0x56, 'w': 0x57, 'x': 0x58, 'y': 0x59, 'z': 0x5A,
        '0': 0x30, '1': 0x31, '2': 0x32, '3': 0x33, '4': 0x34, '5': 0x35, '6': 0x36,
        '7': 0x37, '8': 0x38, '9': 0x39,
        'enter': 0x0D, 'space': 0x20, 'tab': 0x09, 'caps_lock': 0x14,
        'esc': 0x1B, 'page_up': 0x21, 'page_down': 0x22, 'end': 0x23, 'home': 0x24,
        'left': 0x25, 'up': 0x26, 'right': 0x27, 'down': 0x28, 'insert': 0x2D, 'delete': 0x2E,
        'backspace': 0x08,
    }
    VK_TO_PYNPUT = {v: k for k, v in PYNPUT_TO_VK.items()}
    
    if sys.platform == 'win32':
        PYNPUT_MOD_TO_WIN_MOD = {
            'ctrl': 0x0002,  # win32con.MOD_CONTROL
            'alt': 0x0001,   # win32con.MOD_ALT
            'shift': 0x0004, # win32con.MOD_SHIFT
            'cmd': 0x0008,   # win32con.MOD_WIN
        }
    else:
        PYNPUT_MOD_TO_WIN_MOD = {}
    
    @staticmethod
    def format_hotkey_display(hotkey_str):
        if not hotkey_str or "录制" in hotkey_str:
            return hotkey_str
        try:
            parts = hotkey_str.split('+')
            display_parts = []
            for part in parts:
                if part.lower() in {'ctrl', 'alt', 'shift', 'cmd'}:
                    display_parts.append(part.capitalize())
                else:
                    display_parts.append(part.upper())
            return "+".join(display_parts)
        except Exception:
            return hotkey_str.upper()

# ======================================================================
# 宏定义元数据
# ======================================================================
class MacroSchema:
    ACTION_TRANSLATIONS = {
        'FIND_IMAGE':     '01. 查找图像',
        'FIND_TEXT':      '02. 查找文本 (OCR)',
        'MOVE_OFFSET':    '03. 相对移动',
        'MOVE_TO':        '04. 移动到 (绝对坐标)',
        'CLICK':          '05. 点击鼠标',
        'SCROLL':         '06. 滚动滚轮',
        'WAIT':           '07. 等待',
        'TYPE_TEXT':      '08. 输入文本',
        'PRESS_KEY':      '09. 按下按键',
        'AI_COMMAND':     '10. AI 自然语言指令',
        'ACTIVATE_WINDOW':'11. 激活窗口 (按标题)',
        'NOTE':           "12. 备注",
        'GOTO_LABEL':     '13. 跳转到标签',
        'IF_IMAGE_FOUND': '14. IF 找到图像',
        'IF_TEXT_FOUND':  '15. IF 找到文本',
        'ELSE':           '16. ELSE',
        'END_IF':         '17. END_IF',
        'RUN':           '18. 执行命令/脚本/文件',
        'LOOP_START':     '19. 循环开始',          # Loop
        'END_LOOP':       '20. 结束循环',          # EndLoop
        'SET_VAR':        '21. 设置变量',          # Set Var
        'EXTRACT_VAR':    '22. 正则提取变量',      # Extract
        'READ_FILE':      '23. 读取文件到变量',    # Read File
        'IF_VAR':         '24. IF 变量比较',
        'CALCULATE':      '25. 变量计算',          # Calculate
        'GOTO_IF':        '26. 条件跳转',          # Goto If
        'WRITE_FILE':     '27. 写入文件',          # Write File
        'JSON_EXTRACT':   '28. JSON 提取',         # Json Extract
        'PROMPT_INPUT':   '29. 人工输入',          # Prompt Input
        'FOREACH_LINE':   '30. 批量处理每一行',    # Batch Lines
        'END_FOREACH':    '31. 结束批量处理',
    }
    ACTION_KEYS_TO_NAME = {v: k for k, v in ACTION_TRANSLATIONS.items()}
    CONTROL_FLOW_ACTIONS = {'IF_IMAGE_FOUND', 'IF_TEXT_FOUND', 'IF_VAR', 'ELSE', 'END_IF', 'LOOP_START', 'END_LOOP', 'FOREACH_LINE', 'END_FOREACH'}
    
    LANG_OPTIONS = {'chi_sim (简体中文)': 'chi_sim', 'eng (英文)': 'eng'}
    LANG_VALUES_TO_NAME = {v: k for k, v in LANG_OPTIONS.items()}
    
    CLICK_OPTIONS = {'left (左键)': 'left', 'right (右键)': 'right', 'middle (中键)': 'middle'}
    CLICK_VALUES_TO_NAME = {v: k for k, v in CLICK_OPTIONS.items()}

# ======================================================================
# 性能监控
# ======================================================================
class PerformanceMonitor:
    def __init__(self): self.reset()
    def reset(self):
        self.image_stats = {'hits': 0, 'misses': 0, 'times': [], 'loop_hits': 0}
        self.ocr_stats = {'hits': 0, 'misses': 0, 'times': [], 'loop_hits': 0}
    def _get_stats_for(self, stats_dict):
        total = stats_dict['hits'] + stats_dict['misses']
        if total == 0: return "(无记录)"
        unique_hits = stats_dict['hits'] - stats_dict['loop_hits']
        total_valid = unique_hits + stats_dict['misses']
        hit_rate = (unique_hits / total_valid * 100) if total_valid > 0 else 0
        avg_ms = (sum(stats_dict['times']) / len(stats_dict['times']) * 1000) if stats_dict['times'] else 0
        return f"(命中{hit_rate:.0f}% | 循环{stats_dict['loop_hits']} | 均耗{avg_ms:.0f}ms)"
    def record_hit(self, is_loop, is_ocr):
        s = self.ocr_stats if is_ocr else self.image_stats
        s['hits'] += 1
        if is_loop: s['loop_hits'] += 1
    def record_miss(self, is_ocr): (self.ocr_stats if is_ocr else self.image_stats)['misses'] += 1
    def record_time(self, dt, is_ocr): (self.ocr_stats if is_ocr else self.image_stats)['times'].append(dt)
    def get_stats(self): return f"图像{self._get_stats_for(self.image_stats)} | OCR{self._get_stats_for(self.ocr_stats)}"

perf = PerformanceMonitor()

# ======================================================================
# 循环缓存管理器
# ======================================================================
class LoopCacheManager:
    def __init__(self): self.reset()
    
    def reset(self):
        self.caches = {}
        self.stack = []
        
    def get_current_loop_id(self):
        return self.stack[-1] if self.stack else None

    def enter(self, loop_id):
        if loop_id not in self.caches:
            self.caches[loop_id] = {}
        self.stack.append(loop_id)

    def exit(self):
        if self.stack:
            loop_id = self.stack.pop()
            # 主动清理该循环的缓存，符合设计原则
            # 注意: execute_steps 的 finally 块也会调用 reset() 作为兜底
            if loop_id in self.caches:
                del self.caches[loop_id]

    def clear_cache(self, loop_id):
        if loop_id in self.caches:
            del self.caches[loop_id]

    def get(self, sig): 
        loop_id = self.get_current_loop_id()
        return self.caches.get(loop_id, {}).get(sig) if loop_id else None

    def set(self, sig, loc): 
        loop_id = self.get_current_loop_id()
        if loop_id:
            if loop_id not in self.caches:
                 self.caches[loop_id] = {}
            self.caches[loop_id][sig] = loc

loop_cache = LoopCacheManager()

# ======================================================================
# 核心工具函数
# ======================================================================
def _safe_int(value, default=None, min_value=None, max_value=None):
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default, False
    if min_value is not None and result < min_value:
        return default, False
    if max_value is not None and result > max_value:
        return default, False
    return result, True

def _safe_float(value, default=None, min_value=None, max_value=None):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default, False
    if min_value is not None and result < min_value:
        return default, False
    if max_value is not None and result > max_value:
        return default, False
    return result, True

def _warn_param_default(action, name, default):
    print(f"  [WARN] {action} invalid parameter '{name}', using default: {default}")

def _error_param_skip(action, name, expected):
    print(f"  [ERROR] {action} invalid parameter '{name}', expected {expected}; step skipped")

def _console_safe_text(value):
    """Return text that can be printed even when stdout uses a legacy code page."""
    text = str(value)
    encoding = getattr(sys.stdout, 'encoding', None) or 'utf-8'
    return text.encode(encoding, errors='replace').decode(encoding, errors='replace')

def _coerce_bbox(raw_bbox):
    if not isinstance(raw_bbox, (list, tuple)):
        return None
    try:
        bbox = [int(value) for value in raw_bbox]
    except (TypeError, ValueError):
        return None
    if len(bbox) == 2:
        bbox = [bbox[0], bbox[1], bbox[0] + 1, bbox[1] + 1]
    if len(bbox) < 4 or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        return None
    return bbox[:4]

def bbox_to_region(raw_bbox):
    """Convert an (x1, y1, x2, y2) box into a screenshot region."""
    bbox = _coerce_bbox(raw_bbox)
    if not bbox:
        return None
    return (bbox[0], bbox[1], bbox[2] - bbox[0], bbox[3] - bbox[1])

def _padded_bbox_to_region(raw_bbox, pad):
    bbox = _coerce_bbox(raw_bbox)
    if not bbox:
        return None
    left = bbox[0] - pad
    top = bbox[1] - pad
    if sys.platform != 'win32':
        left = max(0, left)
        top = max(0, top)
    right = bbox[2] + pad
    bottom = bbox[3] + pad
    return (left, top, right - left, bottom - top)

def _copy_to_clipboard_with_retry(text, ctx=None, retries=3, delay=0.2):
    for _ in range(retries):
        if ctx and ctx.get('stop_requested'):
            raise MacroStopException("Stop requested while writing to clipboard")
        try:
            pyperclip.copy(text)
            return True
        except Exception:
            time.sleep(delay)
    return False

def smart_screenshot(region=None, pad=0):
    try:
        if sys.platform == 'win32':
            import ctypes
            SM_XVIRTUALSCREEN = 76
            SM_YVIRTUALSCREEN = 77
            SM_CXVIRTUALSCREEN = 78
            SM_CYVIRTUALSCREEN = 79
            user32 = ctypes.windll.user32
            vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
            vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
            vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
            vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
            
            if region:
                x1 = region[0] - pad
                y1 = region[1] - pad
                x2 = region[0] + region[2] + pad
                y2 = region[1] + region[3] + pad
                
                # 针对多屏幕或跨主屏坐标，采用 all_screens 抓取并裁剪
                screen_w = user32.GetSystemMetrics(0)
                screen_h = user32.GetSystemMetrics(1)
                if x1 < 0 or y1 < 0 or x2 > screen_w or y2 > screen_h or vx < 0 or vy < 0:
                    full_screen = ImageGrab.grab(all_screens=True)
                    try:
                        crop_box = (x1 - vx, y1 - vy, x2 - vx, y2 - vy)
                        # 确保不越界并精确计算钳位后的偏移量
                        cx1 = max(0, crop_box[0])
                        cy1 = max(0, crop_box[1])
                        cx2 = min(full_screen.width, crop_box[2])
                        cy2 = min(full_screen.height, crop_box[3])
                        
                        crop_box = (cx1, cy1, cx2, cy2)
                        if crop_box[2] <= crop_box[0] or crop_box[3] <= crop_box[1]:
                            raise ValueError("Invalid crop box geometry")
                        cropped_img = full_screen.crop(crop_box)
                        full_screen.close()
                        # 使用 clamp 限制后的值反向修正物理绝对坐标作为 offset
                        return cropped_img, (cx1 + vx, cy1 + vy)
                    except Exception as e:
                        try: full_screen.close()
                        except Exception: pass
                        raise e
                
                return ImageGrab.grab(bbox=(x1, y1, x2, y2)), (x1, y1)
            else:
                primary_w = user32.GetSystemMetrics(0)
                primary_h = user32.GetSystemMetrics(1)
                if vw > 0 and vh > 0 and (vx != 0 or vy != 0 or vw > primary_w or vh > primary_h):
                    return ImageGrab.grab(all_screens=True), (vx, vy)
                return ImageGrab.grab(), (0, 0)
        else:
            if region:
                x = max(0, region[0] - pad)
                y = max(0, region[1] - pad)
                return ImageGrab.grab(bbox=(x, y, region[0]+region[2]+pad, region[1]+region[3]+pad)), (x, y)
            return ImageGrab.grab(), (0, 0)
    except OSError as e:
        raise ScreenshotUnavailableError("Screen capture is unavailable") from e

SCALES = (1.0, 0.9, 1.1, 0.8, 1.2)
@functools.lru_cache(maxsize=TEMPLATE_CACHE_SIZE)  # [优化] 增大缓存以减少文件读取
def _get_template(path, scale):
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if img is None: return None, 0, 0
    if scale != 1.0:
        h, w = img.shape[:2]
        img = cv2.resize(img, (int(w*scale), int(h*scale)), interpolation=cv2.INTER_AREA)
    return img, img.shape[1], img.shape[0]

def find_image_cv2(path, conf, screenshot_pil, offset=(0,0), enhanced_mode=False):
    if not OPENCV_AVAILABLE: return None
    try:
        t0 = time.time()
        screen_gray = cv2.cvtColor(np.array(screenshot_pil), cv2.COLOR_RGB2GRAY)
        best = (-1, None, 0, 0)
        scales_to_try = SCALES if enhanced_mode else [1.0]
        for scale in scales_to_try:
            tmpl, tw, th = _get_template(path, scale)
            if tmpl is None or th > screen_gray.shape[0] or tw > screen_gray.shape[1]: continue
            res = cv2.matchTemplate(screen_gray, tmpl, cv2.TM_CCOEFF_NORMED)
            min_v, max_v, min_l, max_l = cv2.minMaxLoc(res)
            if max_v > best[0]: best = (max_v, max_l, tw, th)
            if best[0] >= 0.95 and best[0] >= conf: break 
        val, loc, w, h = best
        if val >= conf and loc:
            cx, cy = offset[0] + loc[0] + w//2, offset[1] + loc[1] + h//2
            perf.record_time(time.time()-t0, False)
            return (cx, cy, w, h), val
    except (cv2.error, ValueError, TypeError, AttributeError) as e:
        print(f"CV2找图错误: {e}")
    return None

def quick_check_cv2(path, conf, screenshot_pil, offset, target_loc, enhanced_mode=False):
    """
    [补丁优化] 快速检查图片是否仍在缓存位置
    
    优化: 支持多缩放比例检查，避免缓存失效
    
    Args:
        path: 图片文件路径
        conf: 置信度阈值
        screenshot_pil: PIL截图对象
        offset: 截图偏移量 (x, y)
        target_loc: 目标位置 (x, y)
        enhanced_mode: 是否启用增强模式
        
    Returns:
        bool: 是否在目标位置找到图片
    """
    if not OPENCV_AVAILABLE: return False
    try:
        # [补丁优化] 尝试多个缩放比例，避免因缩放不匹配导致误判
        scales_to_try = QUICK_CHECK_SCALES if enhanced_mode else [1.0]
        for scale in scales_to_try:
            tmpl, tw, th = _get_template(path, scale)
            if tmpl is None: continue
            
            pad_w, pad_h = tw//2 + 15, th//2 + 15
            rel_x, rel_y = target_loc[0] - offset[0], target_loc[1] - offset[1]
            l, t = max(0, rel_x - pad_w), max(0, rel_y - pad_h)
            r, b = min(screenshot_pil.width, rel_x + pad_w), min(screenshot_pil.height, rel_y + pad_h)
            if r <= l or b <= t: continue
            
            crop = cv2.cvtColor(np.array(screenshot_pil.crop((l, t, r, b))), cv2.COLOR_RGB2GRAY)
            if crop.shape[0] < th or crop.shape[1] < tw:
                continue
            _, max_v, _, _ = cv2.minMaxLoc(cv2.matchTemplate(crop, tmpl, cv2.TM_CCOEFF_NORMED))
            
            if max_v >= conf:
                return True  # 找到匹配，立即返回
        
        return False  # 所有缩放比例都不匹配
    except (ValueError, TypeError, AttributeError, IndexError) + ((cv2.error,) if OPENCV_AVAILABLE else ()) as e:
        # Loop cache misses can happen frequently; keep this hot path quiet.
        print(f"[quick_check_cv2] 异常: {e}")
        return False

# ======================================================================
# 主执行引擎
# ======================================================================
def _normalize_goto_label(label):
    return str(label or '').strip().casefold()

def _extract_goto_label_from_note(text):
    match = re.match(r'^\s*(?:LABEL|标签)\s*[:：]\s*(.+?)\s*$', str(text or ''), re.IGNORECASE)
    if not match:
        return None
    label = match.group(1).strip()
    return label or None

def _build_goto_label_table(steps):
    labels = {}
    loop_depth = 0
    for idx, step in enumerate(steps):
        action = step.get('action', '')
        if action in ('END_LOOP', 'END_FOREACH') and loop_depth > 0:
            loop_depth -= 1

        if action == 'NOTE' and step.get('enabled', True):
            label = _extract_goto_label_from_note(step.get('params', {}).get('text', ''))
            if label:
                if loop_depth > 0:
                    raise ValueError(f"标签 '{label}' 位于循环块内部，当前版本暂不支持跳转到循环内部标签")
                key = _normalize_goto_label(label)
                if key in labels:
                    prev_idx = labels[key]['index']
                    raise ValueError(f"标签重复: '{label}' 同时出现在第 {prev_idx + 1} 步和第 {idx + 1} 步")
                labels[key] = {'name': label, 'index': idx}

        if action in ('LOOP_START', 'FOREACH_LINE'):
            loop_depth += 1
    return labels

def _render_vars(param_str, ctx):
    if not isinstance(param_str, str) or not param_str: return param_str
    vars_dict = ctx.get('vars', {})
    def repl(match):
        var_name = match.group(1)
        return str(vars_dict.get(var_name, match.group(0)))
    return re.sub(r'\{([^{}]+)\}', repl, param_str)

def _parse_json_path(path):
    path = str(path or '').strip()
    if not path or path == '$':
        return []
    if path.startswith('$'):
        path = path[1:]
    if path.startswith('.'):
        path = path[1:]

    tokens = []
    i = 0
    while i < len(path):
        if path[i] == '.':
            i += 1
            continue
        if path[i] == '[':
            end = path.find(']', i + 1)
            if end == -1:
                raise ValueError("JSON path bracket is not closed")
            raw = path[i + 1:end].strip()
            if not raw:
                raise ValueError("JSON path bracket is empty")
            if (raw[0:1] == raw[-1:] and raw[0:1] in ("'", '"')):
                tokens.append(raw[1:-1])
            else:
                try:
                    tokens.append(int(raw))
                except ValueError:
                    tokens.append(raw)
            i = end + 1
            continue

        start = i
        while i < len(path) and path[i] not in '.[':
            i += 1
        key = path[start:i].strip()
        if not key:
            raise ValueError("JSON path contains an empty key")
        tokens.append(key)
    return tokens

def _json_extract_value(source_text, json_path):
    data = json.loads(source_text)
    current = data
    for token in _parse_json_path(json_path):
        if isinstance(token, int):
            if not isinstance(current, list):
                raise KeyError(f"expected list before index [{token}]")
            current = current[token]
        else:
            if not isinstance(current, dict):
                raise KeyError(f"expected object before key '{token}'")
            current = current[token]
    if isinstance(current, (dict, list)):
        return json.dumps(current, ensure_ascii=False)
    if current is None:
        return ''
    return str(current)

def _parse_bool_param(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in ('1', 'true', 'yes', 'y', 'on'):
        return True
    if text in ('0', 'false', 'no', 'n', 'off'):
        return False
    return default

def _split_field_names(value):
    if isinstance(value, (list, tuple)):
        raw_names = value
    else:
        raw_names = re.split(r'[,，\t|]+', str(value or ''))
    return [str(name).strip() for name in raw_names if str(name).strip()]

def _normalize_split_delimiter(value):
    text = str(value or '')
    lower = text.strip().lower()
    if lower in ('\\t', 'tab'):
        return '\t'
    if lower in ('\\s', 'space'):
        return ' '
    return text

def _set_foreach_line_vars(loop_data, ctx):
    vars_dict = ctx.setdefault('vars', {})
    items = loop_data.get('items', [])
    index = loop_data.get('index', 0)
    total = len(items)
    line = items[index] if 0 <= index < total else ''

    current_line_var = loop_data.get('current_line_var') or 'current_line'
    index_var = loop_data.get('index_var') or 'loop_index'
    total_var = loop_data.get('total_var') or 'loop_total'
    vars_dict[current_line_var] = line
    vars_dict[index_var] = str(index + 1)
    vars_dict[total_var] = str(total)

    field_names = loop_data.get('field_names') or []
    delimiter = loop_data.get('delimiter', '')
    if field_names:
        if delimiter:
            values = line.split(delimiter)
        else:
            values = [line]
        if loop_data.get('strip_fields', True):
            values = [value.strip() for value in values]
        for field_index, name in enumerate(field_names):
            vars_dict[name] = values[field_index] if field_index < len(values) else ''

def _compare_values(left, operator, right):
    left = str(left)
    right = str(right)
    operator = str(operator or '==')

    if operator == '包含':
        return right in left
    if operator == '不包含':
        return right not in left

    numeric_ops = {
        '==': lambda a, b: a == b,
        '!=': lambda a, b: a != b,
        '>': lambda a, b: a > b,
        '<': lambda a, b: a < b,
        '>=': lambda a, b: a >= b,
        '<=': lambda a, b: a <= b,
    }
    if operator not in numeric_ops:
        return False

    try:
        return numeric_ops[operator](_parse_compare_number(left), _parse_compare_number(right))
    except (TypeError, ValueError, InvalidOperation):
        return numeric_ops[operator](left, right)

def _parse_compare_number(value):
    text = str(value).strip()
    if not text:
        raise ValueError("empty numeric value")
    if re.fullmatch(r'[+-]?\d+', text):
        return int(text)
    if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', text):
        raise ValueError(f"not a numeric value: {value}")
    number = Decimal(text)
    if not number.is_finite():
        raise ValueError(f"not a finite numeric value: {value}")
    return number

_CALC_OPERATORS = {
    ast.Add: operator_module.add,
    ast.Sub: operator_module.sub,
    ast.Mult: operator_module.mul,
    ast.Div: operator_module.truediv,
    ast.FloorDiv: operator_module.floordiv,
    ast.Mod: operator_module.mod,
}
_CALC_UNARY_OPERATORS = {
    ast.UAdd: operator_module.pos,
    ast.USub: operator_module.neg,
}
_CALC_MAX_ABS_VALUE = 1e18

def _validate_calc_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("Only numeric constants are supported")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite numeric values are not supported")
    if abs(value) > _CALC_MAX_ABS_VALUE:
        raise OverflowError("Calculation result is too large")
    return value

def _safe_calculate_expression(expression):
    def eval_node(node):
        if isinstance(node, ast.Expression):
            return eval_node(node.body)
        if isinstance(node, ast.Constant):
            return _validate_calc_number(node.value)
        if isinstance(node, ast.BinOp):
            op_func = _CALC_OPERATORS.get(type(node.op))
            if op_func is None:
                raise TypeError("Unsupported operator")
            return _validate_calc_number(op_func(eval_node(node.left), eval_node(node.right)))
        if isinstance(node, ast.UnaryOp):
            op_func = _CALC_UNARY_OPERATORS.get(type(node.op))
            if op_func is None:
                raise TypeError("Unsupported unary operator")
            return _validate_calc_number(op_func(eval_node(node.operand)))
        raise TypeError("Unsupported node")

    return eval_node(ast.parse(str(expression), mode='eval'))

def execute_steps(steps, run_context=None, status_callback=None):
    print(f"\n--- 执行开始 (Core V1.7.1 Beta) ---")
    perf.reset(); loop_cache.reset()
    _get_template.cache_clear()
    ctx = run_context if run_context else {}
    ctx.setdefault('last_pos', (None, None))
    ctx.setdefault('stop_requested', False)
    ctx.setdefault('clipboard_var', '')
    ctx.setdefault('_active_processes', set())
    ctx.setdefault('_active_process_lock', threading.RLock())
    ctx.setdefault('vars', {})
    ctx['_goto_counts'] = {}
    try:
        goto_labels = _build_goto_label_table(steps)
    except ValueError as e:
        print(f"  [GOTO] 标签配置错误: {e}")
        return False
    
    default_stop = "Ctrl+F11"
    try:
        s = run_context.get('stop_key_str', default_stop)
        stop_key_display = HotkeyUtils.format_hotkey_display(s)
    except Exception:
        stop_key_display = default_stop
    
    pc, loops = 0, []
    try:
        while pc < len(steps):

            if ctx.get('stop_requested', False): 
                print(f"  [停止] 用户请求停止 ({stop_key_display})")
                break
                
            step = steps[pc]; act = step.get('action',''); p = step.get('params',{})
            if ctx.get('debug_steps', False) or not loops or act in {'LOOP_START', 'END_LOOP', 'FOREACH_LINE', 'END_FOREACH', 'ELSE', 'END_IF', 'RUN', 'NOTE', 'GOTO_LABEL'}:
                print(f"[{pc+1}] {act}")
            next_pc = pc + 1

            # [新增] 处理被屏蔽的普通步骤
            if not step.get('enabled', True):
                if act not in MacroSchema.CONTROL_FLOW_ACTIONS:
                    print(f"  [屏蔽] 跳过步骤: {act}")
                    pc = next_pc
                    continue
                else:
                    # 保护机制: 控制流节点强制执行，忽略屏蔽标志
                    pass

            try:
                # [关键] 每次循环初始化结果变量
                res = None
                if (act.startswith('FIND_') or act.startswith('IF_')) and act != 'IF_VAR':
                    res = _handle_find(act, p, ctx, loop_cache.get_current_loop_id() is not None)
                    if act.startswith('IF_'):
                        if not res:
                            print("  -> IF条件不满足,跳过")
                            next_pc = _find_jump(steps, pc, 'IF_', 'END_IF', ['ELSE', 'END_IF'])
                    elif not res: print("  -> 没找到目标,宏停止"); break
                    
                    # FIND_ 和 IF_ 找到目标后均移动鼠标，保持 2026-05-16 的稳定行为。
                    # IF 体内的无坐标 CLICK 可以直接点击当前位置。
                    if res:
                        target_x, target_y = res[0], res[1]
                        ctx['last_pos'] = (target_x, target_y)
                        pyautogui.moveTo(target_x, target_y)
                
                elif act == 'AI_COMMAND':
                    # AI 自然语言指令处理
                    instruction = p.get('instruction', '')
                    if not instruction:
                        print("  [错误] AI 指令为空")
                        break
                    
                    # 获取可选的区域参数
                    region = None
                    if p.get('region') is not None:
                        bbox = _coerce_bbox(p.get('region'))
                        if bbox is None:
                            raise ValueError(f"AI 手动区域解析失败(格式错误): {p.get('region')}")
                        region = tuple(bbox)
                    elif p.get('cache_box') is not None:
                        bbox = _coerce_bbox(p.get('cache_box'))
                        if bbox:
                            region = tuple(bbox)
                    
                    print(f"  [AI] 执行指令: {instruction}")
                    
                    # 调用 VLM 引擎
                    coords = vlm_engine.find_location_by_vlm(instruction, region=region)
                    
                    if coords:
                        target_x, target_y = coords
                        print(f"  [AI] 返回坐标: ({target_x}, {target_y})")
                        duration, ok = _safe_float(p.get('duration', 0.25), 0.25, min_value=0)
                        if not ok:
                            _warn_param_default('AI_COMMAND', 'duration', duration)
                        pyautogui.moveTo(target_x, target_y, duration=duration)
                        ctx['last_pos'] = (target_x, target_y)
                    else:
                        print("  [AI] 未找到目标位置")
                        if p.get('fail_stop', True):  # 默认失败时停止
                            break
                
                elif act == 'CLICK':
                    btn = p.get('button', 'left').lower()
                    clicks, ok = _safe_int(p.get('clicks', 1), None, min_value=1)
                    if not ok:
                        _error_param_skip('CLICK', 'clicks', 'positive integer')
                        pc = next_pc; continue
                    interval, ok = _safe_float(p.get('interval', 0.0), 0.0, min_value=0)
                    if not ok:
                        _warn_param_default('CLICK', 'interval', interval)
                    duration, ok = _safe_float(p.get('duration', 0.0), 0.0, min_value=0)
                    if not ok:
                        _warn_param_default('CLICK', 'duration', duration)
                    try:
                        x = int(p.get('x', '')) if str(p.get('x', '')).strip() else None
                        y = int(p.get('y', '')) if str(p.get('y', '')).strip() else None
                    except (ValueError, TypeError):
                        print("  [错误] CLICK 坐标参数无效")
                        break
                    pyautogui.click(x=x, y=y, button=btn, clicks=clicks, interval=interval, duration=duration)
                    if x is not None and y is not None:
                        ctx['last_pos'] = (x, y)
                
                elif act == 'MOVE_TO':
                    try:
                        x, y = int(p.get('x', 0)), int(p.get('y', 0))
                    except (ValueError, TypeError):
                        print("  [错误] MOVE_TO 坐标参数无效")
                        break
                    duration, ok = _safe_float(p.get('duration', 0.25), 0.25, min_value=0)
                    if not ok:
                        _warn_param_default('MOVE_TO', 'duration', duration)
                    pyautogui.moveTo(x, y, duration=duration)
                    ctx['last_pos'] = (x, y)
                
                elif act == 'MOVE_OFFSET':
                    if ctx['last_pos'][0] is None or ctx['last_pos'][1] is None:
                        print("  [错误] 无上次坐标"); break
                    try:
                        ox, oy = int(p.get('x_offset', 0)), int(p.get('y_offset', 0))
                    except (ValueError, TypeError):
                        print("  [错误] MOVE_OFFSET 偏移参数无效")
                        break
                    duration, ok = _safe_float(p.get('duration', 0.25), 0.25, min_value=0)
                    if not ok:
                        _warn_param_default('MOVE_OFFSET', 'duration', duration)
                    pyautogui.move(ox, oy, duration=duration)
                    ctx['last_pos'] = (ctx['last_pos'][0]+ox, ctx['last_pos'][1]+oy)
                
                elif act == 'SCROLL':
                    try:
                        clicks = int(p.get('amount', 0))
                    except (ValueError, TypeError):
                        print("  [错误] SCROLL amount 参数无效")
                        break
                    try:
                        if 'x' in p and 'y' in p and str(p.get('x', '')).strip() and str(p.get('y', '')).strip():
                            pyautogui.moveTo(int(p['x']), int(p['y']))
                    except (ValueError, TypeError):
                        print("  [错误] SCROLL 坐标参数无效")
                        break
                    pyautogui.scroll(clicks) 
                
                elif act == 'WAIT':
                    # [P2防御] 防止 JSON 中 ms 为非数字字符串触发 traceback
                    try:
                        total_ms = int(p.get('ms', 0))
                    except (ValueError, TypeError):
                        print("  [错误] WAIT 参数 'ms' 必须是整数，步骤已跳过")
                        pc = next_pc; continue
                    if total_ms <= 0:
                        print("  [警告] WAIT 未指定有效等待时间，跳过")
                        pc = next_pc; continue
                    interrupted = False
                    for _ in range(0, total_ms, 100):
                        if ctx.get('stop_requested'):
                            interrupted = True
                            break
                        time.sleep(min(100, total_ms - _) / 1000.0)
                    if interrupted:
                        raise MacroStopException("用户在等待期间请求停止")
                
                elif act == 'TYPE_TEXT':
                    interval, ok = _safe_float(p.get('interval', 0.0), 0.0, min_value=0)
                    if not ok:
                        _warn_param_default('TYPE_TEXT', 'interval', interval)
                    text = p.get('text', '')
                    text = _render_vars(text, ctx)
                    
                    if not text:
                        print("  [警告] TYPE_TEXT 未指定输入文本，跳过")
                        pc = next_pc; continue
                    
                    if '{CLIPBOARD}' in text:
                        clipboard_content = ctx.get('clipboard_var', '')
                        if not clipboard_content:
                            try:
                                clipboard_content = pyperclip.paste()
                            except Exception:
                                clipboard_content = ''
                        
                        text = text.replace('{CLIPBOARD}', clipboard_content)
                        print(f"  [输入] 替换占位符: {text}")
                    
                    if interval > 0: pyautogui.write(text, interval=interval)
                    else:
                        copy_success = _copy_to_clipboard_with_retry(text, ctx)
                        
                        if copy_success:
                            time.sleep(0.1)
                            pyautogui.hotkey('ctrl', 'v')
                        else:
                            print("  [错误] 剪贴板复制失败，跳过粘贴")
                
                elif act == 'PRESS_KEY':
                    keys = [k for k in p.get('key', '').lower().replace(' ', '').split('+') if k]
                    if keys: pyautogui.hotkey(*keys)
                
                elif act == 'ACTIVATE_WINDOW':
                    if not PYGETWINDOW_AVAILABLE:
                        msg = "pygetwindow is unavailable; cannot activate target window"
                        if p.get('ignore_fail', False):
                            print(f"  [WARN] {msg}")
                            pc = next_pc; continue
                        raise RuntimeError(msg)
                    title = p.get('title')
                    if not title:
                        msg = "ACTIVATE_WINDOW is missing a window title"
                        if p.get('ignore_fail', False):
                            print(f"  [WARN] {msg}")
                            pc = next_pc; continue
                        raise RuntimeError(msg)
                    
                    try:
                        wins = gw.getWindowsWithTitle(title)
                        if not wins:
                            raise RuntimeError(f"No window title contains '{title}'")
                        
                        target_win = wins[0]
                        if target_win.isMinimized:
                            target_win.restore()
                        target_win.activate()
                        print(f"  [成功] 已激活窗口: {target_win.title}")
                        for _ in range(5):
                            if ctx.get('stop_requested'):
                                raise MacroStopException("用户在窗口激活期间请求停止")
                            time.sleep(0.1) 
                    except Exception as e:
                        if p.get('ignore_fail', False):
                            print(f"  [WARN] ACTIVATE_WINDOW failed and was ignored: {e}")
                            pc = next_pc; continue
                        raise RuntimeError(f"ACTIVATE_WINDOW failed: {e}") from e
                
                elif act == 'NOTE':
                    # 备注动作 - 仅打印注释，不执行任何操作
                    note_text = p.get('text', '')
                    if note_text:
                        print(f"  [备注] {note_text}")
                    # 注意：必须更新 pc，否则会无限循环
                    pc = next_pc
                    continue

                elif act == 'SET_VAR':
                    var_name = p.get('var_name', '').strip()
                    var_value = _render_vars(str(p.get('var_value', '')), ctx)
                    if var_name:
                        ctx['vars'][var_name] = var_value
                        print(f"  [变量] {var_name} = '{var_value}'")

                elif act == 'PROMPT_INPUT':
                    var_name = p.get('var_name', '').strip()
                    title = _render_vars(str(p.get('title', '宏助手输入')), ctx)
                    prompt = _render_vars(str(p.get('prompt', '请输入内容:')), ctx)
                    default_value = _render_vars(str(p.get('default_value', '')), ctx)
                    if not var_name:
                        print("  [错误] PROMPT_INPUT 缺少变量名")
                        break
                    if ctx.get('stop_requested'):
                        raise MacroStopException("用户在输入前请求停止")
                    callback = ctx.get('prompt_input_callback')
                    try:
                        if callable(callback):
                            value = callback(title, prompt, default_value, ctx)
                        else:
                            suffix = f" [{default_value}]" if default_value else ""
                            raw = input(f"{title} - {prompt}{suffix}: ")
                            value = default_value if raw == '' and default_value else raw
                    except KeyboardInterrupt as e:
                        raise MacroStopException("用户取消输入") from e
                    if value is None:
                        raise MacroStopException("用户取消输入")
                    ctx['vars'][var_name] = str(value)
                    print(f"  [变量] {var_name} = '{ctx['vars'][var_name]}' (用户输入)")
                         
                elif act == 'READ_FILE':
                    file_path = _render_vars(str(p.get('file_path', '')), ctx)
                    var_name = p.get('var_name', '').strip()
                    encoding = p.get('encoding', 'utf-8')
                    if not file_path or not var_name:
                        print("  [错误] READ_FILE 参数不完整")
                        break
                    try:
                        with open(file_path, 'r', encoding=encoding) as f:
                            ctx['vars'][var_name] = f.read()
                        print(f"  [变量] {var_name} 已读取文件 ({len(ctx['vars'][var_name])} 字符)")
                    except Exception as e:
                        print(f"  [错误] READ_FILE 读取失败: {e}")
                        if p.get('fail_stop', False): break
                        
                elif act == 'EXTRACT_VAR':
                    source = _render_vars(str(p.get('source_text', '')), ctx)
                    pattern = str(p.get('regex', ''))
                    var_name = p.get('var_name', '').strip()
                    if not pattern or not var_name:
                        print("  [错误] EXTRACT_VAR 参数不完整")
                        break
                    try:
                        match = re.search(pattern, source)
                        if match:
                            val = match.group(1) if match.lastindex else match.group(0)
                        else:
                            val = ''
                        ctx['vars'][var_name] = val
                        print(f"  [变量] {var_name} 提取为 '{val}'")
                    except Exception as e:
                        print(f"  [错误] EXTRACT_VAR 提取失败: {e}")
                        if p.get('fail_stop', False): break

                elif act == 'JSON_EXTRACT':
                    source = _render_vars(str(p.get('source_json', '')), ctx)
                    json_path = _render_vars(str(p.get('json_path', '')), ctx)
                    var_name = p.get('var_name', '').strip()
                    default_value = _render_vars(str(p.get('default_value', '')), ctx)
                    has_default = (
                        _parse_bool_param(p.get('use_default', False), False)
                        or ('default_value' in p and str(p.get('default_value', '')) != '')
                    )
                    if not source or not var_name:
                        print("  [错误] JSON_EXTRACT 参数不完整")
                        break
                    try:
                        val = _json_extract_value(source, json_path)
                        ctx['vars'][var_name] = val
                        print(f"  [变量] {var_name} = '{val}' (JSON)")
                    except Exception as e:
                        if has_default:
                            ctx['vars'][var_name] = default_value
                            print(f"  [JSON] 提取失败，使用默认值 '{default_value}': {e}")
                        else:
                            print(f"  [错误] JSON_EXTRACT 提取失败: {e}")
                            if p.get('fail_stop', False): break

                elif act == 'FOREACH_LINE':
                    next_pc = _handle_foreach_line_start(steps, pc, loops, p, ctx, status_callback)
                         
                elif act == 'IF_VAR':
                    var_val = _render_vars(str(p.get('var_value', '')), ctx)
                    op = p.get('operator', '==')
                    expected = _render_vars(str(p.get('expected_val', '')), ctx)
                    res_bool = _compare_values(var_val, op, expected)
                        
                    if not res_bool:
                        print(f"  -> IF条件不满足: '{var_val}' {op} '{expected}'")
                        next_pc = _find_jump(steps, pc, 'IF_', 'END_IF', ['ELSE', 'END_IF'])
                    else:
                        print(f"  -> IF条件满足: '{var_val}' {op} '{expected}'")

                elif act == 'CALCULATE':
                    expr = _render_vars(str(p.get('expression', '')), ctx)
                    var_name = p.get('var_name', '').strip()
                    if not expr or not var_name:
                        print("  [错误] CALCULATE 参数不完整")
                        break
                    try:
                        val = _safe_calculate_expression(expr)
                        ctx['vars'][var_name] = str(val)
                        print(f"  [变量] {var_name} = {val} (计算结果)")
                    except Exception as e:
                        print(f"  [错误] CALCULATE 计算失败 '{expr}': {e}")
                        if p.get('fail_stop', False): break
                        
                elif act == 'WRITE_FILE':
                    file_path = _render_vars(str(p.get('file_path', '')), ctx)
                    content = _render_vars(str(p.get('content', '')), ctx)
                    encoding = p.get('encoding', 'utf-8')
                    append = _parse_bool_param(p.get('append', False), False)
                    if not file_path:
                        print("  [错误] WRITE_FILE 未指定路径")
                        break
                    try:
                        mode = 'a' if append else 'w'
                        dir_path = os.path.dirname(file_path)
                        if dir_path and not os.path.exists(dir_path): os.makedirs(dir_path, exist_ok=True)
                        with open(file_path, mode, encoding=encoding) as f:
                            f.write(content)
                        print(f"  [写入] {file_path} (追加: {append})")
                    except Exception as e:
                        print(f"  [错误] WRITE_FILE 失败: {e}")
                        if p.get('fail_stop', False): break
                        
                elif act == 'GOTO_IF':
                    if loops:
                        raise RuntimeError("GOTO_IF 当前版本不允许在 LOOP 循环内部执行")
                    var_val = _render_vars(str(p.get('var_value', '')), ctx)
                    operator = p.get('operator', '==')
                    expected = _render_vars(str(p.get('expected_val', '')), ctx)
                    label = _render_vars(str(p.get('label', '')), ctx).strip()
                    
                    res_bool = _compare_values(var_val, operator, expected)
                        
                    if res_bool:
                        if not label:
                            raise RuntimeError("GOTO_IF 缺少标签名")
                        target = goto_labels.get(_normalize_goto_label(label))
                        if not target:
                            raise RuntimeError(f"GOTO_IF 找不到标签: {label}")
                        max_jumps, ok = _safe_int(p.get('max_jumps', GOTO_LABEL_DEFAULT_MAX_JUMPS), GOTO_LABEL_DEFAULT_MAX_JUMPS, min_value=1)
                        goto_counts = ctx.setdefault('_goto_counts', {})
                        count = goto_counts.get(pc, 0)
                        if count >= max_jumps:
                            raise RuntimeError(f"GOTO_IF 超过最大跳转次数 {max_jumps}: {label}")
                        goto_counts[pc] = count + 1
                        next_pc = target['index']
                        print(f"  [GOTO] 条件成立 ({var_val} {operator} {expected})，跳至 '{target['name']}'")
                    else:
                        print(f"  -> GOTO_IF 条件不满足 ({var_val} {operator} {expected})")

                elif act == 'GOTO_LABEL':
                    if loops:
                        raise RuntimeError("GOTO_LABEL 当前版本不允许在 LOOP 循环内部执行")
                    label = _render_vars(str(p.get('label', '')), ctx).strip()
                    if not label:
                        raise RuntimeError("GOTO_LABEL 缺少标签名")
                    target = goto_labels.get(_normalize_goto_label(label))
                    if not target:
                        raise RuntimeError(f"GOTO_LABEL 找不到标签: {label}")
                    max_jumps, ok = _safe_int(
                        p.get('max_jumps', GOTO_LABEL_DEFAULT_MAX_JUMPS),
                        GOTO_LABEL_DEFAULT_MAX_JUMPS,
                        min_value=1
                    )
                    if not ok:
                        _warn_param_default('GOTO_LABEL', 'max_jumps', max_jumps)
                    goto_counts = ctx.setdefault('_goto_counts', {})
                    count = goto_counts.get(pc, 0)
                    if count >= max_jumps:
                        raise RuntimeError(f"GOTO_LABEL 超过最大跳转次数 {max_jumps}: {label}")
                    goto_counts[pc] = count + 1
                    next_pc = target['index']
                    print(f"  [GOTO] 跳转到标签 '{target['name']}' -> 第 {next_pc + 1} 步")

                elif act == 'RUN':
                    # 执行命令/脚本/文件
                    run_result = _handle_run(p, ctx)
                    if run_result == 'SKIPPED':
                        print("  [RUN] 已跳过，继续执行后续步骤")
                    # 如果返回 False，表示执行失败
                    elif run_result is False:
                        print("  [RUN] 执行失败")
                        # 可配置：是否失败时停止（默认不中断，避免误停）
                        if p.get('fail_stop', False):
                            break
                    else:
                        print(f"  [RUN] 执行成功")

                elif act == 'ELSE': 
                    next_pc = _find_jump(steps, pc, 'IF_', 'END_IF', ['END_IF'])
                
                elif act == 'LOOP_START':
                    next_pc = _handle_loop_start(steps, pc, loops, p, ctx, status_callback)
                
                elif act == 'END_LOOP':
                    # === 核心修复: 统一处理条件循环 ===
                    if loops:
                        top = loops[-1]
                        if top.get('kind') == 'foreach_line':
                            raise RuntimeError("END_LOOP 不能结束批量处理，请使用 END_FOREACH")
                        mode = top.get('mode', 'fixed')
                        
                        # 条件循环: 先增加计数,再检查条件
                        if mode in ('until_image', 'until_text'):
                            top['iteration'] += 1  # <--- 先增加计数
                            
                            # 更新状态显示
                            if status_callback:
                                status_callback(f"🔄 循环第 {top['iteration']} 次 (最多 {top['max_iterations']} 次)")
                            
                            # 检查是否超过最大次数 (安全阀)
                            if top['iteration'] >= top['max_iterations']:
                                loop_id_to_exit = loops.pop()['id']
                                loop_cache.exit()
                                loop_cache.clear_cache(loop_id_to_exit)
                                if status_callback:
                                    status_callback(f"WARN 达到最大迭代 {top['max_iterations']} 次,强制退出")
                                print(f"  [Loop Until] WARN 达到最大迭代次数,强制退出")
                                next_pc = pc + 1  # 继续执行下一步
                            else:
                                # 检查退出条件
                                condition_met = _check_loop_condition(top, ctx)
                                if condition_met:
                                    # OK 条件满足, 退出循环
                                    loop_id_to_exit = loops.pop()['id']
                                    loop_cache.exit()
                                    loop_cache.clear_cache(loop_id_to_exit)
                                    if status_callback:
                                        status_callback(f"OK 条件满足,循环结束 (共 {top['iteration']} 次)")
                                    print(f"  [Loop Until] OK 条件满足,循环结束")
                                    next_pc = pc + 1  # 继续执行下一步
                                else:
                                    # FAIL 条件未满足, 继续循环
                                    print(f"  [Loop Until] FAIL 未找到目标,继续循环 (第 {top['iteration']} 次)")
                                    
                                    # 使用可配置的检测间隔，平衡速度与准确率
                                    # 0.15s 经过实测：既不会让UI卡顿，也能及时检测到目标
                                    time.sleep(LOOP_CHECK_INTERVAL)
                                    
                                    next_pc = top['start']  # 跳回循环开始
                        else:
                            # 固定次数循环, 直接返回开始
                            next_pc = top['start']
                    else:
                        print("[错误] END_LOOP 缺少对应的 LOOP_START")
                        next_pc = pc + 1  # 继续执行下一步

                elif act == 'END_FOREACH':
                    if loops and loops[-1].get('kind') == 'foreach_line':
                        next_pc = loops[-1]['start']
                    elif loops:
                        raise RuntimeError("END_FOREACH 不能结束普通循环，请使用 END_LOOP")
                    else:
                        print("[错误] END_FOREACH 缺少对应的批量处理开始")
                        next_pc = pc + 1

            except pyautogui.FailSafeException as e:
                raise MacroStopException("PyAutoGUI failsafe triggered") from e
            except MacroStopException:
                raise  # 向上传播，不要吞掉
            except Exception as e:
                error_msg = f"  [执行异常] 步骤 {pc+1} ({act}): {e}"
                print(error_msg)
                if status_callback:
                    status_callback(f"ERR {error_msg}")
                break
            pc = next_pc
        
        return pc >= len(steps)
    finally:
        cleanup_active_processes(ctx)
        loop_cache.reset()
        print(f"--- 执行结束 ---\n[统计] {perf.get_stats()}\n")

def _handle_find(act, p, ctx, in_loop):
    is_img = 'IMAGE' in act
    final_engine = FORCE_OCR_ENGINE if (FORCE_OCR_ENGINE and FORCE_OCR_ENGINE != 'auto') else p.get('engine', 'auto')
    sig = f"{act}_{p.get('path', p.get('text',''))}"
    
    region = None
    is_manual_region = False
    runtime_cache_boxes = ctx.setdefault('_runtime_cache_boxes', {})
    if p.get('region') is not None:
        region = bbox_to_region(p.get('region'))
        if region is None:
            raise ValueError(f"手动查找区域解析失败(格式错误): {p.get('region')}")
        is_manual_region = True
    if not region:
        cb_raw = runtime_cache_boxes.get(sig, p.get('cache_box'))
        region = _padded_bbox_to_region(cb_raw, CACHE_BOX_PADDING)

    # 引入 try...finally 结构以保证 ss 得到显式释放
    ss = None
    try:
        ss, offset = smart_screenshot(region)

        # 增强模式：IF 动作和多缩放尝试（性能开销大，但更准确）
        enhanced_mode = ctx.get('enhanced_mode', False)

        if in_loop:
            cached = loop_cache.get(sig)
            if cached and is_img:
                confidence, ok = _safe_float(p.get('confidence', 0.8), 0.8, min_value=0, max_value=1)
                if not ok:
                    _warn_param_default(act, 'confidence', confidence)
                if quick_check_cv2(p.get('path',''), confidence, ss, offset, cached, enhanced_mode):
                    perf.record_hit(True, False); print(f"  [Loop缓存] {cached}"); ctx['last_pos'] = cached; return cached

        res = _do_find(is_img, p, ss, offset, final_engine, ctx)

        # IF 动作不使用全局 fallback（会大幅降低性能，因为它已经重复搜索了）
        # FIND_TEXT/FIND_IMAGE 才使用 fallback
        if not res and region and ENABLE_GLOBAL_FALLBACK and enhanced_mode and not act.startswith('IF_') and not is_manual_region:
            print("  [缓存失效] 全局搜索...")
            # 释放旧的 ss，防止覆盖导致句柄丢失泄漏
            if ss:
                try: ss.close()
                except Exception: pass
                ss = None
            ss, offset = smart_screenshot(None)
            res = _do_find(is_img, p, ss, offset, final_engine, ctx)
            if res:
                if len(res) >= 2:
                    runtime_cache_boxes[sig] = [res[0]-20, res[1]-10, res[0]+20, res[1]+10]

        if res:
            pos = (res[0], res[1])
            if in_loop: loop_cache.set(sig, pos)
            ctx['last_pos'] = pos
            if act in ('FIND_TEXT', 'IF_TEXT_FOUND') and len(res) >= 3:
                save_var = p.get('save_to_var', '').strip()
                if save_var:
                    ctx.setdefault('vars', {})[save_var] = res[2]
                    print(f"  [变量] {save_var} = '{res[2]}'")
            return res

        perf.record_miss(not is_img)
        return None
    finally:
        if ss:
            try: ss.close()
            except Exception: pass

def _do_find(is_img, p, ss, offset, engine='auto', ctx=None):
    """执行查找（图像或文本）并返回统一格式坐标 (x, y)"""
    enhanced_mode = ctx.get('enhanced_mode', False) if ctx else False
    if is_img:
        # 图片查找返回: (cx, cy, w, h)
        confidence, ok = _safe_float(p.get('confidence', 0.8), 0.8, min_value=0, max_value=1)
        if not ok:
            _warn_param_default('FIND_IMAGE', 'confidence', confidence)
        res_val = find_image_cv2(p.get('path',''), confidence, ss, offset, enhanced_mode)
        if res_val:
            perf.record_hit(False, False)
            print(f"  [找到] 图 ({res_val[0][0]},{res_val[0][1]})")
            return (res_val[0][0], res_val[0][1]) 
    else:
        # OCR 查找返回: ((cx, cy), full_text)
        res = ocr_engine.find_text_location(
            p.get('text',''), 
            p.get('lang','eng'), 
            p.get('debug',True), 
            ss, offset, engine, enhanced_mode
        )
        
        if res:
            perf.record_hit(False, True)
            
            # === [修复] 统一返回格式为扁平元组: (x, y, text) ===
            pos = (0, 0)
            text_content = ""

            # 解析 ocr_engine 的返回值
            if isinstance(res, tuple) and len(res) == 2:
                if isinstance(res[0], tuple) and len(res[0]) >= 2:
                    # 新格式: ((x, y), full_text)
                    pos = res[0]
                    text_content = res[1]
                else:
                    # 旧格式兼容: (x, y)
                    pos = res
                    text_content = p.get('text', '')
            else:
                pos = res
                text_content = p.get('text', '')

            # 打印调试信息
            print(f"  [找到] 文 ({pos[0]},{pos[1]}) 内容: '{text_content}'")

            final_text = text_content
            extract_pattern = p.get('extract_pattern', '').strip()
            if extract_pattern and ctx and (p.get('save_to_clipboard', False) or p.get('save_to_var', '').strip()):
                try:
                    match = re.search(extract_pattern, text_content)
                    if match:
                        if match.lastindex:
                            final_text = match.group(1)
                        else:
                            final_text = match.group(0)
                        print(f"  [正则提取] '{final_text}'")
                    else:
                        print(f"  [正则] 未匹配，保留原文")
                except Exception as e:
                    print(f"  [正则错误] {e}")

            # 处理剪贴板逻辑 (副作用)
            if ctx and p.get('save_to_clipboard', False):
                print(f"  [剪贴板] 原始文本: '{text_content}'")
                ctx['clipboard_var'] = final_text
                try:
                    if not _copy_to_clipboard_with_retry(final_text, ctx):
                        raise RuntimeError("clipboard is busy")
                    print(f"  [剪贴板] OK 已复制")
                except Exception as e:
                    print(f"  [剪贴板] 失败: {e}")
            
            return (pos[0], pos[1], final_text)
    
    return None


def _handle_foreach_line_start(steps, pc, loops, p, ctx, cb):
    top = loops[-1] if loops else None

    if top and top.get('kind') == 'foreach_line' and top.get('start') == pc:
        time.sleep(LOOP_PHYSICAL_COOLDOWN)
        next_index = top.get('index', 0) + 1
        if next_index >= len(top.get('items', [])):
            loops.pop()
            if cb:
                cb("批量处理完成")
            print("  [批量] 所有行已处理完成")
            return _find_jump(steps, pc, 'FOREACH_LINE', 'END_FOREACH', ['END_FOREACH'])

        top['index'] = next_index
        _set_foreach_line_vars(top, ctx)
        if cb:
            cb(f"批量处理第 {next_index + 1}/{len(top['items'])} 行")
        print(f"  [批量] 第 {next_index + 1}/{len(top['items'])} 行")
        return pc + 1

    if loops:
        raise RuntimeError("批量处理暂不支持嵌套在其他循环内部")

    file_path = _render_vars(str(p.get('file_path', '')), ctx).strip()
    source_text = _render_vars(str(p.get('source_text', '')), ctx)
    encoding = p.get('encoding', 'utf-8')

    if file_path:
        with open(file_path, 'r', encoding=encoding) as f:
            source_text = f.read()

    if source_text is None or source_text == '':
        print("  [批量] 数据为空，跳过批量处理块")
        return _find_jump(steps, pc, 'FOREACH_LINE', 'END_FOREACH', ['END_FOREACH'])

    skip_empty = _parse_bool_param(p.get('skip_empty', True), True)
    lines = source_text.splitlines()
    if skip_empty:
        lines = [line for line in lines if line.strip()]

    max_lines, ok = _safe_int(p.get('max_lines', 10000), 10000, min_value=1)
    if not ok:
        _warn_param_default('FOREACH_LINE', 'max_lines', max_lines)
    if len(lines) > max_lines:
        raise RuntimeError(f"批量处理行数 {len(lines)} 超过安全上限 {max_lines}")

    if not lines:
        print("  [批量] 没有可处理的行，跳过批量处理块")
        return _find_jump(steps, pc, 'FOREACH_LINE', 'END_FOREACH', ['END_FOREACH'])

    loop_data = {
        'kind': 'foreach_line',
        'start': pc,
        'index': 0,
        'items': lines,
        'current_line_var': str(p.get('current_line_var', 'current_line')).strip() or 'current_line',
        'index_var': str(p.get('index_var', 'loop_index')).strip() or 'loop_index',
        'total_var': str(p.get('total_var', 'loop_total')).strip() or 'loop_total',
        'delimiter': _normalize_split_delimiter(_render_vars(str(p.get('split_delimiter', '')), ctx)),
        'field_names': _split_field_names(p.get('field_names', '')),
        'strip_fields': _parse_bool_param(p.get('strip_fields', True), True),
    }
    loops.append(loop_data)
    _set_foreach_line_vars(loop_data, ctx)
    if cb:
        cb(f"批量处理第 1/{len(lines)} 行")
    print(f"  [批量] 开始处理 {len(lines)} 行")
    return pc + 1


def _handle_loop_start(steps, pc, loops, p, ctx, cb):
    top = loops[-1] if loops else None
    
    
    # 如果是已有循环的迭代检查
    if top and top['start'] == pc:
         # === [修复] 强制给循环加一个物理冷却，防止队列瞬间爆炸 ===
        time.sleep(LOOP_PHYSICAL_COOLDOWN)  # 使用常量 
        mode = top.get('mode', 'fixed')
        
        # 检查是否超过最大迭代次数 (所有模式通用)
        if top['iteration'] >= top['max_iterations']:
            loop_id_to_exit = loops.pop()['id']
            loop_cache.exit()
            loop_cache.clear_cache(loop_id_to_exit)
            if cb: cb(f"达到最大迭代 {top['max_iterations']} 次,循环结束")
            print(f"  [Loop] 警告:达到最大迭代次数 {top['max_iterations']}")
            return _find_jump(steps, pc, 'LOOP_START', 'END_LOOP', ['END_LOOP'])
        
        # 固定次数循环:检查剩余次数
        if mode == 'fixed':
            # [修复BUG-4] iteration 从初始化的 1 开始，每次回到 LOOP_START 时递增
            if top['remain'] > 0:
                top['remain'] -= 1
                top['iteration'] += 1
                total = top.get('total_count', top['iteration'] + top['remain'])
                if cb: cb(f"循环第 {top['iteration']} 次 (共 {total} 次)")
                return pc + 1
            else:
                loop_id_to_exit = loops.pop()['id']
                loop_cache.exit()
                loop_cache.clear_cache(loop_id_to_exit)
                return _find_jump(steps, pc, 'LOOP_START', 'END_LOOP', ['END_LOOP'])
        
        # === 关键修复: 条件循环不在此增加计数,交给 END_LOOP ===
        # 条件循环的迭代计数和退出判断统一在 END_LOOP 处理
        return pc + 1

    if loops and top and top.get('kind') == 'foreach_line':
        raise RuntimeError("普通循环暂不支持嵌套在批量处理内部")
    
    # 新循环初始化
    else:
        mode = p.get('mode', 'fixed')
        # [P2防御] 防止外部 JSON 中 max_iterations 为非数字字符串
        try:
            max_iter = int(p.get('max_iterations', 1000))
        except (ValueError, TypeError):
            print("  [警告] LOOP_START 参数 'max_iterations' 非法，已使用默认值 1000")
            max_iter = 1000
        
        if mode == 'fixed':
            # [P2防御] 防止外部 JSON 中 times 为非数字字符串
            try:
                count = int(p.get('times', 1))
            except (ValueError, TypeError):
                print("  [错误] LOOP_START 参数 'times' 必须是整数，循环已跳过")
                return _find_jump(steps, pc, 'LOOP_START', 'END_LOOP', ['END_LOOP'])
            if count <= 0:
                return _find_jump(steps, pc, 'LOOP_START', 'END_LOOP', ['END_LOOP'])
            remain = count - 1
        else:
            count = max_iter  # 条件循环用 max_iter 作为参考
            remain = max_iter
        
        loop_id = f"L{pc}_{len(loops)}"
        loop_data = {
            'start': pc,
            'remain': remain,
            'id': loop_id,
            'mode': mode,
            'iteration': 0 if mode in ('until_image', 'until_text') else 1,
            'total_count': count,  # [修复BUG-4] 保存总次数，供状态显示
            'max_iterations': max_iter
        }
        if p.get('region') is not None:
            loop_data['region'] = p['region']
        
        # 保存条件参数
        if mode == 'until_image':
            loop_data['condition_image'] = p.get('condition_image', '')
            confidence, ok = _safe_float(p.get('confidence', 0.8), 0.8, min_value=0, max_value=1)
            if not ok:
                _warn_param_default('LOOP_START', 'confidence', confidence)
            loop_data['confidence'] = confidence
            # [优化] 保存搜索区域，加速条件检测
            if 'region' in p:
                print(f"  [Loop Until Image] 目标: {loop_data['condition_image']} (区域: {p['region']})")
            elif 'cache_box' in p:
                loop_data['cache_box'] = p['cache_box']
                print(f"  [Loop Until Image] 目标: {loop_data['condition_image']} (区域: {p['cache_box']})")
            else:
                print(f"  [Loop Until Image] 目标: {loop_data['condition_image']} (全屏)")
        elif mode == 'until_text':
            loop_data['condition_text'] = p.get('condition_text', '')
            loop_data['lang'] = p.get('lang', 'eng')
            # [优化] 保存搜索区域，加速条件检测
            if 'region' in p:
                print(f"  [Loop Until Text] 目标: {loop_data['condition_text']} (区域: {p['region']})")
            elif 'cache_box' in p:
                loop_data['cache_box'] = p['cache_box']
                print(f"  [Loop Until Text] 目标: {loop_data['condition_text']} (区域: {p['cache_box']})")
            else:
                print(f"  [Loop Until Text] 目标: {loop_data['condition_text']} (全屏)")
        
        loops.append(loop_data)
        loop_cache.enter(loop_id)
        
        if mode == 'fixed':
            if cb: cb(f"循环第 1 次 (共 {count} 次)")
        else:
            if cb: cb(f"🔄 条件循环第 1 次 (最多 {max_iter} 次)")
        
        return pc + 1

def _find_jump(steps, start, open_tag, close_tag, targets):
    lvl = 0
    for i in range(start + 1, len(steps)):
        a = steps[i].get('action','')
        if a.startswith(open_tag.rstrip('_')): lvl += 1
        elif a == close_tag:
            if lvl == 0 and a in targets: return i + 1
            lvl -= 1
        elif lvl == 0 and a in targets: return i + 1
    return len(steps)

def _check_loop_condition(loop_data, ctx):
    """检查循环退出条件是否满足
    
    返回值:
    - True: 找到了目标(应该退出循环)
    - False: 没找到(应该继续循环)
    """
    mode = loop_data.get('mode', 'fixed')
    
    # [优化] 统一构建截图区域（支持 cache_box 缩小截图范围）
    def _build_region(ld):
        if ld.get('region') is not None:
            region = bbox_to_region(ld.get('region'))
            if region is None:
                raise LoopConditionCheckError(f"Loop region is invalid: {ld.get('region')}")
            return region
        return _padded_bbox_to_region(ld.get('cache_box'), CACHE_BOX_PADDING)

    if mode == 'until_image':
        path = loop_data.get('condition_image', '')
        conf = loop_data.get('confidence', 0.8)
        
        if not path or not os.path.exists(path):
            print(f"  [Loop Until] 警告: 图像路径无效 '{path}'")
            return False
        
        ss = None
        try:
            region = _build_region(loop_data)
            ss, offset = smart_screenshot(region)
            enhanced_mode = ctx.get('enhanced_mode', False) if ctx else False
            res_val = find_image_cv2(path, conf, ss, offset=offset, enhanced_mode=enhanced_mode)
            found = res_val is not None
            if found:
                print(f"  [Loop Until] OK 找到目标图像: {os.path.basename(path)}")
            return found
        except (ValueError, TypeError, AttributeError, IndexError) + ((cv2.error,) if OPENCV_AVAILABLE else ()) as e:
            print(f"  [Loop Until] 图像检测错误: {e}")
            return False
        except Exception as e:
            print(f"  [Loop Until] 严重错误 (退出循环): {e}")
            raise LoopConditionCheckError("Image loop condition check failed") from e
        finally:
            if ss:
                try: ss.close()
                except Exception: pass
    
    elif mode == 'until_text':
        text = loop_data.get('condition_text', '')
        lang = loop_data.get('lang', 'eng')
        
        if not text:
            print(f"  [Loop Until] 警告: 文本条件为空")
            return False
        
        ss = None
        try:
            region = _build_region(loop_data)
            ss, offset = smart_screenshot(region)
            enhanced_mode = ctx.get('enhanced_mode', False) if ctx else False
            res = ocr_engine.find_text_location(text, lang, False, ss, offset, 'auto', enhanced_mode)
            
            if res:
                found_txt = text
                if isinstance(res, tuple) and len(res) == 2 and isinstance(res[1], str):
                    found_txt = res[1]
                print(f"  [Loop Until] OK 找到目标文本: '{found_txt[:50]}'")
                return True
            return False
        except (ValueError, TypeError, AttributeError) as e:
            print(f"  [Loop Until] 文本检测错误: {e}")
            return False
        except Exception as e:
            print(f"  [Loop Until] 严重错误 (退出循环): {e}")
            raise LoopConditionCheckError("Text loop condition check failed") from e
        finally:
            if ss:
                try: ss.close()
                except Exception: pass
    
    return False

core_engine_version = f"1.7.1 Beta (Core) / OpenCV: {OPENCV_AVAILABLE}"

# ======================================================================
# RUN 处理函数：执行命令/脚本/文件
# ======================================================================
def _split_command_line(text):
    """Split a command line without invoking a shell."""
    text = str(text or '').strip()
    if not text:
        return []
    if sys.platform == 'win32':
        argc = ctypes.c_int()
        argv = ctypes.windll.shell32.CommandLineToArgvW(text, ctypes.byref(argc))
        if argv:
            try:
                return [argv[i] for i in range(argc.value)]
            finally:
                ctypes.windll.kernel32.LocalFree(argv)
    return shlex.split(text, posix=(sys.platform != 'win32'))

def _build_run_command(command, args):
    cmd_list = _split_command_line(command)
    if args:
        cmd_list.extend(_split_command_line(args))
    return cmd_list

def _register_active_process(ctx, process):
    lock = ctx.setdefault('_active_process_lock', threading.RLock())
    active = ctx.setdefault('_active_processes', set())
    with lock:
        active.add(process)

def _unregister_active_process(ctx, process):
    lock = ctx.setdefault('_active_process_lock', threading.RLock())
    active = ctx.setdefault('_active_processes', set())
    with lock:
        active.discard(process)

def terminate_process_tree(process, wait_timeout=0.5):
    if process is None or process.poll() is not None:
        return

    if sys.platform == 'win32':
        try:
            subprocess.run(
                ['taskkill', '/PID', str(process.pid), '/T', '/F'],
                capture_output=True,
                text=True,
                timeout=max(2, wait_timeout + 1)
            )
            process.wait(timeout=wait_timeout)
            return
        except Exception:
            pass

    try:
        process.terminate()
        process.wait(timeout=wait_timeout)
        return
    except Exception:
        pass

    if process.poll() is None:
        try:
            process.kill()
            process.wait(timeout=wait_timeout)
        except Exception:
            pass

def cleanup_active_processes(ctx):
    if not ctx:
        return
    lock = ctx.setdefault('_active_process_lock', threading.RLock())
    active = ctx.setdefault('_active_processes', set())
    with lock:
        processes = list(active)
    for process in processes:
        terminate_process_tree(process)
        _unregister_active_process(ctx, process)

def _execute_subprocess(cmd_list, shell_mode, cwd, timeout, save_output, ctx, run_mode_name):
    """提取的通用子进程执行与输出处理逻辑"""
    process = None
    try:
        process = subprocess.Popen(
            cmd_list,
            shell=shell_mode,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',
            cwd=cwd if cwd else None
        )
        _register_active_process(ctx, process)

        deadline = time.monotonic() + timeout
        while True:
            if ctx.get('stop_requested'):
                raise MacroStopException("Stop requested while RUN process is active")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                terminate_process_tree(process)
                print(f"  [RUN] {run_mode_name}执行超时 ({timeout}秒)")
                return False
            try:
                stdout, stderr = process.communicate(timeout=min(0.1, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
        
        output = stdout.strip() if stdout else stderr.strip()
        
        if process.returncode == 0:
            print(f"  [RUN] {run_mode_name}执行成功")
            if output:
                print(f"        输出: {_console_safe_text(output[:200])}")
            if save_output and output:
                ctx['clipboard_var'] = output
                try:
                    if not _copy_to_clipboard_with_retry(output, ctx):
                        raise RuntimeError("clipboard is busy")
                    print(f"        已保存到剪贴板")
                except Exception:
                    pass
            return True
        else:
            print(f"  [RUN] {run_mode_name}执行失败 (退出码: {process.returncode})")
            if output:
                print(f"        错误: {_console_safe_text(output[:200])}")
            return False
            
    except MacroStopException:
        terminate_process_tree(process)
        raise
    except Exception as e:
        terminate_process_tree(process)
        print(f"  [RUN] {run_mode_name}执行错误: {e}")
        return False
    finally:
        if process is not None:
            _unregister_active_process(ctx, process)
            if process.poll() is not None:
                for stream in (process.stdout, process.stderr):
                    if stream and not stream.closed:
                        stream.close()

def _handle_run(p, ctx):
    """执行命令、脚本或写入文件
    
    参数:
        run_type: 类型 ("command" | "script" | "file")
        command: 命令 (run_type=command)
        script_path: 脚本路径 (run_type=script)
        interpreter: 解释器 (run_type=script, 默认 python)
        file_path: 文件路径 (run_type=file)
        content: 文件内容 (run_type=file)
        args: 命令/脚本参数
        timeout: 超时秒数 (默认 30)
        cwd: 工作目录
        append: 追加模式 (run_type=file)
        save_output: 保存输出到剪贴板
    
    返回:
        bool | str:
            True: 成功
            False: 失败
            'SKIPPED': 被策略跳过（例如 run_enabled=False）
    """
    if not ctx.get('run_enabled', False):
        print("  [RUN] 已跳过（执行外部命令默认已禁用，请在设置中手动开启）")
        return 'SKIPPED'

    p = {k: _render_vars(v, ctx) if isinstance(v, str) else v for k, v in p.items()}
    run_type = p.get('run_type', 'command')
    
    # === 文件写入模式 ===
    if run_type == 'file':
        file_path = p.get('file_path', '')
        if not file_path:
            print("  [RUN] 错误: 未指定文件路径")
            return False
        
        content = p.get('content', '')
        
        # 支持占位符替换
        content = content.replace('{CLIPBOARD}', ctx.get('clipboard_var', ''))
        content = content.replace('{DATETIME}', time.strftime('%Y-%m-%d %H:%M:%S'))
        
        try:
            mode = 'a' if p.get('append', False) else 'w'
            encoding = p.get('encoding', 'utf-8')
            
            # 检查目录是否存在
            dir_path = os.path.dirname(file_path)
            if dir_path and not os.path.exists(dir_path):
                os.makedirs(dir_path, exist_ok=True)
                print(f"  [RUN] 已创建目录: {dir_path}")
            
            with open(file_path, mode, encoding=encoding) as f:
                f.write(content)
            
            print(f"  [RUN] 已写入文件: {file_path}")
            return True
            
        except Exception as e:
            print(f"  [RUN] 写入文件失败: {e}")
            return False
    
    # === 命令执行模式 ===
    elif run_type == 'command':
        command = p.get('command', '')
        args = p.get('args', '')
        # [P2防御] 防止外部 JSON 中 timeout 为非数字字符串
        try:
            timeout = int(p.get('timeout', 30))
            if timeout <= 0:
                raise ValueError("timeout 必须大于 0")
        except (ValueError, TypeError):
            print("  [错误] RUN 参数 'timeout' 必须是正整数，已使用默认值 30")
            timeout = 30
        cwd = p.get('cwd', None)
        save_output = p.get('save_output', False)
        shell_mode = bool(p.get('shell_mode', False))
        
        if not command:
            print("  [RUN] 错误: 未指定命令")
            return False
        
        if shell_mode:
            print("  [RUN] 警告: 已启用 shell 模式，请仅运行可信宏")
            run_cmd = f"{command} {args}" if args else command
        else:
            try:
                run_cmd = _build_run_command(command, args)
            except ValueError as e:
                print(f"  [RUN] 命令参数解析失败: {e}")
                return False
            if not run_cmd:
                print("  [RUN] 错误: 命令为空")
                return False
        
        return _execute_subprocess(run_cmd, shell_mode, cwd, timeout, save_output, ctx, "命令")
    
    # === 脚本执行模式 ===
    elif run_type == 'script':
        script_path = p.get('script_path', '')
        interpreter = p.get('interpreter', 'python')
        args = p.get('args', '')
        # [P2防御] script 模式同样防御 timeout 非法值
        try:
            timeout = int(p.get('timeout', 30))
            if timeout <= 0:
                raise ValueError("timeout 必须大于 0")
        except (ValueError, TypeError):
            print("  [错误] RUN 参数 'timeout' 必须是正整数，已使用默认值 30")
            timeout = 30
        cwd = p.get('cwd', None)
        save_output = p.get('save_output', False)
        
        if not script_path:
            print("  [RUN] 错误: 未指定脚本路径")
            return False
        
        # 检查脚本文件是否存在
        if not os.path.exists(script_path):
            print(f"  [RUN] 错误: 脚本文件不存在: {script_path}")
            return False
        
        # 解释器映射
        INTERPRETERS = {
            'python': 'python',
            'python3': 'python',
            'node': 'node',
            'powershell': 'powershell',
            'cmd': 'cmd',
            'bat': 'cmd',
        }
        cmd = INTERPRETERS.get(interpreter, interpreter)
        
        cmd_list = [cmd, script_path]
        if args:
            try:
                cmd_list.extend(_split_command_line(args))
            except ValueError:
                cmd_list.append(args)
        
        return _execute_subprocess(cmd_list, False, cwd, timeout, save_output, ctx, "脚本")
    
    # 未知类型
    else:
        print(f"  [RUN] 错误: 未知的 run_type: {run_type}")
        return False


# ======================================================================
# 宏数据校验（从 MacroAssistant.py 迁移，完成 1.7.0 Beta 中声明的迁移）
# ======================================================================
def validate_macro_data(data):
    """
    验证宏数据结构是否有效

    Args:
        data: 从 JSON 加载的数据

    Returns:
        bool: 数据是否有效
    """
    # 必须是列表
    if not isinstance(data, list):
        print("[验证失败] 根对象不是列表")
        return False

    # 验证每个步骤的基本结构
    for i, step in enumerate(data):
        # 必须是字典
        if not isinstance(step, dict):
            print(f"[验证失败] 步骤 {i+1} 不是字典对象")
            return False

        # 必须包含 'action' 字段
        if 'action' not in step:
            print(f"[验证失败] 步骤 {i+1} 缺少 'action' 字段")
            return False

        # 必须包含 'params' 字段且为字典
        if 'params' not in step or not isinstance(step['params'], dict):
            print(f"[验证失败] 步骤 {i+1} 缺少 'params' 字段或格式错误")
            return False

        # 验证 action 是否是已知的动作类型（仅警告，不阻止加载）
        if step['action'] not in MacroSchema.ACTION_TRANSLATIONS:
            print(f"[警告] 步骤 {i+1} 包含未知的动作类型: {step['action']}")
            # 不返回 False，允许加载未知动作类型（向前兼容）

    return True
