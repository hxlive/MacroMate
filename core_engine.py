# -*- coding: utf-8 -*-
# core_engine.py
# 描述:自动化宏的核心功能引擎
# 版本:1.6.5
# # 变更:(修复) 新增 MacroStopException，实现快捷键即时中断

# ======================================================================
# 即时中断异常
# ======================================================================
class MacroStopException(BaseException):
    """快捷键触发时注入到执行线程的异常，强制立刻中断宏。
    继承 BaseException 而非 Exception，确保不被 except Exception 误吞。
    """
    pass

import pyautogui
import time
from PIL import Image, ImageGrab, ImageStat
import re
import pyperclip
import os
import sys
import subprocess
from collections import defaultdict
import functools 

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
TEMPLATE_CACHE_SIZE = 500  # 模板缓存大小（优化：从100增加到500）
QUICK_CHECK_SCALES = [1.0, 0.9, 1.1]  # 快速检查尝试的缩放比例



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
    print("[配置] OK OpenCV 引擎就绪 (极速找图内核已可用)")
except ImportError:
    OPENCV_AVAILABLE = False
    print("[配置] FAIL 未找到 OpenCV。将回退到慢速找图模式。")

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
    
    try:
        if sys.platform == 'win32':
            import win32con
            PYNPUT_MOD_TO_WIN_MOD = {
                'ctrl': win32con.MOD_CONTROL, 'alt': win32con.MOD_ALT,
                'shift': win32con.MOD_SHIFT, 'cmd': win32con.MOD_WIN,
            }
        else:
            PYNPUT_MOD_TO_WIN_MOD = {}
    except ImportError:
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
        except:
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
        'IF_IMAGE_FOUND': '13. IF 找到图像',
        'IF_TEXT_FOUND':  '14. IF 找到文本',
        'ELSE':           '15. ELSE',
        'END_IF':         '16. END_IF',
        'RUN':           '17. 执行命令/脚本/文件',
        'LOOP_START':     '18. 循环开始 (Loop)',
        'END_LOOP':       '19. 结束循环 (EndLoop)',
    }
    ACTION_KEYS_TO_NAME = {v: k for k, v in ACTION_TRANSLATIONS.items()}
    
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
def smart_screenshot(region=None):
    if region:
        pad = 0
        x = max(0, region[0] - pad)
        y = max(0, region[1] - pad)
        return ImageGrab.grab(bbox=(x, y, region[0]+region[2]+pad, region[1]+region[3]+pad)), (x, y)
    return ImageGrab.grab(), (0, 0)

SCALES = [1.0, 0.9, 1.1, 0.8, 1.2]
@functools.lru_cache(maxsize=500)  # [优化] 增大缓存以减少文件读取
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
            _, max_v, _, _ = cv2.minMaxLoc(cv2.matchTemplate(crop, tmpl, cv2.TM_CCOEFF_NORMED))
            
            if max_v >= conf:
                return True  # 找到匹配，立即返回
        
        return False  # 所有缩放比例都不匹配
    except (cv2.error, ValueError, TypeError, AttributeError, IndexError) as e:
        # [补丁优化] 记录异常详情，便于调试
        print(f"[quick_check_cv2] 异常: {e}")
        import traceback
        traceback.print_exc()
        return False

