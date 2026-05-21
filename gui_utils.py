# gui_utils.py
# 描述：GUI 组件工厂与界面逻辑处理 (重构版)
# 版本：1.7.0

import sys
import tkinter as tk
from tkinter import ttk, filedialog
from PIL import Image, ImageTk
import os

# 引入核心库中的工具用于处理快捷键显示
try:
    from core_engine import HotkeyUtils, MacroSchema
except ImportError:
    # Fallback
    class HotkeyUtils:
        @staticmethod
        def format_hotkey_display(s): return s.upper()

# =================================================================
# 1. 基础工具函数
# =================================================================
def parse_region_string(region_str):
    if not region_str: return None
    try:
        parts = region_str.replace('，', ',').split(',')
        coords = [int(x.strip()) for x in parts if x.strip()]
        return coords if len(coords) == 4 else None
    except (ValueError, TypeError, IndexError, AttributeError):
        return None


# =================================================================
# 2. 自动换行标签 (AutoWrapLabel)
# =================================================================
class AutoWrapLabel(ttk.Label):
    def __init__(self, master, **kwargs):
        if 'wraplength' not in kwargs:
            kwargs['wraplength'] = 250
        super().__init__(master, **kwargs)
        self.bind('<Configure>', self._on_configure)

    def _on_configure(self, event):
        width = event.width - 15
        if width > 0:
            self.configure(wraplength=width)




