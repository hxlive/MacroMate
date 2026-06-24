# -*- coding: utf-8 -*-
# ocr_engine.py
# 描述:自动化宏的 OCR 功能引擎
# 版本:1.7.1
# 变更:(终极修复) 返回完整识别文本,支持剪贴板功能

from PIL import Image, ImageGrab
import re
import os
import subprocess
import io
import time
import sys
import threading

# ======================================================================
# 依赖库预加载
# ======================================================================
RAPIDOCR_CLASS = None
NUMPY_CV2_AVAILABLE = False

try:
    import numpy as np
    import cv2
    NUMPY_CV2_AVAILABLE = True
    from rapidocr import RapidOCR
    RAPIDOCR_CLASS = RapidOCR 
except Exception as e:
    import traceback
    print(f"[OCR] RapidOCR 依赖加载失败: {e}")
    traceback.print_exc()
    pass 

# ======================================================================
# 全局状态缓存
# ======================================================================
_RAPID_OCR_INSTANCE = None
_RAPID_OCR_INIT_FAILED = False
_RAPID_OCR_LOCK = threading.Lock() 

_TESSERACT_CMD = None
_TESSERACT_TESSDATA = None
_TESSERACT_CHECKED = False
_TESSERACT_LOCK = threading.Lock()

# ======================================================================
# 懒加载与预热实现
# ======================================================================
def preload_engines():
    print("[OCR] 后台预热开始...")
    if NUMPY_CV2_AVAILABLE:
        get_rapid_ocr_engine()
    get_tesseract_cmd()

def get_rapid_ocr_engine():
    global _RAPID_OCR_INSTANCE, _RAPID_OCR_INIT_FAILED
    if _RAPID_OCR_INSTANCE: return _RAPID_OCR_INSTANCE
    if not RAPIDOCR_CLASS: return None
    
    with _RAPID_OCR_LOCK: 
        if _RAPID_OCR_INSTANCE: return _RAPID_OCR_INSTANCE
        if _RAPID_OCR_INIT_FAILED: return None
        try:
            print("[OCR] 正在加载 RapidOCR 模型...")
            t0 = time.time()
            # 恢复标准初始化参数，避免因版本不兼容报错
            _RAPID_OCR_INSTANCE = RAPIDOCR_CLASS()
            print(f"[OCR] RapidOCR 就绪 ({time.time()-t0:.2f}s)")
            return _RAPID_OCR_INSTANCE
        except Exception as e:
            import traceback
            print(f"[严重错误] RapidOCR 初始化失败: {e}")
            traceback.print_exc()
            _RAPID_OCR_INIT_FAILED = True
            return None

def get_tesseract_cmd():
    global _TESSERACT_CMD, _TESSERACT_TESSDATA, _TESSERACT_CHECKED
    if _TESSERACT_CHECKED: return _TESSERACT_CMD
    
    with _TESSERACT_LOCK:
        if _TESSERACT_CHECKED: return _TESSERACT_CMD
        _TESSERACT_CHECKED = True
        
        search_roots = [
            getattr(sys, '_MEIPASS', None),
            os.path.dirname(os.path.abspath(__file__)),
            os.path.dirname(sys.executable),
            os.path.join(os.path.dirname(sys.executable), '_internal')
        ]
        for root in search_roots:
            if not root: continue
            exe = os.path.join(root, 'tesseract_local', 'tesseract.exe')
            if os.path.exists(exe):
                _TESSERACT_CMD = exe
                data = os.path.join(root, 'tesseract_local', 'tessdata')
                if os.path.exists(data):
                    _TESSERACT_TESSDATA = os.path.abspath(data)
                break
        
        if not _TESSERACT_CMD:
            try:
                cflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                find_cmd = 'where' if os.name == 'nt' else 'which'
                res = subprocess.run([find_cmd, 'tesseract'], capture_output=True, text=True, encoding='utf-8', errors='ignore', creationflags=cflags)
                if res.returncode == 0: _TESSERACT_CMD = res.stdout.strip().split('\n')[0]
            except Exception:
                pass

        if _TESSERACT_CMD:
            try:
                import pytesseract
                pytesseract.pytesseract.tesseract_cmd = _TESSERACT_CMD
            except Exception as e:
                print(f"[OCR] pytesseract 加载失败: {e}")
                _TESSERACT_CMD = None
            
        return _TESSERACT_CMD

# ======================================================================
# 引擎状态
# ======================================================================
LANG_MAP = {
    'winocr': {'eng': 'en-US', 'chi_sim': 'zh-Hans'},
    'rapidocr': {'eng': 'en', 'chi_sim': 'ch'},
    'tesseract': {'eng': 'eng', 'chi_sim': 'chi_sim'}
}

