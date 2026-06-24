# -*- coding: utf-8 -*-
# vlm_engine.py
# 描述: 大模型视觉语言引擎 - 接入支持图片理解的大模型 API
# 版本: 1.1.2
# 功能: 将屏幕截图转为 Base64，连同自然语言指令发送给 VLM API，返回坐标

import base64
import json
import os
import sys
import time
import re
import threading

# 依赖库
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    print("[VLM] FAIL 未找到 requests 库 (pip install requests)")

from PIL import Image, ImageGrab

# ======================================================================
# 全局配置
# ======================================================================
APP_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "macro_settings.json")
VLM_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vlm_settings.json")
VLM_CONFIG_KEY = "vlm"

# 默认配置
DEFAULT_CONFIG = {
    "provider": "openai",  # openai, anthropic, deepseek, zhipu
    "api_key": "",
    "base_url": "https://api.openai.com/v1",
    "model": "gpt-4o",
    "timeout": 30,
    "system_prompt": "你是一个自动化助手。请分析用户指令和屏幕截图，返回目标位置的坐标。只返回 X, Y 坐标数字，用英文逗号分隔，例如: 123,456。如果找不到目标，返回 none"
}

# 提供商配置
PROVIDER_CONFIGS = {
    "openai": {
        "name": "OpenAI (GPT-4o)",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o",
        "supports_vision": True
    },
    "anthropic": {
        "name": "Anthropic (Claude)",
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-3-5-sonnet-20241022",
        "supports_vision": True
    },
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "supports_vision": False
    },
    "zhipu": {
        "name": "智谱清言 (GLM-4V)",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4v-plus",
        "supports_vision": True
    },
    "qianwen": {
        "name": "阿里通义千问 (Qwen-VL)",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-vl-plus",
        "supports_vision": True
    },
    "openrouter": {
        "name": "OpenRouter (聚合AI)",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "google/gemma-3-4b-it:free",
        "supports_vision": True
    },
    "step": {
        "name": "阶跃星辰 (Step)",
        "base_url": "https://api.stepfun.com/v1",
        "model": "step-1v-8k",
        "supports_vision": True
    }
}

# ======================================================================
# 引擎状态
# ======================================================================
_vlm_config = None
_vlm_lock = threading.Lock()


def _read_json_file(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError) as e:
        print(f"[VLM] 加载配置失败 ({path}): {e}")
        return {}


def _merge_user_config(default, user_config):
    if not isinstance(user_config, dict):
        return default

    for k, v in user_config.items():
        if v is not None:
            default[k] = v
    user_has_base_url = bool(user_config.get('base_url'))
    user_has_model = bool(user_config.get('model'))
    if default['provider'] in PROVIDER_CONFIGS:
        pc = PROVIDER_CONFIGS[default['provider']]
        if not user_has_base_url:
            default['base_url'] = pc.get('base_url', DEFAULT_CONFIG['base_url'])
        if not user_has_model:
            default['model'] = pc.get('model', DEFAULT_CONFIG['model'])
    return default


def _load_user_config():
    app_config = _read_json_file(APP_CONFIG_FILE)
    vlm_config = app_config.get(VLM_CONFIG_KEY)
    if isinstance(vlm_config, dict):
        return vlm_config
    return _read_json_file(VLM_CONFIG_FILE)


# ======================================================================
# 配置管理
# ======================================================================
def load_config():
    """加载 VLM 配置"""
    global _vlm_config
    if _vlm_config is not None:
        return _vlm_config
    
    with _vlm_lock:
        if _vlm_config is not None:
            return _vlm_config
        
        default = DEFAULT_CONFIG.copy()
        # 尝试从提供商配置获取默认值
        provider = default.get('provider', 'openai')
        if provider in PROVIDER_CONFIGS:
            pc = PROVIDER_CONFIGS[provider]
            default['base_url'] = pc.get('base_url', DEFAULT_CONFIG['base_url'])
            default['model'] = pc.get('model', DEFAULT_CONFIG['model'])

        _vlm_config = _merge_user_config(default, _load_user_config())
        return _vlm_config


def save_config(config):
    """保存 VLM 配置（原子写入）"""
    global _vlm_config
    with _vlm_lock:
        try:
            app_config = _read_json_file(APP_CONFIG_FILE)
            app_config[VLM_CONFIG_KEY] = config
            tmp_path = APP_CONFIG_FILE + '.tmp'
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(app_config, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, APP_CONFIG_FILE)
            _vlm_config = config
            return True
        except (OSError, TypeError) as e:
            print(f"[VLM] 保存配置失败: {e}")
            return False


def get_providers():
    """获取支持的提供商列表"""
    return PROVIDER_CONFIGS