# ======================================================================
# 参数控件工厂类（从 MacroAssistant.py 迁移）
# ======================================================================
class ParamWidgetFactory:
    """
    参数控件工厂类，封装各类参数输入控件的创建逻辑

    特性：
    - 无状态设计，所有方法都是纯函数
    - 通过构造函数传入必要的依赖（字体、回调函数等）
    - 保持与原 MacroAssistant 中的方法接口一致
    """

    def __init__(self, font_ui, font_code, ocr_name_map=None):
        """
        初始化参数控件工厂

        Args:
            font_ui: UI 字体配置
            font_code: 代码字体配置
            ocr_name_map: OCR 引擎名称映射字典 {key: name}
        """
        self.font_ui = font_ui
        self.font_code = font_code
        self.ocr_name_map = ocr_name_map or {}

    def create_param_entry(self, parent, key, label_text, default_value):
        """创建参数输入框"""
        frame = ttk.Frame(parent)
        ttk.Label(frame, text=label_text, font=self.font_ui).pack(anchor="w")
        entry = ttk.Entry(frame, width=25, font=self.font_ui)
        entry.insert(0, default_value)
        entry.pack(anchor="w", fill=tk.X)
        frame.pack(fill=tk.X, pady=8)
        return entry

    def create_param_checkbox(self, parent, key, label_text, default=False):
        """创建复选框"""
        frame = ttk.Frame(parent)
        var = tk.BooleanVar(value=default)
        checkbox = ttk.Checkbutton(frame, text=label_text, variable=var,
                                   bootstyle="primary-round-toggle")
        checkbox.pack(anchor="w")
        frame.pack(fill=tk.X, pady=8)
        var._param_frame = frame
        return var

    def create_param_combobox(self, parent, key, label_text, values, default=None):
        """创建下拉框"""
        frame = ttk.Frame(parent)
        ttk.Label(frame, text=label_text, font=self.font_ui).pack(anchor="w")
        combo = ttk.Combobox(frame, values=values, state="readonly", width=23, font=self.font_ui)
        if default and default in values:
            combo.set(default)
        else:
            combo.current(0)
        combo.pack(anchor="w", fill=tk.X)
        frame.pack(fill=tk.X, pady=8)
        return combo

    def create_ocr_engine_combobox(self, parent, available_ocr_keys):
        """创建 OCR 引擎选择器"""
        combobox_values = ['自动选择 (Auto)']
        for key, name in self.ocr_name_map.items():
            if key in ('auto', 'none'):
                continue
            if key in available_ocr_keys:
                combobox_values.append(name)
            else:
                combobox_values.append(f"{name} (不可用)")

        return self.create_param_combobox(parent, "engine", "OCR 引擎:",
                                          combobox_values, default="自动选择 (Auto)")

    def create_region_selector(self, parent, default_val="", on_select_callback=None):
        """创建区域选择器"""
        frame = ttk.Frame(parent)
        ttk.Label(frame, text="搜索范围 (x1,y1,x2,y2) [留空=全屏]:", font=self.font_ui).pack(anchor="w")

        input_frame = ttk.Frame(frame)
        input_frame.pack(fill=tk.X, expand=True)

        entry = ttk.Entry(input_frame, font=self.font_ui)
        entry.insert(0, str(default_val) if default_val else "")
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        def on_click():
            if on_select_callback:
                on_select_callback(entry)

        btn = ttk.Button(input_frame, text="🎯 框选", width=8,
                         command=on_click,
                         bootstyle="info-outline")
        btn.pack(side=tk.RIGHT, padx=(5, 0))

        frame.pack(fill=tk.X, pady=8)
        return entry  # 返回 Entry 控件

    def create_browse_button(self, parent, callback):
        """创建浏览按钮"""
        btn = ttk.Button(parent, text="浏览...", command=callback,
                         bootstyle="info-outline", padding=(10, 6))
        btn.pack(anchor="w", fill=tk.X, pady=2)
        return btn

    def create_test_button(self, parent, text, command):
        """创建测试按钮"""
        ttk.Separator(parent, orient='horizontal').pack(fill='x', pady=(15, 5))
        btn = ttk.Button(parent, text=text, command=command,
                         bootstyle="info", padding=(10, 6))
        btn.pack(anchor="w", fill=tk.X, pady=2)
        return btn

    def create_hint_label(self, parent, text, bootstyle="secondary"):
        """创建提示标签"""
        label_style = f"{bootstyle}.TLabel"
        label = AutoWrapLabel(parent, text=text, font=self.font_ui, style=label_style)
        label.pack(anchor="w", pady=5, fill=tk.X)
        return label

    def browse_image(self, parent):
        """浏览图片文件"""
        f = filedialog.askopenfilename(filetypes=[("PNG", "*.png"), ("All", "*.*")])
        if f:
            return os.path.abspath(f)
        return None

    def build_action_form(self, action_key, parent_frame, param_widgets, available_ocr_keys, callbacks):
        """
        构建指定动作类型的参数表单界面
        
        Args:
            action_key: 动作类型键名 (如 'FIND_IMAGE')
            parent_frame: 容纳参数控件的父容器
            param_widgets: 用于存储控件引用的字典
            available_ocr_keys: 可用的 OCR 引擎列表
            callbacks: 包含各类回调函数的字典
        """
        # 快捷访问回调
        on_select_region = callbacks.get('on_select_region')
        browse_image_cb = callbacks.get('browse_image')
        on_test_find_image = callbacks.get('on_test_find_image_click')
        on_test_find_text = callbacks.get('on_test_find_text_click')
        on_test_ai_command = callbacks.get('on_test_ai_command_click')
        update_loop_params_cb = callbacks.get('update_loop_params')
        update_run_params_cb = callbacks.get('update_run_params')
        mouse_tracker = callbacks.get('mouse_tracker')
        mouse_pos_var = callbacks.get('mouse_pos_var')

        if action_key in ('FIND_TEXT', 'IF_TEXT_FOUND'):
            if 'none' in available_ocr_keys:
                self.create_hint_label(parent_frame, 
                    "FAIL 错误: 未找到可用的OCR引擎。\n"
                    "请先安装 RapidOCR (推荐) 或 Tesseract，\n"
                    "然后重启本程序。",
                    bootstyle="danger")
                # 这里不能直接修改 action_type，因为它是 UI 层的变量。
                # 告知调用者需要切回图像模式。
                return "SWITCH_TO_FIND_IMAGE"

        if action_key == 'FIND_IMAGE':
            param_widgets['path'] = self.create_param_entry(parent_frame, "path", "图像路径:", "button.png")
            param_widgets['region'] = self.create_region_selector(parent_frame, "", on_select_region)
            param_widgets['confidence'] = self.create_param_entry(parent_frame, "confidence", "置信度(0.1-1.0):", "0.8")
            self.create_hint_label(parent_frame, "* 提示：如果识别失败，请调低置信度")
            self.create_browse_button(parent_frame, browse_image_cb)
            self.create_test_button(parent_frame, "🧪 测试查找图像", on_test_find_image)
            
        elif action_key == 'FIND_TEXT':
            param_widgets['text'] = self.create_param_entry(parent_frame, "text", "查找的文本:", "确定")
            param_widgets['region'] = self.create_region_selector(parent_frame, "", on_select_region)
            param_widgets['lang'] = self.create_param_combobox(parent_frame, "lang", "语言:", list(MacroSchema.LANG_OPTIONS.keys()))
            param_widgets['engine'] = self.create_ocr_engine_combobox(parent_frame, available_ocr_keys)
            
            param_widgets['save_to_clipboard'] = self.create_param_checkbox(parent_frame, "save_to_clipboard", "[OK] 保存识别结果到剪贴板", default=False)
            _sub_ft = ttk.Frame(parent_frame)
            _sub_ft.pack(fill=tk.X)
            _ep_frame_ft = ttk.Frame(_sub_ft)
            ttk.Label(_ep_frame_ft, text="提取模式 (正则，可选):", font=self.font_ui).pack(anchor="w")
            _ep_entry_ft = ttk.Entry(_ep_frame_ft, width=25, font=self.font_ui)
            _ep_entry_ft.insert(0, r"\d+")
            _ep_entry_ft.pack(anchor="w", fill=tk.X)
            param_widgets['extract_pattern'] = _ep_entry_ft
            _hint_ft = AutoWrapLabel(_sub_ft, text="提取模式: 用正则表达式过滤识别结果，如 \\d+ 只提取数字；留空则保存全部文本。", font=self.font_ui, style="secondary.TLabel")

            def _toggle_ft(var=param_widgets['save_to_clipboard'], ef=_ep_frame_ft, hint=_hint_ft):
                if var.get():
                    ef.pack(fill=tk.X, pady=8); hint.pack(anchor="w", pady=5, fill=tk.X)
                else:
                    ef.pack_forget(); hint.pack_forget()
            param_widgets['save_to_clipboard'].trace_add('write', lambda *_: _toggle_ft())
            self.create_test_button(parent_frame, "🧪 测试查找文本 (OCR)", on_test_find_text)
            
        elif action_key == 'MOVE_OFFSET':
            param_widgets['x_offset'] = self.create_param_entry(parent_frame, "x_offset", "X 偏移:", "10")
            param_widgets['y_offset'] = self.create_param_entry(parent_frame, "y_offset", "Y 偏移:", "0")
            
        elif action_key == 'CLICK':
            param_widgets['button'] = self.create_param_combobox(parent_frame, "button", "按键:", list(MacroSchema.CLICK_OPTIONS.keys()))
        
        elif action_key == 'SCROLL':
            param_widgets['amount'] = self.create_param_entry(parent_frame, "amount", "滚动量 (正数=上, 负数=下):", "100")
            param_widgets['x'] = self.create_param_entry(parent_frame, "x", "X 坐标 (可选):", "")
            param_widgets['y'] = self.create_param_entry(parent_frame, "y", "Y 坐标 (可选):", "")
            self.create_hint_label(parent_frame, "* 提示: 如果 X, Y 为空，将在当前鼠标位置滚动。")

        elif action_key == 'WAIT':
            param_widgets['ms'] = self.create_param_entry(parent_frame, "ms", "等待 (毫秒):", "500")
            
        elif action_key == 'TYPE_TEXT':
            param_widgets['text'] = self.create_param_entry(parent_frame, "text", "输入文本:", "你好")
            self.create_hint_label(parent_frame, "* 此功能使用剪贴板 (Ctrl+V)，以支持中文及复杂文本输入。\n* 支持占位符: {CLIPBOARD} 将替换为剪贴板内容\n* 示例: '订单号: {CLIPBOARD}' → '订单号: 12345'")
            
        elif action_key == 'PRESS_KEY':
            param_widgets['key'] = self.create_param_entry(parent_frame, "key", "按键或组合键 (Enter, Ctrl+C):", "Enter")
        
        elif action_key == 'AI_COMMAND':
            param_widgets['instruction'] = self.create_param_entry(parent_frame, "instruction", "AI 指令:", "点击列表里价格最低的那个商品")
            param_widgets['region'] = self.create_region_selector(parent_frame, "", on_select_region)
            self.create_hint_label(parent_frame, "* 提示: 输入自然语言指令，如 '点击确定按钮'\n* AI 会分析屏幕截图，理解指令并返回坐标\n* 支持: OpenAI, Anthropic, DeepSeek, 智谱, 通义千问等")
            self.create_test_button(parent_frame, "🧪 测试 AI 指令", on_test_ai_command)
        
        elif action_key == 'ACTIVATE_WINDOW':
            param_widgets['title'] = self.create_param_entry(parent_frame, "title", "窗口标题 (支持部分匹配):", "记事本")
            self.create_hint_label(parent_frame, "* 提示: 宏将查找标题中包含此文本的窗口，并将其激活到最前端。")
        
        elif action_key == 'NOTE':
            param_widgets['text'] = self.create_param_entry(parent_frame, "text", "备注内容:", "这里是需要备注的文本...")
            self.create_hint_label(parent_frame, "* 注意: 此步骤仅作为注释，不会执行任何操作。\n* 可用于标注宏的执行流程，方便理解和定位。")

        elif action_key == 'RUN':
            run_type_options = {'command (命令)': 'command', 'script (脚本)': 'script', 'file (写入文件)': 'file'}
            param_widgets['run_type'] = self.create_param_combobox(parent_frame, "run_type", "类型:", list(run_type_options.keys()), default='command (命令)')
            param_widgets['command'] = self.create_param_entry(parent_frame, "command", "命令:", "curl")
            param_widgets['args'] = self.create_param_entry(parent_frame, "args", "参数:", "")
            param_widgets['script_path'] = self.create_param_entry(parent_frame, "script_path", "脚本路径:", "process.py")
            param_widgets['interpreter'] = self.create_param_combobox(parent_frame, "interpreter", "解释器:", ["python", "node", "powershell"], default="python")
            param_widgets['file_path'] = self.create_param_entry(parent_frame, "file_path", "文件路径:", "result.txt")
            param_widgets['content'] = self.create_param_entry(parent_frame, "content", "文件内容:", "Hello World")
            param_widgets['encoding'] = self.create_param_combobox(parent_frame, "encoding", "编码:", ["utf-8", "gbk", "gb2312"], default="utf-8")
            param_widgets['timeout'] = self.create_param_entry(parent_frame, "timeout", "超时(秒):", "30")
            param_widgets['cwd'] = self.create_param_entry(parent_frame, "cwd", "工作目录:", "")
            param_widgets['append'] = self.create_param_checkbox(parent_frame, "append", "[OK] 追加模式 (文件)", default=False)
            param_widgets['save_output'] = self.create_param_checkbox(parent_frame, "save_output", "[OK] 保存输出到剪贴板", default=False)
            param_widgets['shell_mode'] = self.create_param_checkbox(parent_frame, "shell_mode", "[警告] shell 模式 (仅可信宏)", default=False)
            param_widgets['fail_stop'] = self.create_param_checkbox(parent_frame, "fail_stop", "[警告] RUN 失败时停止宏", default=False)
            self.create_hint_label(parent_frame, "* {CLIPBOARD} = 剪贴板内容, {DATETIME} = 当前时间")
            if 'run_type' in param_widgets:
                param_widgets['run_type'].bind("<<ComboboxSelected>>", update_run_params_cb)
            update_run_params_cb(None)

        elif action_key == 'MOVE_TO':
            param_widgets['x'] = self.create_param_entry(parent_frame, "x", "X 坐标:", "100")
            param_widgets['y'] = self.create_param_entry(parent_frame, "y", "Y 坐标:", "100")
            ttk.Separator(parent_frame, orient='horizontal').pack(fill='x', pady=(15, 5))
            ttk.Label(parent_frame, text="当前鼠标位置 (参考):", font=self.font_ui, foreground='gray').pack(anchor="w", pady=(5,0))
            ttk.Label(parent_frame, textvariable=mouse_pos_var, font=self.font_code, bootstyle="info").pack(anchor="w")
            mouse_tracker.start()
            
        elif action_key == 'IF_IMAGE_FOUND':
            param_widgets['path'] = self.create_param_entry(parent_frame, "path", "图像路径:", "button.png")
            param_widgets['region'] = self.create_region_selector(parent_frame, "", on_select_region)
            param_widgets['confidence'] = self.create_param_entry(parent_frame, "confidence", "置信度:", "0.8")
            self.create_browse_button(parent_frame, browse_image_cb)
            self.create_test_button(parent_frame, "🧪 测试 IF 图像", on_test_find_image)
            
        elif action_key == 'IF_TEXT_FOUND':
            param_widgets['text'] = self.create_param_entry(parent_frame, "text", "查找文本:", "确定")
            param_widgets['region'] = self.create_region_selector(parent_frame, "", on_select_region)
            param_widgets['lang'] = self.create_param_combobox(parent_frame, "lang", "语言:", list(MacroSchema.LANG_OPTIONS.keys()))
            param_widgets['engine'] = self.create_ocr_engine_combobox(parent_frame, available_ocr_keys)
            param_widgets['save_to_clipboard'] = self.create_param_checkbox(parent_frame, "save_to_clipboard", "[OK] 保存识别结果到剪贴板", default=False)
            _sub_ift = ttk.Frame(parent_frame); _sub_ift.pack(fill=tk.X)
            _ep_frame_ift = ttk.Frame(_sub_ift)
            ttk.Label(_ep_frame_ift, text="提取模式 (正则，可选):", font=self.font_ui).pack(anchor="w")
            _ep_entry_ift = ttk.Entry(_ep_frame_ift, width=25, font=self.font_ui); _ep_entry_ift.insert(0, r"\d+"); _ep_entry_ift.pack(anchor="w", fill=tk.X)
            param_widgets['extract_pattern'] = _ep_entry_ift
            _hint_ift = AutoWrapLabel(_sub_ift, text="提取模式: 用正则表达式过滤识别结果，如 \\d+ 只提取数字；留空则保存全部文本。", font=self.font_ui, style="secondary.TLabel")

            def _toggle_ift(var=param_widgets['save_to_clipboard'], ef=_ep_frame_ift, hint=_hint_ift):
                if var.get():
                    ef.pack(fill=tk.X, pady=8); hint.pack(anchor="w", pady=5, fill=tk.X)
                else:
                    ef.pack_forget(); hint.pack_forget()
            param_widgets['save_to_clipboard'].trace_add('write', lambda *_: _toggle_ift())
            self.create_test_button(parent_frame, "🧪 测试 IF 文本", on_test_find_text)
            
        elif action_key == 'LOOP_START':
            mode_options = {'固定次数': 'fixed', '直到找到图像': 'until_image', '直到找到文本': 'until_text'}
            param_widgets['mode'] = self.create_param_combobox(parent_frame, "mode", "循环模式:", list(mode_options.keys()), default='固定次数')
            param_widgets['times'] = self.create_param_entry(parent_frame, "times", "循环次数:", "10")
            param_widgets['max_iterations'] = self.create_param_entry(parent_frame, "max_iterations", "最大迭代次数 (安全阀):", "1000")
            param_widgets['condition_image'] = self.create_param_entry(parent_frame, "condition_image", "目标图像路径:", "target.png")
            param_widgets['confidence'] = self.create_param_entry(parent_frame, "confidence", "置信度:", "0.8")
            param_widgets['condition_text'] = self.create_param_entry(parent_frame, "condition_text", "目标文本:", "加载完成")
            param_widgets['lang'] = self.create_param_combobox(parent_frame, "lang", "语言:", list(MacroSchema.LANG_OPTIONS.keys()))
            param_widgets['region'] = self.create_region_selector(parent_frame, "", on_select_region)
            self.create_hint_label(parent_frame, "* 提示:\n- 固定次数: 传统循环\n- 直到找到图像: 找到即停\n- 直到找到文本: 找到即停\n- 最大迭代: 安全机制")
            if 'mode' in param_widgets:
                param_widgets['mode'].bind("<<ComboboxSelected>>", update_loop_params_cb)
            update_loop_params_cb(None)

        elif action_key == 'ELSE':
            self.create_hint_label(parent_frame, "* 提示: 'ELSE' 必须与 'IF' 配合使用。")
        elif action_key == 'END_IF':
            self.create_hint_label(parent_frame, "* 提示: 'END_IF' 标志着逻辑块结束。")
        elif action_key == 'END_LOOP':
            self.create_hint_label(parent_frame, "* 提示: 'END_LOOP' 标志着循环结束。")
            
        return None

    def collect_step_data(self, action_key, param_widgets, engine_key_map=None):
        """
        从参数控件中收集并校验数据
        
        Returns:
            params: 整理后的参数字典
            error: 错误消息，若无错误则为 None
        """
        params = {}
        try:
            for k, w in param_widgets.items():
                # 处理 BooleanVar (复选框)
                if isinstance(w, tk.BooleanVar):
                    params[k] = w.get()
                    continue
                
                val = w.get()
                
                # 数字校验
                if k in ['x', 'y', 'ms', 'times', 'x_offset', 'y_offset', 'amount', 'max_iterations', 'timeout']:
                    if val:
                        if k in ('ms', 'times', 'max_iterations'):
                            try:
                                parsed_int = int(val.strip())
                            except (ValueError, TypeError):
                                return None, f"参数 '{k}' 必须是非负整数"
                            if parsed_int < 0:
                                return None, f"参数 '{k}' 必须是非负整数"
                        elif k == 'timeout':
                            # [P2修复] timeout 要求正整数，≤0 无意义
                            try:
                                parsed_timeout = int(val.strip())
                            except (ValueError, TypeError):
                                return None, "参数 'timeout' 必须是正整数（如 30）"
                            if parsed_timeout <= 0:
                                return None, "参数 'timeout' 必须大于 0"
                        else:
                            try:
                                int(val.strip())
                            except (ValueError, TypeError):
                                return None, f"参数 '{k}' 必须是整数"
                
                # confidence 浮点校验
                if k == 'confidence' and val:
                    try:
                        cf = float(val.strip())
                        if not (0.0 < cf <= 1.0):
                            return None, "参数 'confidence' 必须在 0.0 ~ 1.0 之间"
                    except (ValueError, TypeError):
                        return None, "参数 'confidence' 必须是数字（如 0.8）"

                
                if action_key == 'SCROLL' and k in ['x', 'y'] and not val:
                    continue
                
                if not val:
                    if k in ('region', 'extract_pattern'): pass
                    elif action_key in ('ELSE', 'END_IF', 'END_LOOP', 'NOTE'): continue
                    elif action_key in ('SCROLL', 'CLICK') and k in ('x', 'y'): continue
                    elif action_key == 'RUN': continue
                    else: return None, f"参数 '{k}' 不能为空"
                
                # 参数转换
                if k == 'mode':
                    params[k] = {'固定次数': 'fixed', '直到找到图像': 'until_image', '直到找到文本': 'until_text'}.get(val, 'fixed')
                elif k == 'run_type':
                    params[k] = {'command (命令)': 'command', 'script (脚本)': 'script', 'file (写入文件)': 'file'}.get(val, 'command')
                elif k == 'lang':
                    params[k] = MacroSchema.LANG_OPTIONS.get(val, 'eng')
                elif k == 'button':
                    params[k] = MacroSchema.CLICK_OPTIONS.get(val, 'left')
                elif k == 'engine':
                    if engine_key_map:
                        params[k] = engine_key_map.get(val, 'auto')
                    else:
                        params[k] = val.split(' ')[0] 
                elif k == 'region':
                    if val.strip():
                        coords = parse_region_string(val)
                        if coords: params['cache_box'] = coords
                elif k == 'extract_pattern':
                    if val and val.strip(): params[k] = val.strip()
                else:
                    params[k] = val

            # RUN 动作参数清洗
            if action_key == 'RUN':
                run_type = params.get('run_type', 'command')
                if run_type == 'command' and not params.get('command'): return None, "RUN 命令类型必须填写 '命令'"
                elif run_type == 'script' and not params.get('script_path'): return None, "RUN 脚本类型必须填写 '脚本路径'"
                elif run_type == 'file' and not params.get('file_path'): return None, "RUN 文件类型必须填写 '文件路径'"

                common_keys = ['run_type', 'timeout', 'cwd', 'save_output', 'fail_stop']
                keep_keys = common_keys + (['command', 'args', 'shell_mode'] if run_type == 'command' else 
                                         (['script_path', 'interpreter', 'args'] if run_type == 'script' else 
                                          ['file_path', 'content', 'append', 'encoding']))
                
                new_params = {}
                for k in keep_keys:
                    if k in params:
                        v = params[k]
                        if k == 'timeout' and v == '30': continue
                        if k == 'interpreter' and v == 'python': continue
                        if k == 'append' and not v: continue
                        if k == 'save_output' and not v: continue
                        if k == 'shell_mode' and not v: continue
                        if k == 'fail_stop' and not v: continue
                        if k == 'encoding' and v == 'utf-8': continue
                        if v is not None and (isinstance(v, bool) or str(v).strip()):
                            new_params[k] = v
                params = new_params

            # LOOP_START 动作参数清洗
            if action_key == 'LOOP_START':
                mode = params.get('mode', 'fixed')

                if mode == 'fixed':
                    keep_keys = ['mode', 'times']
                elif mode == 'until_image':
                    keep_keys = ['mode', 'condition_image', 'confidence', 'max_iterations', 'cache_box']
                elif mode == 'until_text':
                    keep_keys = ['mode', 'condition_text', 'lang', 'max_iterations', 'cache_box']
                else:
                    keep_keys = ['mode', 'times']

                new_params = {}
                for k in keep_keys:
                    if k in params:
                        new_params[k] = params[k]

                # 基础必填校验（按模式）
                if mode == 'fixed' and not str(new_params.get('times', '')).strip():
                    return None, "参数 'times' 不能为空"
                if mode == 'until_image' and not str(new_params.get('condition_image', '')).strip():
                    return None, "参数 'condition_image' 不能为空"
                if mode == 'until_text' and not str(new_params.get('condition_text', '')).strip():
                    return None, "参数 'condition_text' 不能为空"

                params = new_params

            # 路径有效性校验
            for path_key in ['path', 'condition_image']:
                if path_key in params and params[path_key]:
                    p = params[path_key]
                    if not os.path.exists(p): return None, f"文件不存在: {p}"
                    if not p.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.gif')):
                        return None, f"文件格式错误 (仅支持图片): {os.path.basename(p)}"

            return params, None
        except Exception as e:
            return None, str(e)