MAX_MERGE_WORDS = 20

class OCRPerformanceStats:
    def __init__(self): self.reset()
    def reset(self):
        self.stats = {'winocr': [0,0], 'rapidocr': [0,0], 'tesseract': [0,0]}
        self.total_time = 0; self.call_count = 0
    def record(self, engine, success, duration):
        self.call_count += 1; self.total_time += duration
        self.stats[engine][0 if success else 1] += 1
    def get_stats(self):
        if self.call_count == 0: return "无 OCR 统计"
        avg = (self.total_time / self.call_count) * 1000
        parts = []
        for eng, (succ, fail) in self.stats.items():
            if succ + fail > 0: parts.append(f"{eng}({succ/(succ+fail)*100:.0f}%)")
        return f"OCR统计 (均{avg:.0f}ms): {' | '.join(parts)}"

ocr_stats = OCRPerformanceStats()

# ======================================================================
# === 关键修复: 统一查找入口 - 返回完整文本 ===
# ======================================================================
def find_text_location(target_text, lang='eng', debug=False, screenshot_pil=None, offset=(0,0), engine='auto', enhanced_mode=False):
    """
    查找文本在屏幕上的位置
    
    返回值:
    - 成功: ((x, y), full_text) - 坐标和完整识别文本
    - 失败: None
    """
    # [优化] 增强参数验证，防止空值崩溃
    if not target_text or not isinstance(target_text, str): return None
    target_norm = re.sub(r'\s+', '', target_text).lower()
    if not target_norm: return None
    _should_close_screenshot = False
    if screenshot_pil is None:
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
                primary_w = user32.GetSystemMetrics(0)
                primary_h = user32.GetSystemMetrics(1)
                if vw > 0 and vh > 0 and (vx != 0 or vy != 0 or vw > primary_w or vh > primary_h):
                    screenshot_pil = ImageGrab.grab(all_screens=True)
                    offset = (vx, vy)
                else:
                    screenshot_pil = ImageGrab.grab()
                    offset = (0, 0)
            else:
                screenshot_pil = ImageGrab.grab()
                offset = (0, 0)
            _should_close_screenshot = True
        except OSError as e:
            print(f"  [OCR] 截图失败 (锁屏或无显示器): {e}")
            return None

    try:
        img_bgr_cache = None 

        def get_img_bgr():
            nonlocal img_bgr_cache
            if img_bgr_cache is None:
                try:
                    img_bgr_cache = cv2.cvtColor(np.array(screenshot_pil), cv2.COLOR_RGB2BGR)
                except Exception as e:
                    print(f"  [OCR] 截图转换失败: {e}")
                    return None
            return img_bgr_cache

        if engine == 'auto':
            # 尝试 WinOCR
            try:
                import winocr
                if lang in LANG_MAP['winocr']:
                    t0 = time.time()
                    result = _find_text_winocr(winocr, target_norm, LANG_MAP['winocr'][lang], debug, screenshot_pil, offset)
                    ocr_stats.record('winocr', result is not None, time.time() - t0)
                    if result: return result  # 返回 ((x,y), full_text)
            except ImportError: pass
            
            # 尝试 RapidOCR
            rapid_inst = get_rapid_ocr_engine()
            img_bgr = get_img_bgr()
            if rapid_inst and img_bgr is not None and lang in LANG_MAP['rapidocr']:
                t0 = time.time()
                result = _find_text_rapidocr_internal(rapid_inst, target_norm, debug, img_bgr, offset, enhanced_mode)
                ocr_stats.record('rapidocr', result is not None, time.time() - t0)
                if result: return result

            # 尝试 Tesseract
            if get_tesseract_cmd() and lang in LANG_MAP['tesseract']:
                t0 = time.time()
                result = _find_text_tesseract(target_norm, LANG_MAP['tesseract'][lang], debug, screenshot_pil, offset, enhanced_mode)
                ocr_stats.record('tesseract', result is not None, time.time() - t0)
                if result: return result

        elif engine == 'rapidocr':
            rapid_inst = get_rapid_ocr_engine()
            img_bgr = get_img_bgr()
            if rapid_inst and img_bgr is not None and lang in LANG_MAP['rapidocr']:
                t0 = time.time()
                result = _find_text_rapidocr_internal(rapid_inst, target_norm, debug, img_bgr, offset, enhanced_mode)
                ocr_stats.record('rapidocr', result is not None, time.time() - t0)
                if result: return result

        elif engine == 'winocr':
            try:
                import winocr
                if lang in LANG_MAP['winocr']:
                    t0 = time.time()
                    result = _find_text_winocr(winocr, target_norm, LANG_MAP['winocr'][lang], debug, screenshot_pil, offset)
                    ocr_stats.record('winocr', result is not None, time.time() - t0)
                    if result: return result
            except ImportError: pass

        elif engine == 'tesseract':
            if get_tesseract_cmd() and lang in LANG_MAP['tesseract']:
                t0 = time.time()
                result = _find_text_tesseract(target_norm, LANG_MAP['tesseract'][lang], debug, screenshot_pil, offset, enhanced_mode)
                ocr_stats.record('tesseract', result is not None, time.time() - t0)
                if result: return result

        print(f"  [失败] 未能找到 '{target_text}' (模式: {engine})")
        if debug: print(f"  [统计] {ocr_stats.get_stats()}")
        return None
    finally:
        if _should_close_screenshot and screenshot_pil is not None:
            try: screenshot_pil.close()
            except Exception: pass