# ======================================================================
# 主执行引擎
# ======================================================================
def execute_steps(steps, run_context=None, status_callback=None):
    print(f"\n--- 宏执行开始 (Core V1.6.0) ---")
    perf.reset(); loop_cache.reset()
    ctx = run_context if run_context else {}
    ctx.setdefault('last_pos', (None, None))
    ctx.setdefault('stop_requested', False)
    ctx.setdefault('clipboard_var', '')
    
    default_stop = "Ctrl+F11"
    try:
        s = run_context.get('stop_key_str', default_stop)
        stop_key_display = HotkeyUtils.format_hotkey_display(s)
    except:
        stop_key_display = default_stop
    
    pc, loops = 0, []
    try:
        while pc < len(steps):

            if ctx.get('stop_requested', False): 
                print(f"  [停止] 用户请求停止 ({stop_key_display})")
                break
                
            step = steps[pc]; act = step.get('action',''); p = step.get('params',{})
            print(f"[{pc+1}] {act}")
            next_pc = pc + 1

            # [新增] 处理被屏蔽的普通步骤
            if not step.get('enabled', True):
                if act not in ['IF_IMAGE_FOUND', 'IF_TEXT_FOUND', 'ELSE', 'END_IF', 'LOOP_START', 'END_LOOP']:
                    print(f"  [屏蔽] 跳过步骤: {act}")
                    pc = next_pc
                    continue
                else:
                    # 保护机制: 控制流节点强制执行，忽略屏蔽标志
                    pass

            try:
                # [关键] 每次循环初始化结果变量
                res = None
                if act.startswith('FIND_') or act.startswith('IF_'):
                    res = _handle_find(act, p, ctx, loop_cache.get_current_loop_id() is not None)
                    if act.startswith('IF_'):
                        if not res:
                            print("  -> IF条件不满足,跳过")
                            next_pc = _find_jump(steps, pc, 'IF_', 'END_IF', ['ELSE', 'END_IF'])
                    elif not res: print("  -> 没找到目标,宏停止"); break
                    
                    # [修复] 统一处理返回值: (x, y, text) 或 (x, y, w, h)
                    # FIND_ 和 IF_ 找到目标后均移动鼠标，保持行为一致
                    # (IF 体内的 CLICK 可直接复用此位置，无需再次 OCR)
                    if res:
                        # 取前两个值作为坐标
                        target_x, target_y = res[0], res[1]
                        pyautogui.moveTo(target_x, target_y)
                
                elif act == 'AI_COMMAND':
                    # AI 自然语言指令处理
                    instruction = p.get('instruction', '')
                    if not instruction:
                        print("  [错误] AI 指令为空")
                        break
                    
                    # 获取可选的区域参数
                    region = None
                    if 'cache_box' in p:
                        cb = p['cache_box']
                        if isinstance(cb, list) and len(cb) >= 4:
                            region = tuple(cb)
                    
                    print(f"  [AI] 执行指令: {instruction}")
                    
                    # 调用 VLM 引擎
                    coords = vlm_engine.find_location_by_vlm(instruction, region=region)
                    
                    if coords:
                        target_x, target_y = coords
                        print(f"  [AI] 返回坐标: ({target_x}, {target_y})")
                        pyautogui.moveTo(target_x, target_y, duration=float(p.get('duration', 0.25)))
                        ctx['last_pos'] = (target_x, target_y)
                    else:
                        print("  [AI] 未找到目标位置")
                        if p.get('fail_stop', True):  # 默认失败时停止
                            break
                
                elif act == 'CLICK':
                    btn = p.get('button', 'left').lower()
                    clicks = int(p.get('clicks', 1))
                    interval = float(p.get('interval', 0.0))
                    duration = float(p.get('duration', 0.0))
                    x = int(p['x']) if 'x' in p else None
                    y = int(p['y']) if 'y' in p else None
                    pyautogui.click(x=x, y=y, button=btn, clicks=clicks, interval=interval, duration=duration)
                    if x is not None and y is not None:
                        ctx['last_pos'] = (x, y)
                
                elif act == 'MOVE_TO':
                    x, y = int(p['x']), int(p['y'])
                    pyautogui.moveTo(x, y, duration=float(p.get('duration', 0.25)))
                    ctx['last_pos'] = (x, y)
                
                elif act == 'MOVE_OFFSET':
                    if ctx['last_pos'][0] is None or ctx['last_pos'][1] is None: print("  [错误] 无上次坐标"); break
                    ox, oy = int(p['x_offset']), int(p['y_offset'])
                    pyautogui.move(ox, oy, duration=float(p.get('duration', 0.25)))
                    ctx['last_pos'] = (ctx['last_pos'][0]+ox, ctx['last_pos'][1]+oy)
                
                elif act == 'SCROLL':
                    clicks = int(p.get('amount', 0))
                    if 'x' in p and 'y' in p: pyautogui.moveTo(int(p['x']), int(p['y']))
                    pyautogui.scroll(clicks) 
                
                elif act == 'WAIT': 
                    total_ms = int(p['ms'])
                    for _ in range(0, total_ms, 100):
                        if ctx.get('stop_requested'): break
                        time.sleep(min(100, total_ms - _) / 1000.0)
                
                elif act == 'TYPE_TEXT':
                    interval = float(p.get('interval', 0.0))
                    text = p['text']
                    
                    if '{CLIPBOARD}' in text:
                        clipboard_content = ctx.get('clipboard_var', '')
                        if not clipboard_content:
                            try:
                                clipboard_content = pyperclip.paste()
                            except:
                                clipboard_content = ''
                        
                        text = text.replace('{CLIPBOARD}', clipboard_content)
                        print(f"  [输入] 替换占位符: {text}")
                    
                    if interval > 0: pyautogui.write(text, interval=interval)
                    else: 
                        # 增加剪贴板重试机制，防止被系统占用报错
                        # 尝试 3 次，每次间隔 0.2 秒
                        for _retry in range(3):
                            try:
                                pyperclip.copy(text)
                                break # 成功则跳出重试循环
                            except Exception:
                                time.sleep(0.2)
                        
                        # 无论成功与否，尝试粘贴 (pyautogui 不会报错)
                        time.sleep(0.1)
                        pyautogui.hotkey('ctrl', 'v')
                
                elif act == 'PRESS_KEY':
                    keys = p.get('key', '').lower().replace(' ', '').split('+')
                    if keys: pyautogui.hotkey(*keys)
                
                elif act == 'ACTIVATE_WINDOW':
                    if not PYGETWINDOW_AVAILABLE:
                        print("  [错误] pygetwindow 库未安装,无法激活窗口。")
                        break
                    title = p.get('title')
                    if not title:
                        print("  [错误] 未提供窗口标题。")
                        break
                    
                    try:
                        wins = gw.getWindowsWithTitle(title)
                        if not wins:
                            print(f"  [失败] 未找到标题包含 '{title}' 的窗口。")
                            break
                        
                        target_win = wins[0]
                        if target_win.isMinimized:
                            target_win.restore()
                        target_win.activate()
                        print(f"  [成功] 已激活窗口: {target_win.title}")
                        time.sleep(0.5) 
                    except Exception as e:
                        print(f"  [错误] 激活窗口时出错: {e}")
                        break
                
                elif act == 'NOTE':
                    # 备注动作 - 仅打印注释，不执行任何操作
                    note_text = p.get('text', '')
                    if note_text:
                        print(f"  [备注] {note_text}")
                    # 注意：必须更新 pc，否则会无限循环
                    pc = next_pc
                    continue

                elif act == 'RUN':
                    # 执行命令/脚本/文件
                    run_result = _handle_run(p, ctx)
                    # 如果返回 False，表示执行失败
                    if run_result is False:
                        print("  [RUN] 执行失败")
                        # 可配置：是否失败时停止
                        if p.get('fail_stop', True):
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

            except MacroStopException:
                raise  # 向上传播，不要吞掉
            except Exception as e:
                print(f"  [执行异常] {e}"); import traceback; traceback.print_exc(); break
            pc = next_pc
        
        return pc >= len(steps)
    finally:
        loop_cache.reset()
        print(f"--- 执行结束 ---\n[统计] {perf.get_stats()}\n")