# ======================================================================
# 参数动态显示控制函数（从 MacroAssistant.py 迁移）
# ======================================================================
def update_loop_params(param_widgets, param_frame, mode_widget):
    """
    根据循环模式动态显示/隐藏参数

    Args:
        param_widgets: 参数字典 {key: widget}
        param_frame: 参数面板容器
        mode_widget: 模式选择下拉框
    """
    if 'mode' not in param_widgets:
        return

    mode_map = {
        '固定次数': 'fixed',
        '直到找到图像': 'until_image',
        '直到找到文本': 'until_text'
    }

    selected_mode = mode_widget.get()
    mode = mode_map.get(selected_mode, 'fixed')

    # 收集提示标签
    hint_labels = []
    for widget in param_frame.winfo_children():
        if isinstance(widget, AutoWrapLabel):
            hint_labels.append(widget)

    # 隐藏所有条件参数
    for key in ['times', 'condition_image', 'confidence', 'condition_text', 'lang', 'max_iterations', 'region']:
        if key in param_widgets:
            parent_frame = _get_widget_frame(param_widgets[key])
            if parent_frame:
                parent_frame.pack_forget()

    # 根据模式显示对应参数
    params_to_show = []
    if mode == 'fixed':
        params_to_show = ['times']
    elif mode == 'until_image':
        params_to_show = ['condition_image', 'confidence', 'max_iterations', 'region']
    elif mode == 'until_text':
        params_to_show = ['condition_text', 'lang', 'max_iterations', 'region']

    # 显示参数
    for key in params_to_show:
        if key in param_widgets:
            frame = _get_widget_frame(param_widgets[key])
            if frame:
                frame.pack(fill=tk.X, pady=8)

    # 确保提示标签始终在最后
    for hint_label in hint_labels:
        hint_label.pack_forget()
        hint_label.pack(anchor="w", pady=5, fill=tk.X)