# ======================================================================
# 截图与编码
# ======================================================================
def capture_screen(region=None):
    """
    截取屏幕并转为 Base64
    
    Args:
        region: 可选的区域坐标 (x1, y1, x2, y2)，None 表示全屏
        
    Returns:
        base64_str: Base64 编码的图片字符串
        offset: (x_offset, y_offset) 区域左上角坐标，全屏为 (0, 0)
    """
    screenshot = None
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
                x1, y1, x2, y2 = region[0], region[1], region[2], region[3]
                screen_w = user32.GetSystemMetrics(0)
                screen_h = user32.GetSystemMetrics(1)
                
                if x1 < 0 or y1 < 0 or x2 > screen_w or y2 > screen_h or vx < 0 or vy < 0:
                    full_screen = ImageGrab.grab(all_screens=True)
                    try:
                        crop_box = (x1 - vx, y1 - vy, x2 - vx, y2 - vy)
                        cx1 = max(0, crop_box[0])
                        cy1 = max(0, crop_box[1])
                        cx2 = min(full_screen.width, crop_box[2])
                        cy2 = min(full_screen.height, crop_box[3])
                        
                        crop_box = (cx1, cy1, cx2, cy2)
                        screenshot = full_screen.crop(crop_box)
                        full_screen.close()
                        offset = (cx1 + vx, cy1 + vy)
                    except Exception as e:
                        try: full_screen.close()
                        except Exception: pass
                        raise e
                else:
                    screenshot = ImageGrab.grab(bbox=tuple(region))
                    offset = (region[0], region[1])
            else:
                primary_w = user32.GetSystemMetrics(0)
                primary_h = user32.GetSystemMetrics(1)
                if vw > 0 and vh > 0 and (vx != 0 or vy != 0 or vw > primary_w or vh > primary_h):
                    screenshot = ImageGrab.grab(all_screens=True)
                    offset = (vx, vy)
                else:
                    screenshot = ImageGrab.grab()
                    offset = (0, 0)
        else:
            if region:
                screenshot = ImageGrab.grab(bbox=tuple(region))
                offset = (region[0], region[1])
            else:
                screenshot = ImageGrab.grab()
                offset = (0, 0)
        
        # 转为 JPEG 格式的 Base64
        import io
        buffer = io.BytesIO()
        screenshot.save(buffer, format='JPEG', quality=85)
        b64_str = base64.b64encode(buffer.getvalue()).decode('utf-8')
        return b64_str, offset
    except Exception as e:
        print(f"[VLM] 截图失败: {e}")
        raise RuntimeError(f"VLM screen capture failed: {e}") from e
    finally:
        if screenshot:
            try: screenshot.close()
            except Exception: pass