def _match_words(words, target_norm, offset, full_text, debug, center_from_boxes, single_debug, merged_debug):
    """Match a normalized target in OCR words and return the absolute center plus full text."""
    for word in words:
        if target_norm in word['text']:
            cx, cy = center_from_boxes([word['box']], offset)
            if debug:
                print(single_debug.format(cx=cx, cy=cy, word=word, score=word.get('score', 0.0)))
            return ((cx, cy), full_text)

    for i in range(len(words)):
        merged = words[i]['text']
        if not target_norm.startswith(merged):
            continue
        boxes = [words[i]['box']]
        for j in range(i + 1, min(i + MAX_MERGE_WORDS, len(words))):
            merged += words[j]['text']
            boxes.append(words[j]['box'])
            if target_norm == merged:
                cx, cy = center_from_boxes(boxes, offset)
                if debug:
                    print(merged_debug.format(cx=cx, cy=cy))
                return ((cx, cy), full_text)
    return None
# --- 具体实现函数 (修改为返回完整文本) ---
def _find_text_winocr(winocr_module, target_norm, lang_code, debug, screenshot_pil, offset):
    try:
        res = winocr_module.recognize_pil_sync(screenshot_pil, lang=lang_code)
        if not isinstance(res, dict): return None
        
        words = []
        all_texts = []  # 收集所有文本
        
        for line in res.get('lines', []):
            for w in line.get('words', []):
                if 'text' in w and 'bounding_rect' in w:
                    text_clean = re.sub(r'\s+','',w['text']).lower()
                    words.append({'text': text_clean, 'box': w['bounding_rect'], 'original': w['text']})
                    all_texts.append(w['text'])
        
        full_text = ' '.join(all_texts)
        
        return _match_words(
            words, target_norm, offset, full_text, debug,
            lambda boxes, off: (
                off[0] + sum(b['x'] + b['width']//2 for b in boxes)//len(boxes),
                off[1] + sum(b['y'] + b['height']//2 for b in boxes)//len(boxes),
            ),
            "  [WinOCR OK] ({cx}, {cy})",
            "  [WinOCR OK] 合并 ({cx}, {cy})",
        )
    except Exception as e:
        print(f"  [WinOCR] 异常: {e}")
        return None

def _find_text_rapidocr_internal(inst, target_norm, debug, img_bgr, offset, enhanced_mode=False):
    try:
        # [优化] 针对小字进行缩放预处理 (增强模式为2倍)
        scale = 2 if enhanced_mode else 1
        h, w = img_bgr.shape[:2]
        if scale != 1:
            img_scaled = cv2.resize(img_bgr, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
        else:
            img_scaled = img_bgr
        
        res = inst(img_scaled)
        all_boxes, all_texts, all_scores = [], [], []
        
        if isinstance(res, tuple):
            res_list = res[0]
            if res_list:
                for item in res_list:
                    if isinstance(item, (list, tuple)) and len(item) >= 2:
                        all_boxes.append(item[0])
                        all_texts.append(item[1])
                        all_scores.append(item[2] if len(item)>2 else 0.0)
        elif isinstance(res, list):
            for item in res:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    all_boxes.append(item[0])
                    all_texts.append(item[1])
                    all_scores.append(item[2] if len(item)>2 else 0.0)
        else:
            all_boxes = getattr(res, 'boxes', [])
            all_texts = getattr(res, 'txts', [])
            all_scores = getattr(res, 'scores', [])
            if all_boxes is None: all_boxes = getattr(res, 'dt_boxes', [])
            if all_texts is None:
                rec_res = getattr(res, 'rec_res', [])
                if rec_res: all_texts, all_scores = zip(*rec_res)

        if not all_texts or len(all_texts) == 0: return None
        if len(all_scores) != len(all_texts): all_scores = [0.0] * len(all_texts)

        full_text = ' '.join(all_texts)  # 完整文本
        
        words = []
        for box, text, score in zip(all_boxes, all_texts, all_scores):
            if not isinstance(box, (list, np.ndarray)): continue
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            words.append({
                'text': re.sub(r'\s+','',text).lower(), 
                'box': [min(xs), min(ys), max(xs), max(ys)], 
                'score': score,
                'original': text
            })

        return _match_words(
            words, target_norm, offset, full_text, debug,
            lambda boxes, off: (
                off[0] + (sum((b[0]+b[2])//2 for b in boxes)//len(boxes)) // scale,
                off[1] + (sum((b[1]+b[3])//2 for b in boxes)//len(boxes)) // scale,
            ),
            "  [RapidOCR OK] ({cx}, {cy}) @ {score:.2f}",
            "  [RapidOCR OK] 合并 ({cx}, {cy})",
        )
    except Exception as e:
        if debug: print(f"  [RapidOCR] 解析错误: {e}")
        return None

def _find_text_tesseract(target_norm, lang, debug, screenshot_pil, offset, enhanced_mode=False):
    try:
        import pytesseract
        if _TESSERACT_CMD: pytesseract.pytesseract.tesseract_cmd = _TESSERACT_CMD
        
        s = 2 if enhanced_mode else 1
        if NUMPY_CV2_AVAILABLE:
            gray = cv2.cvtColor(np.array(screenshot_pil), cv2.COLOR_RGB2GRAY)
            h, w = gray.shape[:2]
            if s != 1:
                scaled = cv2.resize(gray, (w*s, h*s), interpolation=cv2.INTER_CUBIC)
            else:
                scaled = gray
            bw = cv2.adaptiveThreshold(scaled, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
            img_processed = Image.fromarray(bw)
        else:
            g = screenshot_pil.convert('L')
            if s != 1:
                img_processed = g.resize((g.size[0]*s, g.size[1]*s), resample=Image.LANCZOS)
            else:
                img_processed = g
        
        config = f'-l {lang}'
        if _TESSERACT_TESSDATA: 
            config += f' --tessdata-dir "{_TESSERACT_TESSDATA}"'
        
        for psm in [6, 11, 3]:
            data = pytesseract.image_to_data(img_processed, config=config + f' --psm {psm}', output_type=pytesseract.Output.DICT)
            words = []
            all_texts = []
            
            for i in range(len(data['text'])):
                try:
                    confidence = float(data['conf'][i])
                except (ValueError, TypeError):
                    confidence = -1

                if confidence > 30 and data['text'][i].strip():
                    words.append({
                        'text': re.sub(r'\s+','',data['text'][i]).lower(),
                        'box': [data['left'][i]//s, data['top'][i]//s, (data['left'][i]+data['width'][i])//s, (data['top'][i]+data['height'][i])//s],
                        'original': data['text'][i]
                    })
                    all_texts.append(data['text'][i])
            
            full_text = ' '.join(all_texts)
            
            if debug: print(f"  [Tesseract] PSM {psm} 识别 {len(words)} 词")

            result = _match_words(
                words, target_norm, offset, full_text, debug,
                lambda boxes, off: (
                    off[0] + sum((b[0]+b[2])//2 for b in boxes)//len(boxes),
                    off[1] + sum((b[1]+b[3])//2 for b in boxes)//len(boxes),
                ),
                f"  [Tesseract OK] (PSM {psm}) ({{cx}}, {{cy}})",
                f"  [Tesseract OK] (PSM {psm}) 合并 ({{cx}}, {{cy}})",
            )
            if result:
                return result
                        
        return None
    except Exception as e: 
        if debug: print(f"[Tesseract Error] {e}")
        return None
    finally:
        if 'img_processed' in locals() and img_processed is not None:
            try: img_processed.close()
            except Exception: pass
        if 'g' in locals() and g is not None and g is not screenshot_pil:
            try: g.close()
            except Exception: pass

def get_available_engines():
    engines = []
    
    try:
        import winocr
        if 'eng' in LANG_MAP['winocr']:
            engines.append(('winocr', 'Windows 10/11 OCR'))
    except ImportError:
        pass

    if get_rapid_ocr_engine() and 'eng' in LANG_MAP['rapidocr']:
        engines.append(('rapidocr', 'RapidOCR (推荐)'))

    if get_tesseract_cmd() and 'eng' in LANG_MAP['tesseract']:
        engines.append(('tesseract', 'Tesseract OCR'))
    
    if not engines:
        engines.append(('none', '无可用OCR引擎'))
        
    return engines

ocr_engine_version = "1.7.1"