def _get_widget_frame(widget):
    """获取控件的父容器 frame，兼容 BooleanVar 和普通 tk Widget"""
    if isinstance(widget, tk.BooleanVar):
        return getattr(widget, '_param_frame', None)
    return widget.master


def update_run_params(param_widgets, param_frame, run_type_widget):
    """
    根据 RUN 类型动态显示/隐藏参数

    Args:
        param_widgets: 参数字典 {key: widget}
        param_frame: 参数面板容器
        run_type_widget: 类型选择下拉框
    """
    if 'run_type' not in param_widgets:
        return

    run_type_map = {
        'command (命令)': 'command',
        'script (脚本)': 'script',
        'file (写入文件)': 'file'
    }

    selected_type = run_type_widget.get()
    run_type = run_type_map.get(selected_type, 'command')

    # 收集提示标签
    hint_labels = []
    for widget in param_frame.winfo_children():
        if isinstance(widget, AutoWrapLabel):
            hint_labels.append(widget)

    # 各类型对应的参数。除了 run_type，本函数统一接管 RUN 的字段可见性。
    command_params = ['command', 'args', 'timeout', 'cwd', 'save_output', 'shell_mode', 'fail_stop']
    script_params = ['script_path', 'interpreter', 'args', 'timeout', 'cwd', 'save_output', 'fail_stop']
    file_params = ['file_path', 'content', 'append', 'encoding']

    # 隐藏所有 RUN 参数，避免编辑已有步骤时把命令/脚本/文件字段全部展开。
    all_type_params = sorted(set(command_params + script_params + file_params))
    for key in all_type_params:
        if key in param_widgets:
            parent_frame = _get_widget_frame(param_widgets[key])
            if parent_frame:
                parent_frame.pack_forget()

    # 显示选中类型的参数
    if run_type == 'command':
        params_to_show = command_params
    elif run_type == 'script':
        params_to_show = script_params
    elif run_type == 'file':
        params_to_show = file_params
    else:
        params_to_show = []

    for key in params_to_show:
        if key in param_widgets:
            frame = _get_widget_frame(param_widgets[key])
            if frame:
                frame.pack(fill=tk.X, pady=8)

    # 确保提示标签始终在最后
    for hint_label in hint_labels:
        hint_label.pack_forget()
        hint_label.pack(anchor="w", pady=5, fill=tk.X)