def _handle_find(act, p, ctx, in_loop):
    is_img = 'IMAGE' in act
    final_engine = FORCE_OCR_ENGINE if (FORCE_OCR_ENGINE and FORCE_OCR_ENGINE != 'auto') else p.get('engine', 'auto')
    
    region = None
    if 'cache_box' in p:
        cb = p['cache_box']
        if isinstance(cb, list) and len(cb) == 2: cb = [cb[0], cb[1], cb[0], cb[1]]; p['cache_box'] = cb
        if isinstance(cb, list) and len(cb) >= 4:
            w_raw, h_raw = cb[2] - cb[0], cb[3] - cb[1]
            if w_raw > 0 and h_raw > 0:
                pad = CACHE_BOX_PADDING  # 使用常量替代魔法数字 
                region = (max(0, cb[0]-pad), max(0, cb[1]-pad), w_raw+pad*2, h_raw+pad*2)
            else:
                if 'cache_box' in p: del p['cache_box']
        else:
            if 'cache_box' in p: del p['cache_box']

    ss, offset = smart_screenshot(region)
    sig = f"{act}_{p.get('path', p.get('text',''))}"

    # 增强模式：IF 动作和多缩放尝试（性能开销大，但更准确）
    enhanced_mode = ctx.get('enhanced_mode', False)

    if in_loop:
        cached = loop_cache.get(sig)
        if cached and is_img and quick_check_cv2(p['path'], float(p.get('confidence',0.8)), ss, offset, cached, enhanced_mode):
            perf.record_hit(True, False); print(f"  [Loop缓存] {cached}"); ctx['last_pos'] = cached; return cached

    res = _do_find(is_img, p, ss, offset, final_engine, ctx)

    # IF 动作不使用全局 fallback（会大幅降低性能，因为它已经重复搜索了）
    # FIND_TEXT/FIND_IMAGE 才使用 fallback
    if not res and region and ENABLE_GLOBAL_FALLBACK and enhanced_mode and not act.startswith('IF_'):
        print("  [缓存失效] 全局搜索...")
        ss, offset = smart_screenshot(None)
        res = _do_find(is_img, p, ss, offset, final_engine, ctx)
        if res:
            if len(res) >= 2:
                p['cache_box'] = [res[0]-20, res[1]-10, res[0]+20, res[1]+10]

    if res:
        pos = (res[0], res[1])
        if in_loop: loop_cache.set(sig, pos)
        ctx['last_pos'] = pos
        return res

    perf.record_miss(not is_img)
    return None

