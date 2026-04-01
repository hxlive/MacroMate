# -*- coding: utf-8 -*-
# vlm_engine.py
# 描述: 大模型视觉语言引擎 - 接入支持图片理解的大模型 API
# 版本: 1.0.0
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
    print("[VLM] ✗ 未找到 requests 库 (pip install requests)")

from PIL import Image, ImageGrab

# ======================================================================
# 全局配置
# ======================================================================
VLM_CONFIG_FILE = "vlm_settings.json"

# 默认配置
DEFAULT_CONFIG = {
    "provider": "openai",  # openai, anthropic, deepseek, zhipu
    "api_key": "",
    "base_url": "https://api.openai.com/v1",
    "model": "gpt-4o",
    "timeout": 30,
    "system_prompt": "你是一个自动化助手。请分析用户指令和屏幕截图，返回目标位置的坐标。只返回 X, Y 坐标数字，用英文逗号分隔，例如: 123,456。如果找不到目标，返回: none"
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
        "supports_vision": True
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
    }
}

# ======================================================================
# 引擎状态
# ======================================================================
_vlm_config = None
_vlm_lock = threading.Lock()


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
        
        if os.path.exists(VLM_CONFIG_FILE):
            try:
                with open(VLM_CONFIG_FILE, 'r', encoding='utf-8') as f:
                    user_config = json.load(f)
                # 合并配置 (用户设置优先)
                for k, v in user_config.items():
                    if v:
                        default[k] = v
                # 如果提供商变了，且用户没有自定义model，才使用提供商的默认model
                if default['provider'] in PROVIDER_CONFIGS:
                    pc = PROVIDER_CONFIGS[default['provider']]
                    default['base_url'] = pc.get('base_url', DEFAULT_CONFIG['base_url'])
                    # 只有当model为空时才使用提供商的默认model
                    if not default.get('model'):
                        default['model'] = pc.get('model', DEFAULT_CONFIG['model'])
            except Exception as e:
                print(f"[VLM] 加载配置失败: {e}")
        
        _vlm_config = default
        return _vlm_config