# ======================================================================
# 参数转换工具函数（从 MacroAssistant.py 迁移）
# ======================================================================
def param_display_to_internal(key, display_value, ocr_name_map, lang_options, click_options):
    """
    将UI显示值转换为内部存储值

    Args:
        key: 参数键名 ('lang', 'button', 'engine' 等)
        display_value: UI中显示的值
        ocr_name_map: OCR引擎名称映射 {display_name: key}
        lang_options: 语言选项映射 {display_name: key}
        click_options: 点击选项映射 {display_name: key}

    Returns:
        内部存储的实际值
    """
    mappings = {
        'lang': lang_options,
        'button': click_options,
        'engine': ocr_name_map
    }

    if key == 'engine' and display_value.endswith(" (不可用)"):
        display_value = display_value.replace(" (不可用)", "")

    mapping = mappings.get(key)
    if mapping:
        return mapping.get(display_value, display_value)

    return display_value


def param_internal_to_display(key, internal_value, ocr_name_map, lang_values_to_name,
                              click_values_to_name, available_ocr_keys=None):
    """
    将内部存储值转换为UI显示值

    Args:
        key: 参数键名
        internal_value: 内部存储的值
        ocr_name_map: OCR引擎名称映射 {key: display_name}
        lang_values_to_name: 语言值到显示名的映射
        click_values_to_name: 点击值到显示名的映射
        available_ocr_keys: 可用的OCR引擎key列表

    Returns:
        UI中应该显示的值
    """
    reverse_mappings = {
        'lang': lang_values_to_name,
        'button': click_values_to_name,
        'engine': ocr_name_map
    }

    mapping = reverse_mappings.get(key)
    if mapping:
        display_val = mapping.get(internal_value, internal_value)

        if key == 'engine' and available_ocr_keys:
            if internal_value not in available_ocr_keys and internal_value != 'auto':
                display_val = f"{display_val} (不可用)"

        return display_val

    return internal_value