def _do_find(is_img, p, ss, offset, engine='auto', ctx=None):
    """执行查找（图像或文本）并返回统一格式坐标 (x, y)"""
    enhanced_mode = ctx.get('enhanced_mode', False) if ctx else False
    if is_img:
        # 图片查找返回: (cx, cy, w, h)
        res_val = find_image_cv2(p['path'], float(p.get('confidence', 0.8)), ss, offset, enhanced_mode)
        if res_val:
            perf.record_hit(False, False)
            print(f"  [找到] 图 ({res_val[0][0]},{res_val[0][1]})")
            return (res_val[0][0], res_val[0][1]) 
    else:
        # OCR 查找返回: ((cx, cy), full_text)
        res = ocr_engine.find_text_location(
            p['text'], 
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

            # 处理剪贴板逻辑 (副作用)
            if ctx and p.get('save_to_clipboard', False):
                print(f"  [剪贴板] 原始文本: '{text_content}'")
                
                extract_pattern = p.get('extract_pattern', '').strip()
                final_text = text_content
                
                if extract_pattern:
                    try:
                        match = re.search(extract_pattern, text_content)
                        if match:
                            final_text = match.group(0)
                            print(f"  [正则提取] '{final_text}'")
                        else:
                            print(f"  [正则] 未匹配，保留原文")
                    except Exception as e:
                        print(f"  [正则错误] {e}")
                
                ctx['clipboard_var'] = final_text
                try:
                    pyperclip.copy(final_text)
                    print(f"  [剪贴板] OK 已复制")
                except Exception as e:
                    print(f"  [剪贴板] 失败: {e}")
            
            # === [修复] 统一只返回坐标 (x, y) ===
            return (pos[0], pos[1])
    
    return None


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
            # [补丁修复] 先检查后递减，确保计数正确
            if top['remain'] > 0:
                top['remain'] -= 1
                top['iteration'] += 1
                if cb: cb(f"循环第 {top['iteration']} 次 (剩余: {top['remain']}次)")
                return pc + 1
            else:
                loop_id_to_exit = loops.pop()['id']
                loop_cache.exit()
                loop_cache.clear_cache(loop_id_to_exit)
                return _find_jump(steps, pc, 'LOOP_START', 'END_LOOP', ['END_LOOP'])
        
        # === 关键修复: 条件循环不在此增加计数,交给 END_LOOP ===
        # 条件循环的迭代计数和退出判断统一在 END_LOOP 处理
        return pc + 1
    
    # 新循环初始化
    else:
        mode = p.get('mode', 'fixed')
        max_iter = int(p.get('max_iterations', 1000))
        
        if mode == 'fixed':
            count = int(p.get('times', 1))
            if count <= 0:
                return _find_jump(steps, pc, 'LOOP_START', 'END_LOOP', ['END_LOOP'])
            remain = count
        else:
            remain = max_iter
        
        loop_id = f"L{pc}_{len(loops)}"
        loop_data = {
            'start': pc,
            'remain': remain,
            'id': loop_id,
            'mode': mode,
            'iteration': 0,
            'max_iterations': max_iter
        }
        
        # 保存条件参数
        if mode == 'until_image':
            loop_data['condition_image'] = p.get('condition_image', '')
            loop_data['confidence'] = float(p.get('confidence', 0.8))
            # [优化] 保存搜索区域，加速条件检测
            if 'cache_box' in p:
                loop_data['cache_box'] = p['cache_box']
                print(f"  [Loop Until Image] 目标: {loop_data['condition_image']} (区域: {p['cache_box']})")
            else:
                print(f"  [Loop Until Image] 目标: {loop_data['condition_image']} (全屏)")
        elif mode == 'until_text':
            loop_data['condition_text'] = p.get('condition_text', '')
            loop_data['lang'] = p.get('lang', 'eng')
            # [优化] 保存搜索区域，加速条件检测
            if 'cache_box' in p:
                loop_data['cache_box'] = p['cache_box']
                print(f"  [Loop Until Text] 目标: {loop_data['condition_text']} (区域: {p['cache_box']})")
            else:
                print(f"  [Loop Until Text] 目标: {loop_data['condition_text']} (全屏)")
        
        loops.append(loop_data)
        loop_cache.enter(loop_id)
        
        if mode == 'fixed':
            if cb: cb(f"循环剩余: {remain}")
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
        cb = ld.get('cache_box')
        if cb and isinstance(cb, list) and len(cb) >= 4:
            w_raw, h_raw = cb[2] - cb[0], cb[3] - cb[1]
            if w_raw > 0 and h_raw > 0:
                pad = CACHE_BOX_PADDING
                return (max(0, cb[0]-pad), max(0, cb[1]-pad), w_raw+pad*2, h_raw+pad*2)
        return None

    if mode == 'until_image':
        path = loop_data.get('condition_image', '')
        conf = loop_data.get('confidence', 0.8)
        
        if not path or not os.path.exists(path):
            print(f"  [Loop Until] 警告: 图像路径无效 '{path}'")
            return False
        
        try:
            region = _build_region(loop_data)
            ss, offset = smart_screenshot(region)
            enhanced_mode = ctx.get('enhanced_mode', False) if ctx else False
            res_val = find_image_cv2(path, conf, ss, offset=offset, enhanced_mode=enhanced_mode)
            found = res_val is not None
            if found:
                print(f"  [Loop Until] OK 找到目标图像: {os.path.basename(path)}")
            return found
        except Exception as e:
            print(f"  [Loop Until] 图像检测错误: {e}")
            return False
    
    elif mode == 'until_text':
        text = loop_data.get('condition_text', '')
        lang = loop_data.get('lang', 'eng')
        
        if not text:
            print(f"  [Loop Until] 警告: 文本条件为空")
            return False
        
        try:
            region = _build_region(loop_data)
            ss, offset = smart_screenshot(region)
            enhanced_mode = ctx.get('enhanced_mode', False) if ctx else False
            res = ocr_engine.find_text_location(text, lang, False, ss, offset, 'auto', enhanced_mode)
            
            if res:
                found_txt = text
                if isinstance(res, tuple) and len(res) == 2 and isinstance(res[1], str):
                    found_txt = res[1]
                found_txt = found_txt.encode('gbk', errors='replace').decode('gbk')
                print(f"  [Loop Until] OK 找到目标文本: '{found_txt}'")
                return True
            return False
        except Exception as e:
            error_msg = str(e).encode('gbk', errors='replace').decode('gbk')
            print(f"  [Loop Until] 文本检测错误: {error_msg}")
            return False
    
    return False

core_engine_version = f"1.6.2 (Core) / OpenCV: {OPENCV_AVAILABLE}"

# ======================================================================
# RUN 处理函数：执行命令/脚本/文件
# ======================================================================
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
        True: 成功
        False: 失败
    """
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
        timeout = int(p.get('timeout', 30))
        cwd = p.get('cwd', None)
        save_output = p.get('save_output', False)
        
        if not command:
            print("  [RUN] 错误: 未指定命令")
            return False
        
        full_cmd = f"{command} {args}" if args else command
        
        try:
            result = subprocess.run(
                full_cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd if cwd else None
            )
            
            output = result.stdout.strip() if result.stdout else result.stderr.strip()
            
            if result.returncode == 0:
                print(f"  [RUN] 命令执行成功")
                if output:
                    print(f"        输出: {output[:200]}")
                if save_output and output:
                    ctx['clipboard_var'] = output
                    try:
                        pyperclip.copy(output)
                        print(f"        已保存到剪贴板")
                    except Exception:
                        pass
                return True
            else:
                print(f"  [RUN] 命令执行失败 (退出码: {result.returncode})")
                if output:
                    print(f"        错误: {output[:200]}")
                return False
                
        except subprocess.TimeoutExpired:
            print(f"  [RUN] 命令执行超时 ({timeout}秒)")
            return False
        except Exception as e:
            print(f"  [RUN] 命令执行错误: {e}")
            return False
    
    # === 脚本执行模式 ===
    elif run_type == 'script':
        script_path = p.get('script_path', '')
        interpreter = p.get('interpreter', 'python')
        args = p.get('args', '')
        timeout = int(p.get('timeout', 30))
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
        
        full_cmd = f'{cmd} "{script_path}" {args}' if args else f'{cmd} "{script_path}"'
        
        try:
            result = subprocess.run(
                full_cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd if cwd else None
            )
            
            output = result.stdout.strip() if result.stdout else result.stderr.strip()
            
            if result.returncode == 0:
                print(f"  [RUN] 脚本执行成功")
                if output:
                    print(f"        输出: {output[:200]}")
                if save_output and output:
                    ctx['clipboard_var'] = output
                    try:
                        pyperclip.copy(output)
                        print(f"        已保存到剪贴板")
                    except Exception:
                        pass
                return True
            else:
                print(f"  [RUN] 脚本执行失败 (退出码: {result.returncode})")
                if output:
                    print(f"        错误: {output[:200]}")
                return False
                
        except subprocess.TimeoutExpired:
            print(f"  [RUN] 脚本执行超时 ({timeout}秒)")
            return False
        except Exception as e:
            print(f"  [RUN] 脚本执行错误: {e}")
            return False
    
    # 未知类型
    else:
        print(f"  [RUN] 错误: 未知的 run_type: {run_type}")
        return False


# ======================================================================
# 宏数据校验（从 MacroAssistant.py 迁移，完成 1.7Beta 中声明的迁移）
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
