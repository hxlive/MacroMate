# -*- coding: utf-8 -*-
# gui_utils.py
# 描述：GUI 辅助工具库 (重构版 - 样式完美还原)
# 版本：1.4.0

import sys
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import pyautogui
from PIL import Image, ImageTk
import os
import time
import base64

# 引入核心库中的工具用于处理快捷键显示
try:
    from core_engine import HotkeyUtils
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
# 2. 鼠标位置追踪器 (MouseTracker)
# =================================================================
class MouseTracker:
    def __init__(self, root, tk_var):
        self.root = root
        self.var = tk_var
        self.job = None
        self.is_running = False

    def start(self):
        if not self.is_running:
            self.is_running = True
            self._update()

    def stop(self):
        self.is_running = False
        if self.job:
            try: self.root.after_cancel(self.job)
            except: pass
            self.job = None
            self.var.set("")

    def _update(self):
        if not self.is_running: return
        try:
            x, y = pyautogui.position()
            self.var.set(f"X: {x}, Y: {y}")
        except Exception as e:
            self.var.set("未知")
        self.job = self.root.after(100, self._update)

# =================================================================
# 3. 自动换行标签 (AutoWrapLabel)
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

# =================================================================
# 4. 区域选择器 (RegionSelector)
# =================================================================
class RegionSelector:
    def __init__(self, master):
        self.master = master
        self.selection = None
        self.is_selecting = False
        self.start_x = 0; self.start_y = 0; self.cur_x = 0; self.cur_y = 0

        self.top = tk.Toplevel(self.master)
        self.top.attributes('-fullscreen', True)
        self.top.attributes('-alpha', 0.3)
        self.top.attributes('-topmost', True)
        self.top.configure(cursor="cross")
        self.top.overrideredirect(True)

        self.canvas = tk.Canvas(self.top, bg="black", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self._bind_events()

    def _bind_events(self):
        self.top.bind("<ButtonPress-1>", self._on_press)
        self.top.bind("<B1-Motion>", self._on_drag)
        self.top.bind("<ButtonRelease-1>", self._on_release)
        self.top.bind("<Escape>", self._on_cancel)
        self.top.bind("<Return>", self._on_confirm)  # [修复 BUG-5] 恢复 Enter 键确认

    def _on_confirm(self, event=None):
        """Enter 键确认当前选区"""
        if self.cur_x != 0 or self.cur_y != 0:
            x1, y1 = min(self.start_x, self.cur_x), min(self.start_y, self.cur_y)
            x2, y2 = max(self.start_x, self.cur_x), max(self.start_y, self.cur_y)
            if abs(x2 - x1) > 5 and abs(y2 - y1) > 5:
                self.selection = (x1, y1, x2, y2)
        self.top.destroy()

    def _on_press(self, event):
        self.is_selecting = True
        self.start_x, self.start_y = event.x, event.y
        self.rect = self.canvas.create_rectangle(
            self.start_x, self.start_y, self.start_x, self.start_y,
            outline="red", width=2, fill="white", stipple="gray50"
        )

    def _on_drag(self, event):
        if self.is_selecting:
            self.cur_x, self.cur_y = event.x, event.y
            self.canvas.coords(self.rect, self.start_x, self.start_y, self.cur_x, self.cur_y)

    def _on_release(self, event):
        if self.is_selecting:
            self.is_selecting = False
            self.cur_x, self.cur_y = event.x, event.y
            x1, y1 = min(self.start_x, self.cur_x), min(self.start_y, self.cur_y)
            x2, y2 = max(self.start_x, self.cur_x), max(self.start_y, self.cur_y)
            if abs(x2 - x1) > 5 and abs(y2 - y1) > 5:
                self.selection = (x1, y1, x2, y2)
            self.top.destroy()

    def _on_cancel(self, event=None):
        self.is_selecting = False
        self.top.destroy()

    def get_region(self):
        self.master.wait_window(self.top)
        return self.selection

# =================================================================
# 5. 快捷键输入框 (HotkeyEntry)
# =================================================================
class HotkeyEntry(ttk.Entry):
    def __init__(self, master, hotkey_var, **kwargs):
        # [NEW-2] 修复：不要绑定 hotkey_var 到 ttk.Entry 上，直接使用 _display_text
        super().__init__(master, **kwargs)
        self.hotkey_var = hotkey_var
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        self.bind("<Key>", self._on_key)
        self._placeholder = "点击此处，按下快捷键..."
        self._is_recording = False
        self._pressed_keys = set()
        self._display_text = tk.StringVar()
        
        # 初始状态配置
        current = self.hotkey_var.get()
        if current:
            self._display_text.set(HotkeyUtils.format_hotkey_display(current))
            self.config(textvariable=self._display_text, bootstyle="default")
        else:
            self._display_text.set(self._placeholder)
            # [NEW-1] 修复：使用 bootstyle="secondary" (灰字) 而不是原生的 foreground 属性
            self.config(textvariable=self._display_text, bootstyle="secondary")

    def _on_focus_in(self, event):
        self._is_recording = True
        self._pressed_keys.clear()
        self._display_text.set("按下快捷键组合...")
        # 录制时高亮为 info 色（蓝）
        self.config(bootstyle="info")

    def _on_focus_out(self, event):
        self._is_recording = False
        self._pressed_keys.clear()
        current = self.hotkey_var.get()
        if current:
            display = HotkeyUtils.format_hotkey_display(current)
            self._display_text.set(display)
            self.config(bootstyle="default")
        else:
            self._display_text.set(self._placeholder)
            self.config(bootstyle="secondary")

    def _on_key(self, event):
        if not self._is_recording:
            return

        key = event.keysym.lower()
        if key in ('shift_l', 'shift_r'): key = 'shift'
        elif key in ('control_l', 'control_r'): key = 'ctrl'
        elif key in ('alt_l', 'alt_r', 'alt_gr'): key = 'alt'
        elif key in ('command', 'command_l', 'command_r', 'win', 'win_l', 'win_r'): key = 'cmd'

        if key not in ('shift', 'ctrl', 'alt', 'cmd'):
            if len(key) == 1 or key in ('f1', 'f2', 'f3', 'f4', 'f5', 'f6', 'f7', 'f8', 'f9', 'f10', 'f11', 'f12', 'space', 'return', 'tab', 'backspace', 'delete', 'insert', 'home', 'end', 'pageup', 'pagedown', 'up', 'down', 'left', 'right'):
                self._pressed_keys.add(key)

        modifiers = []
        if event.state & 0x0001: modifiers.append('shift')
        if event.state & 0x0004: modifiers.append('ctrl')
        if event.state & 0x20000: modifiers.append('alt')
        if event.state & 0x0008: modifiers.append('cmd')

        for mod in modifiers:
            self._pressed_keys.add(mod)

        sorted_keys = sorted(list(self._pressed_keys), key=lambda k: (k not in ['ctrl', 'alt', 'shift', 'cmd'], k))

        if sorted_keys:
            hotkey_str = '+'.join(sorted_keys)
            self.hotkey_var.set(hotkey_str)
            display_str = HotkeyUtils.format_hotkey_display(hotkey_str)
            self._display_text.set(display_str)

        return 'break'

# =================================================================
# 6. 快捷键设置对话框 (HotkeySettingsDialog)
# =================================================================
class HotkeySettingsDialog:
    # [修复 BUG-4] 恢复快捷键格式校验；默认值修正为 ctrl+f10/ctrl+f11
    def __init__(self, parent, run_hotkey, stop_hotkey,
                 default_run='ctrl+f10', default_stop='ctrl+f11'):
        self.parent = parent
        self.default_run = default_run
        self.default_stop = default_stop
        self.result = None

        self.dialog = tk.Toplevel(parent)
        self.dialog.title("快捷键设置")
        self.dialog.geometry("450x480")
        self.dialog.resizable(False, False)
        self.dialog.transient(parent)
        self.dialog.grab_set()

        self.dialog.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - self.dialog.winfo_width()) // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.dialog.winfo_height()) // 2
        self.dialog.geometry(f"+{x}+{y}")

        self._create_ui(run_hotkey, stop_hotkey)
        self.dialog.protocol("WM_DELETE_WINDOW", self._on_close)

    def _create_ui(self, run_hotkey, stop_hotkey):
        main_frame = ttk.Frame(self.dialog, padding=20)
        main_frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(main_frame, text="⌨️ 自定义快捷键",
                  font=("Microsoft YaHei UI", 12, "bold")).pack(pady=(0, 15))

        self.run_var = tk.StringVar(value=run_hotkey)
        run_frame = ttk.Labelframe(main_frame, text="运行/继续 快捷键", padding=15)
        run_frame.pack(fill=tk.X, pady=(0, 15))
        run_inner = ttk.Frame(run_frame)
        run_inner.pack(fill=tk.X)
        run_inner.columnconfigure(0, weight=1)
        self.run_entry = HotkeyEntry(run_inner, self.run_var, width=25)
        self.run_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10), ipady=5)
        ttk.Button(run_inner, text="🎯 录制", command=self.run_entry.focus_set,
                   bootstyle="info", width=12).grid(row=0, column=1, ipady=3)

        self.stop_var = tk.StringVar(value=stop_hotkey)
        stop_frame = ttk.Labelframe(main_frame, text="停止宏快捷键", padding=15)
        stop_frame.pack(fill=tk.X, pady=(0, 15))
        stop_inner = ttk.Frame(stop_frame)
        stop_inner.pack(fill=tk.X)
        stop_inner.columnconfigure(0, weight=1)
        self.stop_entry = HotkeyEntry(stop_inner, self.stop_var, width=25)
        self.stop_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10), ipady=5)
        ttk.Button(stop_inner, text="🎯 录制", command=self.stop_entry.focus_set,
                   bootstyle="info", width=12).grid(row=0, column=1, ipady=3)

        ttk.Label(main_frame, text="💡 支持: Ctrl, Alt, Shift, F1-F12, A-Z, 0-9等",
                  font=("Microsoft YaHei UI", 9), foreground="#666").pack(pady=(20, 20))

        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(10, 0))
        btn_frame.columnconfigure(0, weight=1)
        btn_frame.columnconfigure(1, weight=1)
        btn_frame.columnconfigure(2, weight=1)
        ttk.Button(btn_frame, text="✕ 取消", command=self._on_close,
                   bootstyle="secondary", padding=(10, 10)).grid(row=0, column=0, sticky="ew", padx=(5, 0))
        ttk.Button(btn_frame, text="🔄 恢复默认", command=self._reset_default,
                   bootstyle="warning-outline", padding=(10, 10)).grid(row=0, column=1, sticky="ew", padx=5)
        ttk.Button(btn_frame, text="✓ 保存", command=self._on_save,
                   bootstyle="success", padding=(10, 10)).grid(row=0, column=2, sticky="ew", padx=(0, 5))

    def _reset_default(self):
        self.run_var.set(self.default_run)
        self.stop_var.set(self.default_stop)

    def _on_save(self):
        run_hk = self.run_var.get().strip().lower()
        stop_hk = self.stop_var.get().strip().lower()
        if not run_hk or not stop_hk:
            messagebox.showerror("错误", "快捷键不能为空", parent=self.dialog)
            return
        if run_hk == stop_hk:
            messagebox.showerror("错误", "运行和停止快捷键不能相同", parent=self.dialog)
            return
        if not self._validate_hotkey(run_hk):
            messagebox.showerror("错误", f"运行快捷键格式无效: {run_hk}", parent=self.dialog)
            return
        if not self._validate_hotkey(stop_hk):
            messagebox.showerror("错误", f"停止快捷键格式无效: {stop_hk}", parent=self.dialog)
            return
        self.result = (run_hk, stop_hk)
        self.dialog.destroy()

    def _validate_hotkey(self, hotkey):
        parts = hotkey.split('+')
        if not parts:
            return False
        if len(parts) == 1:
            p = parts[0]
            if p.startswith('f') and p[1:].isdigit():
                return int(p[1:]) in range(1, 13)
            return False
        modifiers = {'ctrl', 'alt', 'shift', 'cmd'}
        valid_keys = set('abcdefghijklmnopqrstuvwxyz0123456789')
        valid_keys.update([f'f{i}' for i in range(1, 13)])
        valid_keys.update(['space', 'enter', 'tab', 'esc', 'backspace', 'delete'])
        for i, part in enumerate(parts):
            part = part.strip()
            if i < len(parts) - 1:
                if part not in modifiers:
                    return False
            else:
                if part not in valid_keys:
                    return False
        return True

    def _on_close(self):
        self.dialog.destroy()