# ======================================================================
# 工具函数（从 MacroAssistant.py 迁移）
# ======================================================================
def resource_path(relative_path):
    """获取资源文件路径，支持打包后环境"""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


def get_icon_path(icon_name="app_icon.ico", app_version="1.7Beta"):
    """
    获取图标路径,打包后从临时目录提取
    返回可用于 iconbitmap() 的实际文件路径

    Args:
        icon_name: 图标文件名
        app_version: 应用版本号（用于临时文件命名）

    Returns:
        图标文件路径或 None
    """
    # 开发环境直接返回
    if not getattr(sys, 'frozen', False):
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), icon_name)
        if os.path.exists(icon_path):
            return icon_path
        return None

    # 打包环境:提取到临时目录
    try:
        import tempfile
        import shutil

        # 从 _MEIPASS 获取图标
        source_icon = os.path.join(sys._MEIPASS, icon_name)

        if not os.path.exists(source_icon):
            print(f"[警告] 未找到打包的图标文件: {source_icon}")
            return None

        # 创建临时文件
        temp_dir = tempfile.gettempdir()
        temp_icon = os.path.join(temp_dir, f"macroassistant_{app_version}.ico")

        # 复制图标到临时目录
        shutil.copy2(source_icon, temp_icon)
        print(f"[Info] 图标已提取到: {temp_icon}")

        return temp_icon
    except Exception as e:
        print(f"[错误] 提取图标失败: {e}")
        return None