# ======================================================================
# 坐标解析
# ======================================================================
def parse_coordinates(response_text):
    """
    解析 API 返回的坐标文本
    
    支持格式:
    - "123,456"
    - "X: 123, Y: 456"  
    - "x=123, y=456"
    - "坐标: 123, 456"
    - "位于 (123, 456)"
    
    Returns:
        (x, y) 或 None
    """
    if not response_text:
        return None
    
    # 清理文本
    text = response_text.strip().lower()
    
    # 检查无结果标记
    if 'none' in text or '找不到' in text or '未找到' in text or '无法' in text:
        return None
    
    # 尝试多种匹配模式
    patterns = [
        r'(-?\d+)\s*[,，]\s*(-?\d+)\s*[,，]\s*(-?\d+)\s*[,，]\s*(-?\d+)',  # 4个坐标: 899,1326,924,1344
        r'(-?\d+)\s*[,，]\s*(-?\d+)',           # 2个坐标: 123,456 或 123，456
        r'x\s*[:=]\s*(-?\d+)\s*[,，]?\s*y\s*[:=]\s*(-?\d+)',  # x:123, y:456
        r'(-?\d+)\s*[,，]\s*(-?\d+).*(?:坐标|location)',  # 123,456 坐标
        r'位于\s*[（(]?\s*(-?\d+)\s*[,，]\s*(-?\d+)\s*[）)]?',  # 位于 (123,456)
        r'(-?\d+)px?\s*[,，]\s*(-?\d+)px?',     # 带单位: 123px, 456px
        r'^\s*(?:coordinate|position|location|point)?\s*[:：]?\s*(-?\d+)\s+(-?\d+)\s*$',  # 整段只有一个空格分隔坐标
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                groups = match.groups()
                if len(groups) == 4:
                    x1, y1, x2, y2 = int(groups[0]), int(groups[1]), int(groups[2]), int(groups[3])
                    if -10000 <= x1 <= 10000 and -10000 <= y1 <= 10000 and -10000 <= x2 <= 10000 and -10000 <= y2 <= 10000:
                        cx = (x1 + x2) // 2
                        cy = (y1 + y2) // 2
                        return (cx, cy)
                else:
                    # 2个坐标
                    x = int(groups[0])
                    y = int(groups[1])
                    # 合理性检查 (屏幕坐标通常在 -10000 到 10000 范围内，放宽以支持多屏和负坐标)
                    if -10000 <= x <= 10000 and -10000 <= y <= 10000:
                        return (x, y)
            except (ValueError, IndexError):
                continue
    
    return None


# ======================================================================
# API 调用
# ======================================================================
# API call adapters
# ======================================================================
class _OpenAICompatibleAdapter:
    endpoint = "/chat/completions"
    include_system = True
    max_tokens = 100
    image_order = "text_first"
    extra_headers = None

    @classmethod
    def build_request(cls, instruction, image_b64, cfg):
        headers = {
            "Authorization": f"Bearer {cfg.get('api_key', '')}",
            "Content-Type": "application/json",
        }
        if cls.extra_headers:
            headers.update(cls.extra_headers)

        messages = []
        system_prompt = cfg.get('system_prompt', DEFAULT_CONFIG['system_prompt'])
        if cls.include_system:
            messages.append({"role": "system", "content": system_prompt})

        if image_b64:
            text_part = {"type": "text", "text": instruction}
            image_part = {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
            }
            content = [text_part, image_part] if cls.image_order == "text_first" else [image_part, text_part]
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": instruction})

        payload = {"model": cfg.get('model', ''), "messages": messages}
        if cls.max_tokens is not None:
            payload["max_tokens"] = cls.max_tokens
        return f"{cfg.get('base_url', '')}{cls.endpoint}", headers, payload

    @staticmethod
    def parse_response(result):
        choices = result.get('choices', [])
        if choices:
            msg = choices[0].get('message', {})
            return msg.get('content', '')
        return ""


class _AnthropicAdapter:
    @staticmethod
    def build_request(instruction, image_b64, cfg):
        headers = {
            "x-api-key": cfg.get('api_key', ''),
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload = {
            "model": cfg.get('model', ''),
            "max_tokens": 100,
            "system": cfg.get('system_prompt', DEFAULT_CONFIG['system_prompt']),
        }
        if image_b64:
            payload["messages"] = [{
                "role": "user",
                "content": [
                    {"type": "text", "text": instruction},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": image_b64,
                        },
                    },
                ],
            }]
        else:
            payload["messages"] = [{"role": "user", "content": instruction}]
        return f"{cfg.get('base_url', '')}/messages", headers, payload

    @staticmethod
    def parse_response(result):
        content = result.get('content', [])
        if content and isinstance(content, list):
            return content[0].get('text', '')
        return ""


class _DeepSeekAdapter(_OpenAICompatibleAdapter):
    image_order = "image_first"


class _ZhipuAdapter(_OpenAICompatibleAdapter):
    include_system = False
    max_tokens = None
    image_order = "image_first"


class _QianwenAdapter(_OpenAICompatibleAdapter):
    image_order = "image_first"


class _StepAdapter(_OpenAICompatibleAdapter):
    include_system = False
    image_order = "image_first"


class _OpenRouterAdapter(_OpenAICompatibleAdapter):
    extra_headers = {
        "HTTP-Referer": "https://github.com/hxlive/MacroMate",
        "X-Title": "MacroMate",
    }


VLM_ADAPTERS = {
    "openai": _OpenAICompatibleAdapter,
    "anthropic": _AnthropicAdapter,
    "deepseek": _DeepSeekAdapter,
    "zhipu": _ZhipuAdapter,
    "qianwen": _QianwenAdapter,
    "step": _StepAdapter,
    "openrouter": _OpenRouterAdapter,
}


def _encode_screenshot_pil(screenshot_pil):
    import io
    buffer = io.BytesIO()
    screenshot_pil.save(buffer, format='JPEG', quality=85)
    return base64.b64encode(buffer.getvalue()).decode('utf-8')


def _resolve_vlm_image_b64(image_b64, screenshot_pil, raise_on_error):
    if image_b64 or not screenshot_pil:
        return image_b64
    try:
        return _encode_screenshot_pil(screenshot_pil)
    except Exception as e:
        err_msg = f"[VLM] FAIL image encoding failed: {e}"
        print(err_msg)
        if raise_on_error: raise RuntimeError(err_msg) from e
        return None


def _validate_vlm_capability(provider, model, image_b64, raise_on_error):
    if not image_b64:
        print("[VLM] text-only mode")
        return True
    if PROVIDER_CONFIGS.get(provider, {}).get('supports_vision') is False:
        msg = f"current model does not support image input: {provider}/{model}"
        print(f"[VLM] FAIL {msg}")
        if raise_on_error:
            raise ValueError(msg)
        return False
    return True