# =================================================================
# 7. VLM 设置对话框 (VLMSettingsDialog)
# =================================================================
class VLMSettingsDialog:
    # [修复 BUG-3] 恢复完整功能：load_config、测试连接、timeout 设置
    def __init__(self, parent):
        self.result = None
        try:
            import vlm_engine
            self.current_config = vlm_engine.load_config()
            self.providers = vlm_engine.get_providers()
        except Exception:
            self.current_config = {'provider': 'openai', 'api_key': '', 'model': '', 'timeout': 30, 'base_url': ''}
            self.providers = {}
        self.font_ui = ("Microsoft YaHei UI", 10)

        self.dialog = tk.Toplevel(parent)
        self.dialog.title("🤖 AI 配置设置")
        self.dialog.geometry("520x660")
        self.dialog.resizable(False, False)
        self.dialog.transient(parent)
        self.dialog.grab_set()
        self.dialog.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - self.dialog.winfo_width()) // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.dialog.winfo_height()) // 2
        self.dialog.geometry(f"+{x}+{y}")

        self._create_ui()

    def _create_ui(self):
        main_frame = ttk.Frame(self.dialog, padding=20)
        main_frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(main_frame, text="🤖 AI 大模型配置",
                  font=("Microsoft YaHei UI", 12, "bold")).pack(pady=(0, 15))

        # 提供商
        provider_frame = ttk.Labelframe(main_frame, text="AI 提供商", padding=10)
        provider_frame.pack(fill=tk.X, pady=(0, 10))
        self.provider_var = tk.StringVar(value=self.current_config.get('provider', 'openai'))
        provider_names = [f"{v['name']} ({k})" for k, v in self.providers.items()] if self.providers \
            else ['openai', 'anthropic', 'deepseek', 'zhipu', 'qwen']
        self.provider_combo = ttk.Combobox(provider_frame, values=provider_names,
                                            state="readonly", textvariable=self.provider_var,
                                            font=self.font_ui)
        self.provider_combo.pack(fill=tk.X)
        self.provider_combo.bind("<<ComboboxSelected>>", self._on_provider_change)

        # API Key
        key_frame = ttk.Labelframe(main_frame, text="API Key", padding=10)
        key_frame.pack(fill=tk.X, pady=(0, 10))
        self.api_key_var = tk.StringVar(value=self.current_config.get('api_key', ''))
        key_entry = ttk.Entry(key_frame, textvariable=self.api_key_var,
                              font=self.font_ui, show="*")
        key_entry.pack(fill=tk.X)
        self.show_key_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(key_frame, text="显示 API Key", variable=self.show_key_var,
                        command=lambda: key_entry.config(
                            show="" if self.show_key_var.get() else "*")).pack(anchor="w", pady=(5, 0))

        # 模型
        model_frame = ttk.Labelframe(main_frame, text="模型 (可选)", padding=10)
        model_frame.pack(fill=tk.X, pady=(0, 10))
        self.model_var = tk.StringVar(value=self.current_config.get('model', ''))
        ttk.Entry(model_frame, textvariable=self.model_var, font=self.font_ui).pack(fill=tk.X)
        ttk.Label(model_frame, text="留空则使用默认值",
                  font=("Microsoft YaHei UI", 8), foreground="gray").pack(anchor="w")

        # 超时
        timeout_frame = ttk.Labelframe(main_frame, text="超时时间 (秒)", padding=10)
        timeout_frame.pack(fill=tk.X, pady=(0, 10))
        self.timeout_var = tk.IntVar(value=self.current_config.get('timeout', 30))
        ttk.Spinbox(timeout_frame, from_=10, to=120,
                    textvariable=self.timeout_var, font=self.font_ui).pack(fill=tk.X)

        # 按钮
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(15, 0))
        btn_frame.columnconfigure(0, weight=1)
        btn_frame.columnconfigure(1, weight=1)
        btn_frame.columnconfigure(2, weight=1)
        ttk.Button(btn_frame, text="取消", command=self.dialog.destroy,
                   bootstyle="secondary", padding=(10, 8)).grid(row=0, column=0, sticky="ew", padx=(0, 3))
        ttk.Button(btn_frame, text="测试连接", command=self._test_connection,
                   bootstyle="info", padding=(10, 8)).grid(row=0, column=1, sticky="ew", padx=3)
        ttk.Button(btn_frame, text="保存", command=self._save,
                   bootstyle="primary", padding=(10, 8)).grid(row=0, column=2, sticky="ew", padx=(3, 0))

        ttk.Label(main_frame, text="输入 API Key，选择提供商，保存即可使用 AI 指令动作",
                  font=("Microsoft YaHei UI", 8), foreground="#666").pack(pady=(10, 0))

    def _on_provider_change(self, event):
        selected = self.provider_var.get()
        provider_key = selected.split(" (")[-1].rstrip(")") if "(" in selected else selected
        if self.providers and provider_key in self.providers:
            default_model = self.providers[provider_key].get('model', '')
            if not self.model_var.get():
                self.model_var.set(default_model)

    def _test_connection(self):
        try:
            import vlm_engine, io
            selected = self.provider_var.get()
            provider_key = selected.split(" (")[-1].rstrip(")") if "(" in selected else selected
            api_key = self.api_key_var.get().strip()
            if not api_key:
                messagebox.showwarning("提示", "请先输入 API Key", parent=self.dialog)
                return
            config = vlm_engine.DEFAULT_CONFIG.copy()
            config.update({'provider': provider_key, 'api_key': api_key,
                           'timeout': self.timeout_var.get(),
                           'system_prompt': "你是一个助手，直接回答用户问题即可。"})
            if self.model_var.get().strip():
                config['model'] = self.model_var.get().strip()
            if self.providers and provider_key in self.providers:
                config['base_url'] = self.providers[provider_key].get('base_url', '')
            self.dialog.config(cursor="watch")
            self.dialog.update()
            from PIL import ImageGrab
            screenshot = ImageGrab.grab()
            buf = io.BytesIO()
            screenshot.save(buf, format='JPEG', quality=85)
            image_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
            vlm_engine.call_vlm_api("描述你看到了什么？", image_b64=image_b64, config=config)
            messagebox.showinfo("成功", "API 连接成功！", parent=self.dialog)
        except Exception as e:
            messagebox.showerror("错误", f"连接失败:\n{str(e)}", parent=self.dialog)
        finally:
            self.dialog.config(cursor="")

    def _save(self):
        try:
            import vlm_engine
            selected = self.provider_var.get()
            provider_key = selected.split(" (")[-1].rstrip(")") if "(" in selected else selected
            config = vlm_engine.DEFAULT_CONFIG.copy()
            config['provider'] = provider_key
            config['api_key'] = self.api_key_var.get().strip()
            config['timeout'] = self.timeout_var.get()
            if self.model_var.get().strip():
                config['model'] = self.model_var.get().strip()
            if self.providers and provider_key in self.providers:
                config['base_url'] = self.providers[provider_key].get('base_url', '')
            if vlm_engine.save_config(config):
                self.result = config
                self.dialog.destroy()
            else:
                messagebox.showerror("错误", "保存配置失败", parent=self.dialog)
        except Exception as e:
            messagebox.showerror("错误", f"保存失败: {e}", parent=self.dialog)