def save_config(config):
    """保存 VLM 配置"""
    global _vlm_config
    try:
        with open(VLM_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        _vlm_config = config
        return True
    except Exception as e:
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
    try:
        if region:
            screenshot = ImageGrab.grab(bbox=tuple(region))
            offset = (region[0], region[1])  # 区域的左上角坐标
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
        return None, (0, 0)


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
        r'(\d+)\s*[,，]\s*(\d+)\s*[,，]\s*(\d+)\s*[,，]\s*(\d+)',  # 4个坐标: 899,1326,924,1344
        r'(\d+)\s*[,，]\s*(\d+)',           # 2个坐标: 123,456 或 123，456
        r'x\s*[:=]\s*(\d+)\s*[,，]?\s*y\s*[:=]\s*(\d+)',  # x:123, y:456
        r'(\d+)\s*[,，]\s*(\d+).*(?:坐标|location)',  # 123,456 坐标
        r'位于\s*[（(]?\s*(\d+)\s*[,，]\s*(\d+)\s*[）)]?',  # 位于 (123,456)
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                groups = match.groups()
                if len(groups) == 4:
                    # 4个坐标: x1,y1,x2,y2 -> 计算中心点
                    x1, y1, x2, y4 = int(groups[0]), int(groups[1]), int(groups[2]), int(groups[3])
                    if 0 <= x1 <= 10000 and 0 <= y1 <= 10000 and 0 <= x2 <= 10000 and 0 <= y4 <= 10000:
                        cx = (x1 + x2) // 2
                        cy = (y1 + y4) // 2
                        return (cx, cy)
                else:
                    # 2个坐标
                    x = int(groups[0])
                    y = int(groups[1])
                    # 合理性检查 (屏幕坐标通常在 0-10000 范围内)
                    if 0 <= x <= 10000 and 0 <= y <= 10000:
                        return (x, y)
            except (ValueError, IndexError):
                continue
    
    return None


# ======================================================================
# API 调用
# ======================================================================
def call_vlm_api(instruction, image_b64=None, screenshot_pil=None, config=None):
    """
    调用 VLM API 获取坐标
    
    Args:
        instruction: 自然语言指令，如 "点击确定按钮"
        image_b64: Base64 编码的图片 (可选)
        screenshot_pil: PIL 图片对象 (可选，会自动转为 Base64)
        config: 配置字典 (可选，默认从文件加载)
        
    Returns:
        (x, y) 坐标或 None
    """
    if not REQUESTS_AVAILABLE:
        print("[VLM] ✗ requests 库不可用，无法调用 API")
        return None
    
    cfg = config if config else load_config()
    
    if not cfg.get('api_key'):
        print("[VLM] ✗ 未设置 API Key")
        return None
    
    provider = cfg.get('provider', 'openai')
    api_key = cfg.get('api_key', '')
    base_url = cfg.get('base_url', '')
    model = cfg.get('model', '')
    timeout = cfg.get('timeout', 30)
    system_prompt = cfg.get('system_prompt', DEFAULT_CONFIG['system_prompt'])
    
    # 如果没有提供 Base64，尝试从 PIL 图片获取
    if not image_b64 and screenshot_pil:
        try:
            import io
            buffer = io.BytesIO()
            screenshot_pil.save(buffer, format='JPEG', quality=85)
            image_b64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
        except Exception as e:
            print(f"[VLM] 图片编码失败: {e}")
            return None
    
    if not image_b64:
        # 允许纯文本请求（用于测试连接）
        print("[VLM] 无图片，纯文本模式")
    
    # 构建请求
    headers = {}
    payload = {}
    
    if provider == "openai":
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        if image_b64:
            # 带图片的请求
            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": system_prompt
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": instruction
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{image_b64}"
                                }
                            }
                        ]
                    }
                ],
                "max_tokens": 100
            }
        else:
            # 纯文本请求（测试用）
            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": system_prompt
                    },
                    {
                        "role": "user",
                        "content": instruction
                    }
                ],
                "max_tokens": 100
            }
        url = f"{base_url}/chat/completions"
        
    elif provider == "anthropic":
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json"
        }
        payload = {
            "model": model,
            "max_tokens": 100,
            "system": system_prompt,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": instruction
                        },
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": image_b64
                            }
                        }
                    ]
                }
            ]
        }
        url = f"{base_url}/messages"
        
    elif provider == "deepseek":
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        if image_b64:
            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": system_prompt
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "image_url": f"data:image/jpeg;base64,{image_b64}"
                            },
                            {
                                "type": "text",
                                "text": instruction
                            }
                        ]
                    }
                ],
                "max_tokens": 100
            }
        else:
            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": system_prompt
                    },
                    {
                        "role": "user",
                        "content": instruction
                    }
                ],
                "max_tokens": 100
            }
        url = f"{base_url}/chat/completions"
        
    elif provider == "zhipu":
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        if image_b64:
            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{image_b64}"
                                }
                            },
                            {
                                "type": "text",
                                "text": instruction
                            }
                        ]
                    }
                ]
            }
        else:
            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": instruction
                    }
                ]
            }
        url = f"{base_url}/chat/completions"
        
    elif provider == "qianwen":
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        if image_b64:
            payload = {
                "model": model,
                "input": {
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "image": f"data:image/jpeg;base64,{image_b64}"
                                },
                                {
                                    "text": instruction
                                }
                            ]
                        }
                    ]
                },
                "parameters": {
                    "max_tokens": 100
                }
            }
        else:
            payload = {
                "model": model,
                "input": {
                    "messages": [
                        {
                            "role": "user",
                            "content": instruction
                        }
                    ]
                },
                "parameters": {
                    "max_tokens": 100
                }
            }
        url = f"{base_url}/chat/completions"
        
    elif provider == "step":
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_b64}"
                            }
                        },
                        {
                            "type": "text",
                            "text": instruction
                        }
                    ]
                }
            ],
            "max_tokens": 100
        }
        url = f"{base_url}/chat/completions"
        
    elif provider == "openrouter":
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/hxlive/MacroAssistant",
            "X-Title": "MacroAssistant"
        }
        
        if image_b64:
            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": system_prompt
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": instruction
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{image_b64}"
                                }
                            }
                        ]
                    }
                ],
                "max_tokens": 100
            }
        else:
            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": system_prompt
                    },
                    {
                        "role": "user",
                        "content": instruction
                    }
                ],
                "max_tokens": 100
            }
        url = f"{base_url}/chat/completions"
    
    else:
        print(f"[VLM] ✗ 不支持的提供商: {provider}")
        return None
    
    # 发送请求
    try:
        t0 = time.time()
        response = requests.post(url, headers=headers, json=payload, timeout=timeout)
        elapsed = time.time() - t0
        
        if response.status_code != 200:
            print(f"[VLM] API 返回错误: {response.status_code} - {response.text}")
            return None
        
        # 解析响应
        result = response.json()
        
        # 提取文本
        text_content = ""
        if provider in ("openai", "deepseek", "qianwen", "openrouter"):
            choices = result.get('choices', [])
            if choices:
                msg = choices[0].get('message', {})
                text_content = msg.get('content', '')
        elif provider == "anthropic":
            content = result.get('content', [])
            if content and isinstance(content, list):
                text_content = content[0].get('text', '')
        elif provider == "zhipu":
            choices = result.get('choices', [])
            if choices:
                msg = choices[0].get('message', {})
                text_content = msg.get('content', '')
        
        # 打印完整调试信息
        print(f"[VLM] 原始响应: {result}")
        if not text_content:
            print(f"[VLM] API 返回空内容")
            return None
        
        print(f"[VLM] API 响应 ({elapsed:.2f}s): {text_content[:200]}...")
        
        # 解析坐标
        coords = parse_coordinates(text_content)
        return coords
        
    except requests.Timeout:
        print(f"[VLM] ✗ 请求超时 ({timeout}s)")
        return None
    except Exception as e:
        print(f"[VLM] ✗ 请求失败: {e}")
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
    
    # 如果有区域偏移，需要转换为绝对坐标
    if coords and region:
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
    b64 = capture_screen()
    if b64:
        print(f"[VLM] 截图成功, Base64 长度: {len(b64)}")
    
    # 测试 API
    print("[VLM] 测试 API...")
    coords = find_location_by_vlm("找到屏幕中任何文字按钮的中心位置")
    if coords:
        print(f"[VLM] 找到坐标: {coords}")
    else:
        print("[VLM] 未找到坐标")


vlm_engine_version = "1.0.0"