def _parse_vlm_response_json(response, raise_on_error):
    try:
        return response.json()
    except ValueError as e:
        err_msg = f"[VLM] FAIL API returned invalid JSON: {e}"
        print(err_msg)
        if raise_on_error: raise RuntimeError(err_msg) from e
        return None


def call_vlm_api(instruction, image_b64=None, screenshot_pil=None, config=None, raise_on_error=False):
    """Call the configured VLM API and return an (x, y) coordinate or None."""
    if not REQUESTS_AVAILABLE:
        err_msg = "[VLM] FAIL requests package is unavailable"
        print(err_msg)
        if raise_on_error: raise RuntimeError(err_msg)
        return None

    cfg = config if config else load_config()
    if not cfg.get('api_key'):
        err_msg = "[VLM] FAIL API key is not configured"
        print(err_msg)
        if raise_on_error: raise ValueError(err_msg)
        return None

    provider = cfg.get('provider', 'openai')
    model = cfg.get('model', '')
    timeout = cfg.get('timeout', 30)
    adapter = VLM_ADAPTERS.get(provider)
    if not adapter:
        print(f"[VLM] FAIL unsupported provider: {provider}")
        return None

    image_b64 = _resolve_vlm_image_b64(image_b64, screenshot_pil, raise_on_error)
    if screenshot_pil and not image_b64:
        return None
    if not _validate_vlm_capability(provider, model, image_b64, raise_on_error):
        return None

    url, headers, payload = adapter.build_request(instruction, image_b64, cfg)

    try:
        t0 = time.time()
        response = requests.post(url, headers=headers, json=payload, timeout=timeout)
        elapsed = time.time() - t0

        if response.status_code != 200:
            reason = getattr(response, 'reason', '') or 'request failed'
            err_msg = f"[VLM] API returned error: {response.status_code} - {reason}"
            print(err_msg)
            if raise_on_error: raise RuntimeError(err_msg)
            return None

        result = _parse_vlm_response_json(response, raise_on_error)
        if result is None:
            return None

        text_content = adapter.parse_response(result)
        if not text_content:
            print("[VLM] API returned empty content")
            return None

        print(f"[VLM] API response ({elapsed:.2f}s): {text_content[:200]}...")
        return parse_coordinates(text_content)

    except requests.Timeout as e:
        err_msg = f"[VLM] FAIL request timed out ({timeout}s)"
        print(err_msg)
        if raise_on_error: raise RuntimeError(err_msg) from e
        return None
    except requests.RequestException as e:
        err_msg = f"[VLM] FAIL request failed: {e}"
        print(err_msg)
        if raise_on_error: raise RuntimeError(err_msg) from e
        return None
    except (KeyError, TypeError, IndexError) as e:
        err_msg = f"[VLM] FAIL malformed API response: {e}"
        print(err_msg)
        if raise_on_error: raise RuntimeError(err_msg) from e
        return None


def find_location_by_vlm(instruction, region=None, config=None):
    """
    查找目标位置 (主入口函数)
    
    Args:
        instruction: 自然语言指令
        region: 可选的搜索区域 (x1, y1, x2, y2)
        config: 可选的配置
        
    Returns:
        (x, y) 坐标或 None (如果指定了 region，会自动转换为绝对坐标)
    """
    # 截图 (带偏移量)
    b64, offset = capture_screen(region)
    if not b64:
        return None
    
    # 调用 API
    coords = call_vlm_api(instruction, image_b64=b64, config=config)
    
    # 截图坐标以截图左上角为原点；区域截图和虚拟屏全屏截图都可能带 offset。
    if coords and offset != (0, 0):
        abs_x = coords[0] + offset[0]
        abs_y = coords[1] + offset[1]
        return (abs_x, abs_y)
    
    return coords


# ======================================================================
# 测试函数
# ======================================================================
def test_vlm():
    """测试 VLM 引擎"""
    config = load_config()
    if not config.get('api_key'):
        print("[VLM] 请先配置 API Key")
        return
    
    # 测试截图
    print("[VLM] 测试截图...")
    b64, offset = capture_screen()
    if b64:
        print(f"[VLM] 截图成功, Base64 长度: {len(b64)}")
    
    # 测试 API
    print("[VLM] 测试 API...")
    coords = find_location_by_vlm("找到屏幕中任何文字按钮的中心位置")
    if coords:
        print(f"[VLM] 找到坐标: {coords}")
    else:
        print("[VLM] 未找到坐标")


vlm_engine_version = "1.1.2"