# =================================================================
# 8. 图片提示管理器 (ImageTooltipManager)
# =================================================================
class ImageTooltipManager:
    """[修复 BUG-1] 接受 steps 列表或 getter 函数，兼容两种调用方式"""
    def __init__(self, treeview, steps_or_getter):
        self.tree = treeview
        # 支持传入 lambda: self.steps 或直接传入 list
        self._getter = steps_or_getter if callable(steps_or_getter) else lambda: steps_or_getter
        self.tooltip = None
        self.current_item = None
        self._bind_events()

    def _bind_events(self):
        self.tree.bind('<Motion>', self._on_motion)
        self.tree.bind('<Leave>', self._on_leave)

    def _on_motion(self, event):
        item = self.tree.identify_row(event.y)
        if item != self.current_item:
            self.current_item = item
            self._hide_tooltip()
            if item:
                self._show_tooltip(item, event.x_root, event.y_root)

    def _on_leave(self, event):
        self._hide_tooltip()
        self.current_item = None

    def _show_tooltip(self, item, x, y):
        try:
            steps = self._getter()
            if not steps:
                return
            idx = self.tree.index(item)
            if idx < 0 or idx >= len(steps):
                return

            step = steps[idx]
            action = step.get('action', '')
            params = step.get('params', {})

            if action not in ('FIND_IMAGE', 'IF_IMAGE_FOUND'):
                return

            img_path = params.get('path', '')
            if not img_path or not os.path.exists(img_path):
                return

            img = Image.open(img_path)
            img.thumbnail((200, 150), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(img)

            self.tooltip = tk.Toplevel(self.tree)
            self.tooltip.wm_overrideredirect(True)
            self.tooltip.wm_geometry(f"+{x+15}+{y+15}")

            label = ttk.Label(self.tooltip, image=photo)
            label.image = photo
            label.pack()

            info_text = f"{os.path.basename(img_path)}\n{img.size[0]}x{img.size[1]}"
            ttk.Label(self.tooltip, text=info_text, font=("Microsoft YaHei UI", 8)).pack()

        except Exception as e:
            print(f"图片提示加载失败: {e}")

    def _hide_tooltip(self):
        if self.tooltip:
            self.tooltip.destroy()
            self.tooltip = None

# =================================================================
# 9. 迷你状态窗口 (MiniStatusWindow)
# [修复 BUG-2] 恢复完整接口：stop_callback + update_status(status, loop)
# =================================================================
class MiniStatusWindow:
    """
    宏执行时的迷你悬浮状态栏窗口
    - 无边框、始终置顶，显示于屏幕左下角
    - 点击可停止宏
    """
    def __init__(self, parent, stop_callback):
        self.parent = parent
        self.stop_callback = stop_callback

        self.window = tk.Toplevel(parent)
        self.window.overrideredirect(True)
        self.window.attributes('-topmost', True)

        window_width = 500
        window_height = 35
        screen_height = self.window.winfo_screenheight()
        self.window.geometry(f"{window_width}x{window_height}+10+{screen_height - window_height - 50}")

        main_frame = ttk.Frame(self.window, bootstyle="primary", padding=0)
        main_frame.pack(fill=tk.BOTH, expand=True)

        self.status_label = ttk.Label(
            main_frame, text="运行中...",
            relief=tk.FLAT, anchor=tk.W, padding=(8, 5),
            bootstyle="primary-inverse", font=("Microsoft YaHei UI", 9)
        )
        self.status_label.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.loop_label = ttk.Label(
            main_frame, text="",
            relief=tk.FLAT, anchor=tk.E, padding=(0, 5, 8, 5),
            bootstyle="primary-inverse", font=("Microsoft YaHei UI", 9)
        )
        self.loop_label.pack(side=tk.RIGHT)

        for w in (main_frame, self.status_label, self.loop_label):
            w.bind("<Button-1>", self._on_click)
            w.bind("<Enter>", lambda e: self.window.config(cursor="hand2"))
            w.bind("<Leave>", lambda e: self.window.config(cursor=""))

    def _on_click(self, event):
        if self.stop_callback:
            self.stop_callback()

    def update_status(self, status_text, loop_text=""):
        """更新状态栏显示内容"""
        try:
            if self.window.winfo_exists():
                self.status_label.config(text=status_text)
                self.loop_label.config(text=loop_text)
        except tk.TclError:
            pass

    def destroy(self):
        try:
            if self.window.winfo_exists():
                self.window.destroy()
        except tk.TclError:
            pass


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
        return var  # 返回 BooleanVar

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
            widget = param_widgets[key]
            parent_frame = widget.master
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
            param_widgets[key].master.pack(fill=tk.X, pady=8)

    # 确保提示标签始终在最后
    for hint_label in hint_labels:
        hint_label.pack_forget()
        hint_label.pack(anchor="w", pady=5, fill=tk.X)


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

    # 各类型对应的参数
    command_params = ['command', 'args']
    script_params = ['script_path', 'interpreter']
    file_params = ['file_path', 'content']

    # 隐藏所有类型的参数
    all_type_params = command_params + script_params + file_params
    for key in all_type_params:
        if key in param_widgets:
            widget = param_widgets[key]
            parent_frame = widget.master
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
            frame = param_widgets[key].master
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
