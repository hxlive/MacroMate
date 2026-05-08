# -*- coding: utf-8 -*-
# MacroAssistant.py
# 描述: 自动化宏的 GUI 界面
# 版本: 1.6.5
# 变更: 迁移部分代码至gui_utils.py
#       修复一些小bug
#       修复任务栏图标显示问题
        

# 使用: 
#   - GUI 模式: python MacroAssistant.py
#   - 命令行: python MacroAssistant.py script.json
#             python MacroAssistant.py --run script.json
#             python MacroAssistant.py --theme darkly (指定主题)

import sys
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import json
import pyautogui
import time
import threading
import ttkbootstrap as tb
from pynput import keyboard
import os
import sys
import queue
from PIL import Image, ImageGrab, ImageTk
import functools
import webbrowser

# 强制启用 DPI 感知，解决 125%/150% 缩放下的坐标偏移问题
try:
    if sys.platform == 'win32':
        import ctypes
        # 设置 DPI 感知级别为 "PerMonitorV2" (Awareness 2)
        # 这会让 ImageGrab 和 pyautogui 的坐标系强制对齐
        ctypes.windll.shcore.SetProcessDpiAwareness(2) 
except Exception:
    try:
        # 回退旧版 API (兼容 Win7/8)
        ctypes.windll.user32.SetProcessDPIAware()
    except: pass
    
# 依赖：快捷键冲突检测
try:
    if sys.platform == 'win32':
        import ctypes
        import ctypes.wintypes
        import win32con
        HOTKEY_CHECK_AVAILABLE = True
except ImportError:
    HOTKEY_CHECK_AVAILABLE = False
    print("[配置] FAIL 未找到 pywin32 库 (pip install pywin32)。将跳过快捷键冲突检测。")

# =================================================================
# 全局配置
# =================================================================
APP_VERSION = "1.6.5"
APP_TITLE = f"宏助手 (Macro Assistant) v{APP_VERSION}"
APP_ICON = "app_icon.ico" 
CONFIG_FILE = "macro_settings.json"
MAX_RECENT_FILES = 5

DEFAULT_HOTKEY_RUN = "Ctrl+F10"
DEFAULT_HOTKEY_STOP = "Ctrl+F11"
# =================================================================
# 性能优化常量
STATUS_QUEUE_CHECK_INTERVAL_IDLE = 500  # 空闲时状态队列检查间隔（毫秒）
STATUS_QUEUE_CHECK_INTERVAL_RUNNING = 50  # 运行时状态队列检查间隔（毫秒）
STATUS_QUEUE_MAX_BATCH = 50  # 状态队列单次最大处理数
OCR_PRELOAD_DELAY = 100  # OCR引擎预热延迟（毫秒）


# resource_path 和 get_icon_path 已迁移到 gui_utils.py

import logging
logging.getLogger('rapidocr').setLevel(logging.WARNING)

print("[DEBUG] 开始导入核心模块...")
try:
    import core_engine as macro_engine
    print("[DEBUG] core_engine 导入成功")
    import ocr_engine
    print("[DEBUG] ocr_engine 导入成功")
    import vlm_engine
    print("[DEBUG] vlm_engine 导入成功")
    from core_engine import HotkeyUtils, MacroSchema, validate_macro_data
    # [变更] 导入重构后的 gui_utils 组件
    import gui_utils
    from gui_utils import (
        RegionSelector,
        HotkeyEntry,
        HotkeySettingsDialog,
        ImageTooltipManager,
        MouseTracker,
        AutoWrapLabel,
        parse_region_string,
        VLMSettingsDialog,
        MiniStatusWindow,
        ParamWidgetFactory,
        param_display_to_internal,
        param_internal_to_display,
        resource_path,
        get_icon_path,
        update_loop_params,
        update_run_params
    )
except ImportError as e:
    messagebox.showerror("导入错误", f"缺少必要的模块文件或导入失败: {e}\n请确保 core_engine.py, ocr_engine.py, gui_utils.py 都在同一目录。")
    exit()

# -----------------------------------------------------------------
# 快捷键录制与冲突检测
# -----------------------------------------------------------------
PYNPUT_TO_VK = HotkeyUtils.PYNPUT_TO_VK
VK_TO_PYNPUT = HotkeyUtils.VK_TO_PYNPUT

if HOTKEY_CHECK_AVAILABLE:
    PYNPUT_MOD_TO_WIN_MOD = {
        'ctrl': win32con.MOD_CONTROL,
        'alt': win32con.MOD_ALT,
        'shift': win32con.MOD_SHIFT,
        'cmd': win32con.MOD_WIN,
    }

def capitalize_hotkey_str(s):
    """辅助函数：将 ctrl+f10 转换为 Ctrl+F10"""
    return HotkeyUtils.format_hotkey_display(s)


class MacroApp:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1160x820")  # 稍微加宽以适应优化后的列宽 
        
        self.font_ui = ("Microsoft YaHei UI", 10)
        self.font_code = ("Consolas", 10)
        
        self.root.style.configure(".", font=self.font_ui)
        # <--- Treeview 样式配置
        self.root.style.configure("Treeview", font=self.font_code, rowheight=25)
        self.root.style.configure("Treeview.Heading", font=self.font_ui)
        
        self.is_app_running = True
        self.root.protocol("WM_DELETE_WINDOW", self.on_exit)
        
        # 设置窗口图标 (确保任务栏显示)
        icon_path = get_icon_path()
        if icon_path and os.path.exists(icon_path):
            try: 
                self.root.iconbitmap(icon_path)
                print(f"[Info] 图标已设置: {icon_path}")
                
                # Windows 特定：确保任务栏图标正确显示
                if sys.platform == 'win32':
                    try:
                        import ctypes
                        # 设置应用程序用户模型ID,确保任务栏分组和图标显示正确
                        myappid = f'hxlive.macroassistant.{APP_VERSION}'
                        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
                        print(f"[Info] AppUserModelID 已设置: {myappid}")
                    except Exception as e:
                        print(f"[警告] 设置 AppUserModelID 失败: {e}")
            except Exception as e: 
                print(f"[错误] 设置图标失败: {e}")
        else:
            print(f"[警告] 未找到图标文件,使用默认图标")
        
        self.steps = []
        self.editing_index = None
        self.is_macro_running = False
        self.last_test_location = None 
        self.current_run_context = None 
        self._macro_thread = None          # [新增] 保存执行线程引用以支持强制中断
        self.held_keys = set()
        
        # [新增] 迷你状态栏窗口
        self.mini_status_window = None
        
        self.hotkey_run_str = tb.StringVar(value=DEFAULT_HOTKEY_RUN)
        self.hotkey_stop_str = tb.StringVar(value=DEFAULT_HOTKEY_STOP)
        self.hotkey_listener = None
        
        self.current_theme = tb.StringVar(value=self.root.style.theme_use())
        self.skip_confirm_var = tb.BooleanVar(value=False)
        self.dont_minimize_var = tb.BooleanVar(value=False)
        self.enhanced_mode_var = tb.BooleanVar(value=True)
        self.recent_files = []
        self.status_queue = queue.Queue()
        
        # [变更] 使用 MouseTracker 类替代原有的 job 和 func
        self.mouse_pos_var = tb.StringVar()
        self.mouse_tracker = MouseTracker(self.root, self.mouse_pos_var)
        
        # OCR 引擎健康检查与映射
        self.FULL_OCR_NAME_MAP = {
            'auto': '自动选择 (Auto)',
            'winocr': 'Windows 10/11 OCR',
            'rapidocr': 'RapidOCR',
            'tesseract': 'Tesseract OCR',
            'none': '无可用OCR引擎'
        }
        self.FULL_OCR_KEY_MAP = {name: key for key, name in self.FULL_OCR_NAME_MAP.items()}
        # OCR 引擎检测将在后台线程运行，先用占位值保证主线程快速进入 mainloop
        self.available_ocr_engines = []   # 后台检测完成前的占位值
        self.available_ocr_keys = ['auto']
        
        # [重构] 创建参数控件工厂实例（已迁移到 gui_utils.py）
        self.widget_factory = ParamWidgetFactory(
            font_ui=self.font_ui,
            font_code=self.font_code,
            ocr_name_map=self.FULL_OCR_NAME_MAP
        )

        # ──────────────────────────────────────────────────────────────
        # 显示 loading，将重型 UI 构建延迟到 mainloop 启动后执行。
        # 窗口出现时已在事件循环中，不会触发「未响应」。
        # ──────────────────────────────────────────────────────────────
        self.root.update_idletasks()
        self._splash_label = tk.Label(
            self.root,
            text="正在加载界面...",
            font=("Microsoft YaHei UI", 14),
            fg="#666666",
            bg="#FFFFFF"
        )
        self._splash_label.place(relx=0.5, rely=0.5, anchor="center")
        self.root.update_idletasks()
        self.root.after(10, self._deferred_ui_init)

    def _deferred_ui_init(self):
        """mainloop 已启动后才执行 UI 构建，避免窗口冻结。"""
        if hasattr(self, '_splash_label') and self._splash_label:
            self._splash_label.destroy()
            self._splash_label = None

        self._init_menu()
        self._init_ui()

        # 初始化悬浮预览管理器
        self.tooltip_manager = ImageTooltipManager(self.steps_tree, lambda: self.steps)

        self.load_app_settings()
        self.update_recent_files_menu()
        self.update_status_bar_hotkeys()
        self.root.after(500, self.check_hotkey_conflicts)
        self.start_hotkey_listener()
        # OCR 引擎异步检测
        threading.Thread(target=self._detect_ocr_engines_bg, daemon=True).start()
        self._check_status_queue()

    # ------------------------------------------------------------------
    # OCR 引擎异步检测（避免阻塞主线程）
    # ------------------------------------------------------------------
    def _detect_ocr_engines_bg(self):
        """后台线程：检测可用 OCR 引擎并预热，完成后回调主线程更新状态。"""
        try:
            engines = ocr_engine.get_available_engines()
            # 检测完成后顺手预热（合并两次后台任务）
            ocr_engine.preload_engines()
        except Exception as e:
            print(f"[OCR] 后台检测异常: {e}")
            engines = [('none', '无可用OCR引擎')]
        self.root.after(0, self._on_ocr_engines_ready, engines)

    def _on_ocr_engines_ready(self, engines):
        """主线程回调：OCR 引擎检测完成，更新状态。"""
        self.available_ocr_engines = engines
        self.available_ocr_keys = [e[0] for e in engines]
        if 'none' in self.available_ocr_keys:
            print("[警告] 未找到任何可用的OCR引擎 (RapidOCR, Tesseract, WinOCR)。")
            self.status_var.set("WARN 未找到可用 OCR 引擎，文本查找功能不可用。")
        else:
            engine_names = ' / '.join(e[1] for e in engines)
            print(f"[OCR] 引擎就绪: {engine_names}")

    def _init_menu(self):
        self.menu_bar = tk.Menu(self.root)
        self.root.config(menu=self.menu_bar)
        
        file_menu = tk.Menu(self.menu_bar, tearoff=0, font=self.font_ui)
        self.menu_bar.add_cascade(label="  文件  ", menu=file_menu)
        file_menu.add_command(label="📄 新建宏", accelerator="Ctrl+N", command=self.new_macro)
        file_menu.add_command(label="📂 打开宏...", accelerator="Ctrl+O", command=self.load_macro)
        file_menu.add_command(label="💾 保存宏...", accelerator="Ctrl+S", command=self.save_macro)
        file_menu.add_separator()
        self.recent_files_menu = tk.Menu(file_menu, tearoff=0, font=self.font_ui)
        file_menu.add_cascade(label="最近加载", menu=self.recent_files_menu)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.on_exit)

        self.root.bind('<Control-n>', lambda e: self.new_macro())
        self.root.bind('<Control-o>', lambda e: self.load_macro())
        self.root.bind('<Control-s>', lambda e: self.save_macro())

        settings_menu = tk.Menu(self.menu_bar, tearoff=0, font=self.font_ui)
        self.menu_bar.add_cascade(label="  设置  ", menu=settings_menu)
        settings_menu.add_command(label="⌨️ 快捷键设置...", command=self.open_hotkey_settings)
        settings_menu.add_separator()
        settings_menu.add_command(label="🤖  AI 设置...", command=self.open_vlm_settings)

        theme_menu = tk.Menu(self.menu_bar, tearoff=0, font=self.font_ui)
        self.menu_bar.add_cascade(label="  主题  ", menu=theme_menu)
        
        light_themes = ['litera', 'cosmo', 'flatly', 'journal', 'lumen', 'minty', 'pulse', 'sandstone', 'united', 'yeti']
        for theme in light_themes:
            theme_menu.add_radiobutton(label=f"亮 - {theme.capitalize()}", variable=self.current_theme, value=theme, command=self.change_theme)
        theme_menu.add_separator()
        dark_themes = ['superhero', 'cyborg', 'darkly', 'solar']
        for theme in dark_themes:
            theme_menu.add_radiobutton(label=f"暗 - {theme.capitalize()}", variable=self.current_theme, value=theme, command=self.change_theme)

        about_menu = tk.Menu(self.menu_bar, tearoff=0, font=self.font_ui)
        self.menu_bar.add_cascade(label="  关于  ", menu=about_menu)
        about_menu.add_command(label="关于", command=self.show_about_dialog)

    def _init_ui(self):
        status_bar_frame = ttk.Frame(self.root, bootstyle="primary")
        status_bar_frame.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_var = tk.StringVar()
        self.status_label_left = ttk.Label(status_bar_frame, textvariable=self.status_var, relief=tk.FLAT, anchor=tk.W, padding=5, bootstyle="primary-inverse", font=self.font_ui)
        self.status_label_left.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.loop_status_var = tk.StringVar()
        self.loop_status_label_right = ttk.Label(status_bar_frame, textvariable=self.loop_status_var, relief=tk.FLAT, anchor=tk.E, padding=(0, 5, 5, 5), bootstyle="primary-inverse", font=self.font_ui)
        self.loop_status_label_right.pack(side=tk.RIGHT)

        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # =====================================================================
        # 左侧面板 (Treeview + Preview)
        # =====================================================================
        list_frame = ttk.Frame(main_frame, padding=10)
        list_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # 标题栏
        title_frame = ttk.Frame(list_frame)
        title_frame.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(title_frame, text="宏步骤序列:", font=("Microsoft YaHei UI", 11, "bold")).pack(side=tk.LEFT)
        
        # --- Treeview 替换 Listbox ---
        tree_frame = ttk.Frame(list_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        
        columns = ("id", "action", "params")
        self.steps_tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="browse")
        
        self.steps_tree.heading("id", text="#")
        self.steps_tree.heading("action", text="动作")
        self.steps_tree.heading("params", text="参数详情 / 备注")
        
        # 优化列宽：缩小序号列，适当缩小动作列，扩大参数列
        self.steps_tree.column("id", width=45, minwidth=40, stretch=False, anchor="center")
        self.steps_tree.column("action", width=220, minwidth=200, stretch=False)
        self.steps_tree.column("params", width=320, minwidth=280, stretch=True)
        
        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.steps_tree.yview)
        self.steps_tree.configure(yscrollcommand=scrollbar.set)
        
        self.steps_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # 绑定事件
        self.steps_tree.bind("<Double-1>", lambda e: self.load_step_for_edit())
        
        # [新增] 右键菜单
        self.tree_menu = tk.Menu(self.root, tearoff=0, font=self.font_ui)
        self.tree_menu.add_command(label="屏蔽/启用选中步骤", command=self.toggle_step_enabled)
        self.steps_tree.bind("<Button-3>", self.show_tree_menu)
        
        # 配置编辑行的样式
        self.steps_tree.tag_configure('editing', background='#FFF3CD')
        self.steps_tree.tag_configure('disabled', foreground='#999999')
        # 备注行使用与其他行相同的样式

        left_bottom_frame = ttk.Frame(list_frame)
        left_bottom_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(10,0))
        left_bottom_frame.columnconfigure(0, weight=1); left_bottom_frame.columnconfigure(1, weight=1)
        left_bottom_frame.columnconfigure(2, weight=1); left_bottom_frame.columnconfigure(3, weight=1)

        self.move_up_btn = ttk.Button(left_bottom_frame, text="↑ 上移", command=lambda: self.move_step("up"), bootstyle="primary-outline", padding=(10, 6))
        self.move_up_btn.grid(row=0, column=0, sticky="nsew", padx=(0, 2), pady=(0, 5))
        self.move_down_btn = ttk.Button(left_bottom_frame, text="↓ 下移", command=lambda: self.move_step("down"), bootstyle="primary-outline", padding=(10, 6))
        self.move_down_btn.grid(row=0, column=1, sticky="nsew", padx=2, pady=(0, 5))
        self.remove_btn = ttk.Button(left_bottom_frame, text="🗑 删除选中", command=self.remove_step, bootstyle="danger-outline", padding=(10, 6))
        self.remove_btn.grid(row=0, column=2, sticky="nsew", padx=2, pady=(0, 5))
        self.load_step_btn = ttk.Button(left_bottom_frame, text="✎ 修改步骤", command=self.load_step_for_edit, bootstyle="info-outline", padding=(10, 6))
        self.load_step_btn.grid(row=0, column=3, sticky="nsew", padx=(2, 0), pady=(0, 5))

        self.run_btn = ttk.Button(left_bottom_frame, text="", command=self.run_macro, bootstyle="success", padding=(15, 10))
        self.run_btn.grid(row=1, column=0, columnspan=4, sticky="nsew", padx=(0, 0), pady=5) 
        
        check_frame = ttk.Frame(left_bottom_frame)
        check_frame.grid(row=2, column=0, columnspan=4, sticky="nsew", pady=(10, 0))
        check_frame.columnconfigure(0, weight=1); check_frame.columnconfigure(1, weight=1)
        
        skip_check = ttk.Checkbutton(check_frame, text="跳过运行前的确认提示", variable=self.skip_confirm_var, bootstyle="primary-round-toggle")
        skip_check.grid(row=0, column=0, sticky="w", padx=2, pady=(0, 5)) 
        minimize_check = ttk.Checkbutton(check_frame, text="运行时主界面不最小化", variable=self.dont_minimize_var, bootstyle="primary-round-toggle")
        minimize_check.grid(row=0, column=1, sticky="w", padx=2, pady=(0, 5))
        
        enhanced_check = ttk.Checkbutton(check_frame, text="开启增强模式 (多级缩放匹配与 OCR 放大预处理)", variable=self.enhanced_mode_var, bootstyle="success-round-toggle")
        enhanced_check.grid(row=1, column=0, columnspan=2, sticky="w", padx=2)
        
        # =====================================================================
        # 右侧面板
        # =====================================================================
        add_frame = ttk.Labelframe(main_frame, text="添加新步骤", padding=10)
        add_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=10, pady=10, expand=False)
        
        add_frame.pack_propagate(False)  # 禁止子控件影响父容器
        add_frame.configure(width=380)   # 固定宽度
        
        right_bottom_frame = ttk.Frame(add_frame)
        right_bottom_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(10,0))
        right_bottom_frame.columnconfigure(0, weight=2); right_bottom_frame.columnconfigure(1, weight=1) 
        
        self.add_step_btn = ttk.Button(right_bottom_frame, text="＋ 添加到序列 >>", command=self.add_or_update_step, bootstyle="success", padding=(12, 8))
        self.add_step_btn.grid(row=0, column=0, sticky="nsew", padx=(0, 2), columnspan=2)
        self.cancel_edit_btn = ttk.Button(right_bottom_frame, text="✕ 取消修改", command=self.cancel_edit_mode, bootstyle="secondary", padding=(10, 6))
        
        ttk.Label(add_frame, text="选择动作:").pack(anchor="w")
        self.action_type = ttk.Combobox(add_frame, state="readonly", font=self.font_ui, height=16) 
        self.action_type['values'] = list(MacroSchema.ACTION_TRANSLATIONS.values())
        self.action_type.current(0)
        
        self.action_type.pack(anchor="w", fill=tk.X, pady=5)
        self.action_type.bind("<<ComboboxSelected>>", self.update_param_fields)
        self.param_frame = ttk.Frame(add_frame)
        self.param_frame.pack(fill=tk.X, expand=True, pady=5)
        
        # [变更] 不再需要绑定 Configure 事件，AutoWrapLabel 会自动处理
        self.param_widgets = {}
        self.update_param_fields(None)

    # --- Treeview 辅助方法 ---
    def _param_display_to_internal(self, key, display_value):
        """将UI显示值转换为内部存储值（委托给 gui_utils）"""
        return param_display_to_internal(
            key, 
            display_value, 
            self.FULL_OCR_KEY_MAP,
            MacroSchema.LANG_OPTIONS,
            MacroSchema.CLICK_OPTIONS
        )
    
    def _param_internal_to_display(self, key, internal_value):
        """将内部存储值转换为UI显示值（委托给 gui_utils）"""
        return param_internal_to_display(
            key,
            internal_value,
            self.FULL_OCR_NAME_MAP,
            MacroSchema.LANG_VALUES_TO_NAME,
            MacroSchema.CLICK_VALUES_TO_NAME,
            self.available_ocr_keys
        )

    def _get_selected_index(self):
        """获取当前选中项的索引"""
        selected_items = self.steps_tree.selection()
        if not selected_items: return None
        return self.steps_tree.index(selected_items[0])

    def update_status_bar_hotkeys(self):
        """更新状态栏和运行按钮上的快捷键提示"""
        run_display = capitalize_hotkey_str(self.hotkey_run_str.get())
        stop_display = capitalize_hotkey_str(self.hotkey_stop_str.get())
        self.status_var.set(f"准备就绪...  |  [{run_display}] 启动宏  |  [{stop_display}] 停止宏")
        self.run_btn.config(text=f"▶ 运行宏 ({run_display})")

    def open_hotkey_settings(self):
        """打开快捷键设置对话框"""
        dialog = HotkeySettingsDialog(
            self.root, 
            self.hotkey_run_str.get(), 
            self.hotkey_stop_str.get(),
            default_run=DEFAULT_HOTKEY_RUN,
            default_stop=DEFAULT_HOTKEY_STOP
        )
        self.root.wait_window(dialog.dialog)
        
        if dialog.result:
            new_run, new_stop = dialog.result
            self.hotkey_run_str.set(new_run)
            self.hotkey_stop_str.set(new_stop)
            
            self.on_save_hotkeys()
            
            messagebox.showinfo(
                "设置已保存",
                f"快捷键已更新:\n\n"
                f"运行宏: {capitalize_hotkey_str(new_run)}\n"
                f"停止宏: {capitalize_hotkey_str(new_stop)}",
                parent=self.root
            )
            
    def on_save_hotkeys(self):
        """保存并重启监听器"""
        self.save_app_settings()
        
        if not self.check_hotkey_conflicts(show_success=False):
            messagebox.showwarning("冲突警告", "快捷键已保存，但检测到冲突。\n请确保没有其他程序占用它。", parent=self.root)
        
        self.restart_hotkey_listener()
        self.update_status_bar_hotkeys()

    def open_vlm_settings(self):
        """打开 VLM (AI) 设置对话框"""
        dialog = VLMSettingsDialog(self.root)
        self.root.wait_window(dialog.dialog)
        
        if dialog.result:
            messagebox.showinfo("设置已保存", "AI 配置已更新", parent=self.root)

    def show_about_dialog(self):
        """显示关于对话框"""
        # 防止重复打开关于对话框
        if hasattr(self, '_about_dialog_ref') and self._about_dialog_ref and self._about_dialog_ref.winfo_exists():
            self._about_dialog_ref.focus_force()
            return
        
        # 创建关于对话框
        about_dialog = tk.Toplevel(self.root)
        self._about_dialog_ref = about_dialog  # 保存引用
        about_dialog.title("关于")
        about_dialog.geometry("500x400")  # 增加高度和宽度确保按钮不被截断
        about_dialog.resizable(False, False)
        about_dialog.transient(self.root)
        about_dialog.grab_set()
        
        # 获取图标路径（只调用一次）
        icon_path = get_icon_path()
        
        # 设置窗口图标
        if icon_path and os.path.exists(icon_path):
            try:
                about_dialog.iconbitmap(icon_path)
            except (OSError, tk.TclError) as e:
                print(f"[警告] 设置关于对话框图标失败: {e}")
        
        # 相对于主窗口居中显示
        about_dialog.update_idletasks()
        
        # 获取主窗口的位置和大小
        main_x = self.root.winfo_x()
        main_y = self.root.winfo_y()
        main_width = self.root.winfo_width()
        main_height = self.root.winfo_height()
        
        # 获取关于对话框的大小
        dialog_width = about_dialog.winfo_width()
        dialog_height = about_dialog.winfo_height()
        
        # 计算居中位置
        x = main_x + (main_width - dialog_width) // 2
        y = main_y + (main_height - dialog_height) // 2
        
        about_dialog.geometry(f"+{x}+{y}")
        
        # 主框架 - 减小内边距
        main_frame = ttk.Frame(about_dialog, padding=20)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # ========== 顶部：图标和软件标题区域 ==========
        # 外层容器 - 将所有内容居中
        top_outer = ttk.Frame(main_frame)
        top_outer.pack(fill=tk.X, pady=(5, 18))
        
        # 内层容器 - 实际内容区域
        top_frame = ttk.Frame(top_outer)
        top_frame.pack(anchor="center")
        
        # 左侧：图标
        icon_container = ttk.Frame(top_frame)
        icon_container.pack(side=tk.LEFT, padx=(0, 28))
        
        # 显示图标（使用已获取的icon_path）
        if icon_path and os.path.exists(icon_path):
            try:
                # 使用上下文管理器加载图标，避免资源泄漏
                from PIL import Image, ImageTk
                with Image.open(icon_path) as icon_img:
                    # 调整大小
                    resized_img = icon_img.resize((96, 96), Image.Resampling.LANCZOS)
                    icon_photo = ImageTk.PhotoImage(resized_img)
                    
                    icon_label = ttk.Label(icon_container, image=icon_photo)
                    icon_label.image = icon_photo  # 保持引用防止被垃圾回收
                    icon_label.pack()
            except (OSError, IOError) as e:
                print(f"[警告] 加载图标图像失败: {e}")
                ttk.Label(icon_container, text="🔧", font=("Microsoft YaHei UI", 48)).pack()
        else:
            ttk.Label(icon_container, text="🔧", font=("Microsoft YaHei UI", 48)).pack()
        
        # 右侧：软件标题和版本（左对齐）
        title_container = ttk.Frame(top_frame)
        title_container.pack(side=tk.LEFT, pady=10)
        
        # 软件名称
        ttk.Label(title_container, 
                 text="宏助手",
                 font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="w", pady=(0, 2))
        
        ttk.Label(title_container, 
                 text="Macro Assistant",
                 font=("Microsoft YaHei UI", 10),
                 foreground="#666666").pack(anchor="w", pady=(0, 6))
        
        # 版本信息
        version_frame = ttk.Frame(title_container)
        version_frame.pack(anchor="w")
        
        version_label = ttk.Label(version_frame, 
                                 text=f" v{APP_VERSION} ",
                                 font=("Consolas", 9, "bold"),
                                 bootstyle="info",
                                 padding=(6, 2))
        version_label.pack(side=tk.LEFT)

        
        # ========== 分隔线 ==========
        separator1 = ttk.Separator(main_frame, orient='horizontal')
        separator1.pack(fill='x', pady=(0, 18))
        
        # ========== 中部：详细信息区域 ==========
        info_frame = ttk.Frame(main_frame)
        info_frame.pack(fill=tk.X, pady=(0, 18), padx=5)
        
        # 使用网格布局，更整齐
        info_frame.columnconfigure(1, weight=1)
        
        # 作者信息
        ttk.Label(info_frame, 
                 text="软件作者",
                 font=("Microsoft YaHei UI", 10, "bold"),
                 foreground="#777777").grid(row=0, column=0, sticky="w", padx=(0, 20), pady=6)
        
        ttk.Label(info_frame, 
                 text="寒星",
                 font=("Microsoft YaHei UI", 10)).grid(row=0, column=1, sticky="w", pady=6)
        
        # 项目主页
        ttk.Label(info_frame, 
                 text="项目主页",
                 font=("Microsoft YaHei UI", 10, "bold"),
                 foreground="#777777").grid(row=1, column=0, sticky="w", padx=(0, 20), pady=6)
        
        # 链接标签
        link_label = ttk.Label(info_frame, 
                              text="github.com/hxlive/MacroAssistant",
                              font=("Microsoft YaHei UI", 10),
                              foreground="#0066CC",
                              cursor="hand2")
        link_label.grid(row=1, column=1, sticky="w", pady=6)
        
        # 绑定点击事件
        link_label.bind("<Button-1>", 
                       lambda e: webbrowser.open("https://github.com/hxlive/MacroAssistant/"))
        
        # 鼠标悬停效果
        def on_enter(e):
            link_label.config(font=("Microsoft YaHei UI", 10, "underline"), foreground="#0052A3")
        def on_leave(e):
            link_label.config(font=("Microsoft YaHei UI", 10), foreground="#0066CC")
        
        link_label.bind("<Enter>", on_enter)
        link_label.bind("<Leave>", on_leave)
        
        # ========== 分隔线 ==========
        separator2 = ttk.Separator(main_frame, orient='horizontal')
        separator2.pack(fill='x', pady=(0, 18))
        
        # ========== 底部：操作按钮区域 ==========
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=(0, 5))
        
        # 关闭按钮 - 居中显示
        close_btn = ttk.Button(button_frame, 
                              text="确  定",
                              command=about_dialog.destroy,
                              bootstyle="primary",
                              width=18,
                              padding=(15, 8))
        close_btn.pack(anchor="center")
        
        # 对话框销毁时清理引用
        def on_dialog_destroy(event=None):
            self._about_dialog_ref = None
        
        about_dialog.bind("<Destroy>", on_dialog_destroy)
        
        # ESC键和回车键关闭
        about_dialog.bind("<Escape>", lambda e: about_dialog.destroy())
        about_dialog.bind("<Return>", lambda e: about_dialog.destroy())


    def on_exit(self):
        self.is_app_running = False
        self.held_keys.clear()
        
        # [变更] 使用 MouseTracker 类停止
        self.mouse_tracker.stop()
            
        if self.hotkey_listener:
            print("[Info] 正在停止快捷键监听器...")
            try:
                self.hotkey_listener.stop()
                self.hotkey_listener.join(timeout=0.5) 
            except Exception as e:
                print(f"[警告] 停止监听器时出错: {e}")
                
        try:
            self.root.quit()
            self.root.destroy()
        except Exception: 
            pass

    def update_param_fields(self, event):
        self.last_test_location = None
        
        # [变更] 停止鼠标追踪
        self.mouse_tracker.stop()
        self.mouse_pos_var.set("")
        
        for widget in self.param_frame.winfo_children(): widget.destroy()
        self.param_widgets = {}
        action_key = MacroSchema.ACTION_KEYS_TO_NAME.get(self.action_type.get())
        if not action_key: return
        
        if action_key in ('FIND_TEXT', 'IF_TEXT_FOUND'):
            if 'none' in self.available_ocr_keys:
                self.widget_factory.create_hint_label(self.param_frame, 
                    "FAIL 错误: 未找到可用的OCR引擎。\n"
                    "请先安装 RapidOCR (推荐) 或 Tesseract，\n"
                    "然后重启本程序。",
                    bootstyle="danger")
                self.action_type.set(MacroSchema.ACTION_TRANSLATIONS['FIND_IMAGE'])
                self.update_param_fields(None)
                return
        
        if action_key == 'FIND_IMAGE':
            self.param_widgets['path'] = self.widget_factory.create_param_entry(self.param_frame, "path", "图像路径:", "button.png")
            self.param_widgets['region'] = self.widget_factory.create_region_selector(self.param_frame, "", self.on_select_region)
            self.param_widgets['confidence'] = self.widget_factory.create_param_entry(self.param_frame, "confidence", "置信度(0.1-1.0):", "0.8")
            self.widget_factory.create_hint_label(self.param_frame, "* 提示：如果识别失败，请调低置信度")
            self.widget_factory.create_browse_button(self.param_frame, self.browse_image)
            self.widget_factory.create_test_button(self.param_frame, "🧪 测试查找图像", self.on_test_find_image_click)
            
        elif action_key == 'FIND_TEXT':
            self.param_widgets['text'] = self.widget_factory.create_param_entry(self.param_frame, "text", "查找的文本:", "确定")
            self.param_widgets['region'] = self.widget_factory.create_region_selector(self.param_frame, "", self.on_select_region)
            self.param_widgets['lang'] = self.widget_factory.create_param_combobox(self.param_frame, "lang", "语言:", list(MacroSchema.LANG_OPTIONS.keys()))
            self.param_widgets['engine'] = self.widget_factory.create_ocr_engine_combobox(self.param_frame, self.available_ocr_keys)
            
            # === 保存到剪贴板选项（勾选后才展开从属控件）===
            self.param_widgets['save_to_clipboard'] = self.widget_factory.create_param_checkbox(self.param_frame, "save_to_clipboard", "[OK] 保存识别结果到剪贴板", default=False)

            # 始终占位的容器（位于 checkbox 下方，测试按钮上方）
            _sub_ft = ttk.Frame(self.param_frame)
            _sub_ft.pack(fill=tk.X)  # 永不 pack_forget，保持位置

            # 提取模式输入框
            _ep_frame_ft = ttk.Frame(_sub_ft)
            ttk.Label(_ep_frame_ft, text="提取模式 (正则，可选):", font=self.font_ui).pack(anchor="w")
            _ep_entry_ft = ttk.Entry(_ep_frame_ft, width=25, font=self.font_ui)
            _ep_entry_ft.insert(0, r"\d+")
            _ep_entry_ft.pack(anchor="w", fill=tk.X)
            self.param_widgets['extract_pattern'] = _ep_entry_ft

            # 说明提示
            _hint_ft = AutoWrapLabel(_sub_ft,
                text="提取模式: 用正则表达式过滤识别结果，如 \\d+ 只提取数字；留空则保存全部文本。",
                font=self.font_ui, style="secondary.TLabel")

            # 初始隐藏（内部子件不 pack）
            def _toggle_ft(var=self.param_widgets['save_to_clipboard'],
                           ef=_ep_frame_ft, hint=_hint_ft):
                if var.get():
                    ef.pack(fill=tk.X, pady=8)
                    hint.pack(anchor="w", pady=5, fill=tk.X)
                else:
                    ef.pack_forget()
                    hint.pack_forget()
            self.param_widgets['save_to_clipboard'].trace_add('write', lambda *_: _toggle_ft())

            self.widget_factory.create_test_button(self.param_frame, "🧪 测试查找文本 (OCR)", self.on_test_find_text_click)
            
        elif action_key == 'MOVE_OFFSET':
            self.param_widgets['x_offset'] = self.widget_factory.create_param_entry(self.param_frame, "x_offset", "X 偏移:", "10")
            self.param_widgets['y_offset'] = self.widget_factory.create_param_entry(self.param_frame, "y_offset", "Y 偏移:", "0")
        elif action_key == 'CLICK':
            self.param_widgets['button'] = self.widget_factory.create_param_combobox(self.param_frame, "button", "按键:", list(MacroSchema.CLICK_OPTIONS.keys()))
        
        elif action_key == 'SCROLL':
            self.param_widgets['amount'] = self.widget_factory.create_param_entry(self.param_frame, "amount", "滚动量 (正数=上, 负数=下):", "100")
            self.param_widgets['x'] = self.widget_factory.create_param_entry(self.param_frame, "x", "X 坐标 (可选):", "")
            self.param_widgets['y'] = self.widget_factory.create_param_entry(self.param_frame, "y", "Y 坐标 (可选):", "")
            self.widget_factory.create_hint_label(self.param_frame, "* 提示: 如果 X, Y 为空，将在当前鼠标位置滚动。")

        elif action_key == 'WAIT':
            self.param_widgets['ms'] = self.widget_factory.create_param_entry(self.param_frame, "ms", "等待 (毫秒):", "500")
        elif action_key == 'TYPE_TEXT':
            self.param_widgets['text'] = self.widget_factory.create_param_entry(self.param_frame, "text", "输入文本:", "你好")
            self.widget_factory.create_hint_label(self.param_frame, 
                "* 此功能使用剪贴板 (Ctrl+V)，以支持中文及复杂文本输入。\n"
                "* 支持占位符: {CLIPBOARD} 将替换为剪贴板内容\n"
                "* 示例: '订单号: {CLIPBOARD}' → '订单号: 12345'")
        elif action_key == 'PRESS_KEY':
            self.param_widgets['key'] = self.widget_factory.create_param_entry(self.param_frame, "key", "按键或组合键 (Enter, Ctrl+C):", "Enter")
        
        elif action_key == 'AI_COMMAND':
            # AI 自然语言指令
            self.param_widgets['instruction'] = self.widget_factory.create_param_entry(self.param_frame, "instruction", "AI 指令:", "点击列表里价格最低的那个商品")
            self.param_widgets['region'] = self.widget_factory.create_region_selector(self.param_frame, "", self.on_select_region)
            self.widget_factory.create_hint_label(self.param_frame, 
                "* 提示: 输入自然语言指令，如 '点击确定按钮'\n"
                "* AI 会分析屏幕截图，理解指令并返回坐标\n"
                "* 支持: OpenAI, Anthropic, DeepSeek, 智谱, 通义千问等")
            self.widget_factory.create_test_button(self.param_frame, "🧪 测试 AI 指令", self.on_test_ai_command_click)
        
        elif action_key == 'ACTIVATE_WINDOW':
            self.param_widgets['title'] = self.widget_factory.create_param_entry(self.param_frame, "title", "窗口标题 (支持部分匹配):", "记事本")
            self.widget_factory.create_hint_label(self.param_frame, "* 提示: 宏将查找标题中包含此文本的窗口，并将其激活到最前端。")
        
        elif action_key == 'NOTE':
            # 备注（仅用于注释，不执行任何操作）
            self.param_widgets['text'] = self.widget_factory.create_param_entry(self.param_frame, "text", "备注内容:", "这里是需要备注的文本...")
            self.widget_factory.create_hint_label(self.param_frame, 
                "* 注意: 此步骤仅作为注释，不会执行任何操作。\n"
                "* 可用于标注宏的执行流程，方便理解和定位。")

        elif action_key == 'RUN':
            # 执行命令/脚本/文件 - 根据类型动态显示参数
            run_type_options = {
                'command (命令)': 'command',
                'script (脚本)': 'script',
                'file (写入文件)': 'file'
            }
            self.param_widgets['run_type'] = self.widget_factory.create_param_combobox(self.param_frame, "run_type", "类型:", list(run_type_options.keys()), default='command (命令)')

            # 根据选择的类型，显示对应的参数
            self.param_widgets['command'] = self.widget_factory.create_param_entry(self.param_frame, "command", "命令:", "curl")
            self.param_widgets['args'] = self.widget_factory.create_param_entry(self.param_frame, "args", "参数:", "")
            self.param_widgets['script_path'] = self.widget_factory.create_param_entry(self.param_frame, "script_path", "脚本路径:", "process.py")
            self.param_widgets['interpreter'] = self.widget_factory.create_param_combobox(self.param_frame, "interpreter", "解释器:", ["python", "node", "powershell"], default="python")
            self.param_widgets['file_path'] = self.widget_factory.create_param_entry(self.param_frame, "file_path", "文件路径:", "result.txt")
            self.param_widgets['content'] = self.widget_factory.create_param_entry(self.param_frame, "content", "文件内容:", "Hello World")
            self.param_widgets['timeout'] = self.widget_factory.create_param_entry(self.param_frame, "timeout", "超时(秒):", "30")
            self.param_widgets['cwd'] = self.widget_factory.create_param_entry(self.param_frame, "cwd", "工作目录:", "")
            self.param_widgets['append'] = self.widget_factory.create_param_checkbox(self.param_frame, "append", "[OK] 追加模式 (文件)", default=False)
            self.param_widgets['save_output'] = self.widget_factory.create_param_checkbox(self.param_frame, "save_output", "[OK] 保存输出到剪贴板", default=False)

            # 占位符说明
            self.widget_factory.create_hint_label(self.param_frame,
                "* {CLIPBOARD} = 剪贴板内容, {DATETIME} = 当前时间")

            # 绑定类型切换事件
            if 'run_type' in self.param_widgets:
                self.param_widgets['run_type'].bind("<<ComboboxSelected>>", self.update_run_params)

            # 初始化显示
            self.update_run_params(None)

        elif action_key == 'MOVE_TO':
            self.param_widgets['x'] = self.widget_factory.create_param_entry(self.param_frame, "x", "X 坐标:", "100")
            self.param_widgets['y'] = self.widget_factory.create_param_entry(self.param_frame, "y", "Y 坐标:", "100")
            
            ttk.Separator(self.param_frame, orient='horizontal').pack(fill='x', pady=(15, 5))
            ttk.Label(self.param_frame, text="当前鼠标位置 (参考):", font=self.font_ui, foreground='gray').pack(anchor="w", pady=(5,0))
            ttk.Label(self.param_frame, textvariable=self.mouse_pos_var, font=self.font_code, bootstyle="info").pack(anchor="w")
            # [变更] 启动鼠标追踪
            self.mouse_tracker.start()
            
        elif action_key == 'IF_IMAGE_FOUND':
            self.param_widgets['path'] = self.widget_factory.create_param_entry(self.param_frame, "path", "图像路径:", "button.png")
            self.param_widgets['region'] = self.widget_factory.create_region_selector(self.param_frame, "", self.on_select_region)
            self.param_widgets['confidence'] = self.widget_factory.create_param_entry(self.param_frame, "confidence", "置信度:", "0.8")
            self.widget_factory.create_browse_button(self.param_frame, self.browse_image)
            self.widget_factory.create_test_button(self.param_frame, "🧪 测试 IF 图像", self.on_test_find_image_click)
            
        elif action_key == 'IF_TEXT_FOUND':
            self.param_widgets['text'] = self.widget_factory.create_param_entry(self.param_frame, "text", "查找文本:", "确定")
            self.param_widgets['region'] = self.widget_factory.create_region_selector(self.param_frame, "", self.on_select_region)
            self.param_widgets['lang'] = self.widget_factory.create_param_combobox(self.param_frame, "lang", "语言:", list(MacroSchema.LANG_OPTIONS.keys()))
            self.param_widgets['engine'] = self.widget_factory.create_ocr_engine_combobox(self.param_frame, self.available_ocr_keys)
            
            # === 保存到剪贴板选项（勾选后才展开从属控件）===
            self.param_widgets['save_to_clipboard'] = self.widget_factory.create_param_checkbox(self.param_frame, "save_to_clipboard", "[OK] 保存识别结果到剪贴板", default=False)

            # 始终占位的容器（位于 checkbox 下方，测试按钮上方）
            _sub_ift = ttk.Frame(self.param_frame)
            _sub_ift.pack(fill=tk.X)  # 永不 pack_forget，保持位置

            # 提取模式输入框
            _ep_frame_ift = ttk.Frame(_sub_ift)
            ttk.Label(_ep_frame_ift, text="提取模式 (正则，可选):", font=self.font_ui).pack(anchor="w")
            _ep_entry_ift = ttk.Entry(_ep_frame_ift, width=25, font=self.font_ui)
            _ep_entry_ift.insert(0, r"\d+")
            _ep_entry_ift.pack(anchor="w", fill=tk.X)
            self.param_widgets['extract_pattern'] = _ep_entry_ift

            # 说明提示
            _hint_ift = AutoWrapLabel(_sub_ift,
                text="提取模式: 用正则表达式过滤识别结果，如 \\d+ 只提取数字；留空则保存全部文本。",
                font=self.font_ui, style="secondary.TLabel")

            # 初始隐藏（内部子件不 pack）
            def _toggle_ift(var=self.param_widgets['save_to_clipboard'],
                            ef=_ep_frame_ift, hint=_hint_ift):
                if var.get():
                    ef.pack(fill=tk.X, pady=8)
                    hint.pack(anchor="w", pady=5, fill=tk.X)
                else:
                    ef.pack_forget()
                    hint.pack_forget()
            self.param_widgets['save_to_clipboard'].trace_add('write', lambda *_: _toggle_ift())

            self.widget_factory.create_test_button(self.param_frame, "🧪 测试 IF 文本", self.on_test_find_text_click)
            
        elif action_key == 'LOOP_START':
            # 循环模式选择
            mode_options = {
                '固定次数': 'fixed',
                '直到找到图像': 'until_image',
                '直到找到文本': 'until_text'
            }
            self.param_widgets['mode'] = self.widget_factory.create_param_combobox(self.param_frame, "mode", "循环模式:", list(mode_options.keys()), default='固定次数')
            
            # 根据模式动态显示参数
            # 这里先创建所有可能的控件，后续通过 update_loop_params 动态显示/隐藏
            self.param_widgets['times'] = self.widget_factory.create_param_entry(self.param_frame, "times", "循环次数:", "10")
            self.param_widgets['max_iterations'] = self.widget_factory.create_param_entry(self.param_frame, "max_iterations", "最大迭代次数 (安全阀):", "1000")
            
            # 条件：图像
            self.param_widgets['condition_image'] = self.widget_factory.create_param_entry(self.param_frame, "condition_image", "目标图像路径:", "target.png")
            self.param_widgets['confidence'] = self.widget_factory.create_param_entry(self.param_frame, "confidence", "置信度:", "0.8")
            
            # 条件：文本
            self.param_widgets['condition_text'] = self.widget_factory.create_param_entry(self.param_frame, "condition_text", "目标文本:", "加载完成")
            self.param_widgets['lang'] = self.widget_factory.create_param_combobox(self.param_frame, "lang", "语言:", list(MacroSchema.LANG_OPTIONS.keys()))

            # [新增] 搜索范围（until_image / until_text 共用，缩小截图区域加速检测）
            self.param_widgets['region'] = self.widget_factory.create_region_selector(self.param_frame, "", self.on_select_region)
            
            self.widget_factory.create_hint_label(self.param_frame, 
                "* 提示:"
                "- 固定次数: 传统循环，执行指定次数"
                "- 直到找到图像: 找到图像即停止"
                "- 直到找到文本: 找到文本即停止"
                "- 最大迭代: 防止无限循环的安全机制")
            
            # 绑定模式切换事件
            if 'mode' in self.param_widgets:
                self.param_widgets['mode'].bind("<<ComboboxSelected>>", self.update_loop_params)
            
            # 初始化显示
            self.update_loop_params(None)
        elif action_key == 'ELSE':
            self.widget_factory.create_hint_label(self.param_frame, "* 提示: 'ELSE' 必须与 'IF' 配合使用。它将执行 'IF' 条件不满足时的逻辑。")
        elif action_key == 'END_IF':
            self.widget_factory.create_hint_label(self.param_frame, "* 提示: 'END_IF' 必须与 'IF' 配合使用。它标志着 'IF' 或 'ELSE' 逻辑块的结束。")
        elif action_key == 'END_LOOP':
            self.widget_factory.create_hint_label(self.param_frame, "* 提示: 'END_LOOP' 必须与 'LOOP_START' 配合使用。它标志着循环体的结束。")



    # update_loop_params 和 update_run_params 已迁移到 gui_utils.py

    def update_loop_params(self, event):
        """包装函数：调用 gui_utils 中的 update_loop_params"""
        if self.param_widgets.get('mode') is None:
            return
        update_loop_params(self.param_widgets, self.param_frame, self.param_widgets.get('mode'))

    def update_run_params(self, event):
        """包装函数：调用 gui_utils 中的 update_run_params"""
        if self.param_widgets.get('run_type') is None:
            return
        update_run_params(self.param_widgets, self.param_frame, self.param_widgets.get('run_type'))

    def on_select_region(self, entry_widget):
        self.root.iconify()
        time.sleep(0.3) # 等待最小化动画完成
        
        try:
            # [变更] 使用 gui_utils 中的 RegionSelector
            region = RegionSelector(self.root).get_region()
            self.root.deiconify()
            
            if region:
                val_str = f"{region[0]}, {region[1]}, {region[2]}, {region[3]}"
                entry_widget.delete(0, tk.END)
                entry_widget.insert(0, val_str)
        except Exception as e:
            self.root.deiconify()
            messagebox.showerror("错误", f"选区失败: {e}")

    # create_browse_button, create_test_button, _create_hint_label 已迁移到 gui_utils.py

    def on_test_find_image_click(self):
        try:
            path = self.param_widgets['path'].get()
            conf = float(self.param_widgets['confidence'].get())
            if not os.path.exists(path): raise FileNotFoundError
            
            # <--- 读取搜索范围
            region_box = None
            if 'region' in self.param_widgets:
                val = self.param_widgets['region'].get().strip()
                # [变更] 使用 gui_utils.parse_region_string
                region_box = parse_region_string(val)

            self.status_var.set("测试中...")
            self.root.iconify()
            # 将 region_box 传给线程
            self.root.after(2000, lambda: self._run_test_thread(self._test_find_image, (path, conf, region_box)))
        except: messagebox.showerror("错误", "参数无效")

    def on_test_find_text_click(self):
        try:
            text = self.param_widgets['text'].get()
            lang = MacroSchema.LANG_OPTIONS.get(self.param_widgets['lang'].get(), 'eng')
            
            # 获取下拉框的原始值
            engine_name = self.param_widgets['engine'].get()
            
            # <--- 优先检查是否不可用
            if engine_name.endswith(" (不可用)"):
                messagebox.showwarning(
                    "引擎不可用", 
                    f"您选择的引擎 '{engine_name}' 在当前环境中未安装或无法加载。\n\n请选择其他引擎，或安装相应组件后重启程序。",
                    parent=self.root
                )
                return # 直接阻断测试，不再往下执行
            
            engine = self.FULL_OCR_KEY_MAP.get(engine_name, 'auto')
            
            region_box = None
            if 'region' in self.param_widgets:
                val = self.param_widgets['region'].get().strip()
                # [变更] 使用 gui_utils.parse_region_string
                region_box = parse_region_string(val)
            
            if not text: raise ValueError
            self.status_var.set("测试中...")
            self.root.iconify()
            # 将 region_box 传给线程
            self.root.after(2000, lambda: self._run_test_thread(self._test_find_text, (text, lang, engine, region_box)))
        except: messagebox.showerror("错误", "参数无效")

    def on_test_ai_command_click(self):
        """测试 AI 自然语言指令"""
        try:
            instruction = self.param_widgets['instruction'].get()
            if not instruction.strip():
                messagebox.showwarning("输入错误", "请输入 AI 指令")
                return
            
            # 读取区域
            region_box = None
            if 'region' in self.param_widgets:
                val = self.param_widgets['region'].get().strip()
                region_box = parse_region_string(val)
            
            self.status_var.set("AI 分析中...")
            self.root.iconify()
            self.root.after(2000, lambda: self._run_test_thread(self._test_ai_command, (instruction, region_box)))
        except: messagebox.showerror("错误", "参数无效")

    def _test_ai_command(self, instruction, region_box=None):
        """后台执行 AI 指令测试"""
        try:
            coords = vlm_engine.find_location_by_vlm(instruction, region=region_box)
            self.root.after(0, lambda: self._on_test_ai_complete(coords))
        except Exception as e:
            self.root.after(0, lambda err=e: self._on_test_error(err))

    def _on_test_ai_complete(self, coords):
        """AI 测试完成回调"""
        self.root.deiconify()
        self.root.attributes('-topmost', True)
        if coords and len(coords) >= 2:
            self.last_test_location = (coords[0], coords[1])
            pyautogui.moveTo(coords[0], coords[1])
            messagebox.showinfo("AI 成功", f"找到坐标: {self.last_test_location}\n\nAI 已移动鼠标到该位置")
        else:
            messagebox.showwarning("AI 失败", "未能从 AI 获取有效坐标\n\n请检查:\n1. API Key 是否正确配置\n2. 网络是否正常\n3. 指令是否清晰")
        self.update_status_bar_hotkeys()
        self.root.attributes('-topmost', False)

    def _run_test_thread(self, func, args):
        threading.Thread(target=func, args=args, daemon=True).start()

    def _test_find_image(self, path, conf, region_box=None):
        try:
            # <--- 根据区域截图
            if region_box:
                screenshot = ImageGrab.grab(bbox=tuple(region_box))
                offset = (region_box[0], region_box[1])
            else:
                screenshot = ImageGrab.grab()
                offset = (0, 0)
                
            res_val = macro_engine.find_image_cv2(path, conf, screenshot_pil=screenshot, offset=offset)
            loc = res_val[0] if res_val else None
            self.root.after(0, lambda: self._on_test_complete(loc))
        except Exception as e: 
            self.root.after(0, lambda err=e: self._on_test_error(err))

    def _test_find_text(self, text, lang, engine, region_box=None):
        try:
            # <--- 根据区域截图
            if region_box:
                screenshot = ImageGrab.grab(bbox=tuple(region_box))
                offset = (region_box[0], region_box[1])
            else:
                screenshot = ImageGrab.grab()
                offset = (0, 0)
            
            loc = ocr_engine.find_text_location(text, lang, True, screenshot_pil=screenshot, offset=offset, engine=engine)
            self.root.after(0, lambda: self._on_test_complete(loc))
        except Exception as e: 
            self.root.after(0, lambda err=e: self._on_test_error(err))

    def _on_test_complete(self, loc):
        self.root.deiconify()
        self.root.attributes('-topmost', True)
        
        x, y = None, None
        
        if loc and len(loc) >= 2:
            # 兼容两种格式:
            # 1. (x, y) - 图像查找返回
            # 2. ((x, y), text) - OCR返回
            first = loc[0]
            if isinstance(first, tuple) and len(first) >= 2:
                # 格式2: ((x, y), text)
                x, y = first[0], first[1]
            else:
                # 格式1: (x, y)
                x, y = loc[0], loc[1]
        
        if x is not None and y is not None:
            self.last_test_location = (x, y)
            pyautogui.moveTo(x, y)
            messagebox.showinfo("成功", f"找到于 {self.last_test_location}")
        else:
            messagebox.showwarning("失败", "未找到目标")
        
        self.update_status_bar_hotkeys()
        self.root.attributes('-topmost', False)

    def _on_test_error(self, e):
        self.root.deiconify()
        messagebox.showerror("错误", str(e))
        self.update_status_bar_hotkeys()

    def browse_image(self):
        """浏览图片文件（保持向后兼容）"""
        f = filedialog.askopenfilename(filetypes=[("PNG", "*.png"), ("All", "*.*")])
        if f: 
            f = os.path.abspath(f) 
            self.param_widgets['path'].delete(0, tk.END)
            self.param_widgets['path'].insert(0, f)

    def add_or_update_step(self):
        """添加或更新步骤 (已优化：支持插入到选中行下方)"""
        action = MacroSchema.ACTION_KEYS_TO_NAME.get(self.action_type.get())
        if not action: return
        params = {}
        try:
            for k, w in self.param_widgets.items():
                # === 新增：处理 BooleanVar (复选框) ===
                if isinstance(w, tk.BooleanVar):
                    val = w.get()
                    params[k] = val
                    continue
                
                val = w.get()
                
                # 数字校验
                if k in ['x', 'y', 'ms', 'times', 'x_offset', 'y_offset', 'amount', 'max_iterations']:
                    if val and not val.strip().lstrip('-').isdigit():
                        messagebox.showwarning("输入错误", f"参数 '{k}' 必须是整数")
                        return
                
                if action == 'SCROLL' and k in ['x', 'y'] and not val:
                    continue
                
                if not val:
                    if k == 'region': pass # region 允许为空
                    elif k == 'extract_pattern': pass # 正则允许为空
                    elif action in ['ELSE', 'END_IF', 'END_LOOP', 'NOTE']: continue
                    elif action == 'SCROLL' and k in ['x', 'y']: continue
                    elif action == 'RUN': continue  # RUN 步骤允许空值
                    else: return
                
                # 参数转换
                elif k == 'mode':
                    mode_map = {
                        '固定次数': 'fixed',
                        '直到找到图像': 'until_image',
                        '直到找到文本': 'until_text'
                    }
                    params[k] = mode_map.get(val, 'fixed')
                
                # RUN 类型的转换
                elif k == 'run_type':
                    run_type_map = {
                        'command (命令)': 'command',
                        'script (脚本)': 'script',
                        'file (写入文件)': 'file'
                    }
                    params[k] = run_type_map.get(val, 'command')
                
                elif k == 'interpreter':
                    # interpreter 已经是内部值，无需转换
                    params[k] = val
                
                # [重构] 使用统一的参数映射函数
                elif k in ('lang', 'button', 'engine'):
                    params[k] = self._param_display_to_internal(k, val)
                
                # [变更] 使用通用函数解析 region
                elif k == 'region':
                    if val.strip():
                        coords = parse_region_string(val)
                        if coords: params['cache_box'] = coords
                    continue
                
                # === 新增：处理 extract_pattern，为空时不保存 ===
                elif k == 'extract_pattern':
                    if val and val.strip():
                        params[k] = val.strip()
                    continue

                else:
                    params[k] = val
        except Exception as e: 
            print(f"参数解析错误: {e}")
            return
        
        # ============================================================
        # [新增] 优化 RUN 步骤：只保存需要的参数
        # ============================================================
        if action == 'RUN':
            run_type = params.get('run_type', 'command')
            
            # 根据类型确定需要保存的参数
            if run_type == 'command':
                # 只保留 command 和 args
                params = {k: params[k] for k in ('run_type', 'command', 'args') if k in params and params[k]}
            elif run_type == 'script':
                # 只保留 script_path 和 interpreter
                params = {k: params[k] for k in ('run_type', 'script_path', 'interpreter') if k in params and params[k]}
            elif run_type == 'file':
                # file 类型需要保留：run_type, file_path, content, append
                params = {k: params[k] for k in ('run_type', 'file_path', 'content', 'append') if k in params and params[k]}
            
            # 通用参数：只在非默认值时保存
            if params.get('timeout'):
                if params.get('timeout') != '30':
                    pass  # 保留
                else:
                    params.pop('timeout', None)
            
            if params.get('cwd'):
                if params.get('cwd'):
                    pass  # 保留
                else:
                    params.pop('cwd', None)
            
            if params.get('save_output'):
                if params.get('save_output'):
                    pass  # 保留
                else:
                    params.pop('save_output', None)
        
        # [补丁优化] 验证图片文件的有效性
        if action in ('FIND_IMAGE', 'IF_IMAGE_FOUND'):
            img_path = params.get('path', '')
            if img_path:
                if not os.path.exists(img_path):
                    messagebox.showwarning(
                        "文件不存在", 
                        f"图片文件不存在:\n{img_path}\n\n请确认文件路径是否正确。",
                        parent=self.root
                    )
                    return
                if not img_path.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.gif')):
                    messagebox.showwarning(
                        "文件格式错误",
                        f"仅支持常见图片格式 (PNG, JPG, BMP, GIF)\n\n当前文件: {os.path.basename(img_path)}",
                        parent=self.root
                    )
                    return
        
        # [补丁优化] 验证循环条件图片
        if action == 'LOOP_START':
            mode = params.get('mode', 'fixed')
            if mode == 'until_image':
                img_path = params.get('condition_image', '')
                if img_path:
                    if not os.path.exists(img_path):
                        messagebox.showwarning(
                            "文件不存在",
                            f"循环条件图片不存在:\n{img_path}\n\n请确认文件路径是否正确。",
                            parent=self.root
                        )
                        return
                    if not img_path.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.gif')):
                        messagebox.showwarning(
                            "文件格式错误",
                            f"仅支持常见图片格式\n\n当前文件: {os.path.basename(img_path)}",
                            parent=self.root
                        )
                        return
        
        step = {"action": action, "params": params}
        
        # 仅在没有手动指定区域时，才询问是否使用测试结果作为缓存
        if action in ('FIND_TEXT', 'FIND_IMAGE', 'IF_TEXT_FOUND', 'IF_IMAGE_FOUND') \
           and not self.editing_index \
           and self.last_test_location \
           and 'cache_box' not in step['params']:
            if messagebox.askyesno("缓存", "使用测试坐标作为缓存？"):
                step["params"]["cache_box"] = [self.last_test_location[0], self.last_test_location[1], self.last_test_location[0]+1, self.last_test_location[1]+1]

        # ============================================================
        # [核心修改] 插入逻辑优化
        # ============================================================
        target_index = -1 # 记录新位置用于滚动
        
        if self.editing_index is not None:
            # 修改模式：原地更新
            self.steps[self.editing_index] = step
            target_index = self.editing_index
            self.cancel_edit_mode()
        else:
            # 新增模式：检查当前是否有选中行
            selected_idx = self._get_selected_index()
            
            if selected_idx is not None:
                # 有选中：插入到选中行的下一行
                target_index = selected_idx + 1
                self.steps.insert(target_index, step)
            else:
                # 无选中：追加到末尾
                self.steps.append(step)
                target_index = len(self.steps) - 1
                
            self.update_listbox_display()
        
        # ============================================================
        # [UI优化] 自动滚动并选中新添加/修改的行
        # ============================================================
        children = self.steps_tree.get_children()
        if 0 <= target_index < len(children):
            item_id = children[target_index]
            self.steps_tree.see(item_id)           # 滚动到可见
            self.steps_tree.selection_set(item_id) # 自动选中
            
        self.last_test_location = None

    def load_step_for_edit(self):
        """加载选中步骤到编辑区 (修复：循环模式回显问题)"""
        idx = self._get_selected_index()
        if idx is None: return
        
        step = self.steps[idx]
        
        # 1. 设置动作类型 (这将重置右侧面板为默认状态)
        self.action_type.set(MacroSchema.ACTION_TRANSLATIONS.get(step['action']))
        self.update_param_fields(None)
        
        # ============================================================
        # [关键修复] 优先强制处理 LOOP_START 的模式
        # ============================================================
        if step['action'] == 'LOOP_START':
            # 获取保存的模式 (默认 fixed)
            saved_mode = step['params'].get('mode', 'fixed')
            
            # 翻译模式为中文
            mode_map_rev = {
                'fixed': '固定次数',
                'until_image': '直到找到图像',
                'until_text': '直到找到文本'
            }
            display_mode = mode_map_rev.get(saved_mode, '固定次数')
            
            # 1. 强行修改下拉框的值
            if 'mode' in self.param_widgets:
                self.param_widgets['mode'].set(display_mode)
            
            # 2. 强行触发界面刷新 (这一步会让"目标文本"输入框从隐藏变为显示)
            # 必须在填入"沙发"等文字之前完成这一步！
            self.update_loop_params(None)

        # ============================================================
        # [新增] 优先强制处理 RUN 的类型
        # ============================================================
        elif step['action'] == 'RUN':
            # 获取保存的类型 (默认 command)
            saved_run_type = step['params'].get('run_type', 'command')
            
            # 翻译类型为中文
            run_type_map_rev = {
                'command': 'command (命令)',
                'script': 'script (脚本)',
                'file': 'file (写入文件)'
            }
            display_run_type = run_type_map_rev.get(saved_run_type, 'command (命令)')
            
            # 1. 强行修改下拉框的值
            if 'run_type' in self.param_widgets:
                self.param_widgets['run_type'].set(display_run_type)
            
            # 2. 强行触发界面刷新 (这一步会让对应类型的输入框显示出来)
            self.update_run_params(None)

        # ============================================================
        # 常规参数填充 (此时输入框已经显示出来了，可以安全填值了)
        # ============================================================
        
        # 预处理 Region 显示
        if 'cache_box' in step['params'] and 'region' in self.param_widgets:
            cb = step['params']['cache_box']
            if isinstance(cb, list) and len(cb) == 4:
                self.param_widgets['region'].delete(0, tk.END)
                self.param_widgets['region'].insert(0, f"{cb[0]}, {cb[1]}, {cb[2]}, {cb[3]}")
        
        # 遍历并填充所有参数
        for k, v in step['params'].items():
            # 跳过 mode, run_type (前面处理了) 和 cache_box (前面处理了)
            if k in ('mode', 'run_type', 'cache_box', 'region'): continue
            
            if k in self.param_widgets:
                w = self.param_widgets[k]
                
                # [重构] 使用统一的参数映射函数
                if k in ('lang', 'button', 'engine'):
                    display_val = self._param_internal_to_display(k, v)
                else:
                    display_val = v
                
                # 赋值
                if isinstance(w, tk.BooleanVar): w.set(bool(v))
                elif isinstance(w, ttk.Combobox): w.set(display_val)
                else: 
                    w.delete(0, tk.END)
                    w.insert(0, str(display_val))

        # 更新编辑状态
        self.editing_index = idx
        self.add_step_btn.config(text="[OK] 更新步骤", bootstyle="warning")
        self.add_step_btn.grid_configure(columnspan=1)
        self.cancel_edit_btn.grid(row=0, column=1, sticky="nsew", padx=(2,0))
        self.update_listbox_display()

    def cancel_edit_mode(self):
        self.editing_index = None
        self.add_step_btn.config(text="＋ 添加到序列 >>", bootstyle="success")
        self.cancel_edit_btn.grid_remove()
        self.add_step_btn.grid_configure(columnspan=2)
        self.update_listbox_display()

    def update_listbox_display(self):
        """更新 Treeview 显示"""
        for item in self.steps_tree.get_children():
            self.steps_tree.delete(item)
            
        block_stack = []
        for i, step in enumerate(self.steps):
            act = step['action']
            
            # 缩进逻辑
            current_indent_level = max(0, len(block_stack) - (1 if act in ['ELSE', 'END_IF', 'END_LOOP'] else 0))
            indent_str = "    " * current_indent_level
            
            # 参数预览文本
            display_params = step['params'].copy()
            
            cache_str = ""
            if 'cache_box' in display_params:
                box = display_params.pop('cache_box')
                cache_str = f"[区域: {box[0]},{box[1]},{box[2]},{box[3]}] "

            if 'engine' in display_params:
                # <--- 列表显示时也使用完整映射
                display_params['engine'] = self.FULL_OCR_NAME_MAP.get(display_params['engine'], display_params['engine'])
                
            # 格式化参数列字符串
            param_text = f"{cache_str}{display_params}" if display_params else ""
            
            action_label = MacroSchema.ACTION_TRANSLATIONS.get(act, act)
            
            # 备注动作特殊处理：显示为注释格式
            if act == 'NOTE':
                note_text = step['params'].get('text', '')
                param_text = f"// {note_text}" if note_text else "// (空备注)"
            
            # 插入行 (Values对应: id, action, params)
            is_enabled = step.get('enabled', True)
            display_action = f"{indent_str}{action_label}"
            if not is_enabled:
                display_action = f"{indent_str}[屏蔽] {action_label}"
                
            item_id = self.steps_tree.insert("", "end", values=(
                i + 1,
                display_action,
                param_text
            ))
            
            tags = []
            if i == self.editing_index:
                tags.append('editing')
            if not is_enabled:
                tags.append('disabled')
                
            if tags:
                self.steps_tree.item(item_id, tags=tuple(tags))
                
            if i == self.editing_index:
                # 确保滚动可见
                self.steps_tree.see(item_id)
                # 保持选中状态 (可选)
                self.steps_tree.selection_set(item_id)

            if act.startswith('IF_') or act == 'LOOP_START':
                block_stack.append(act)
            elif act in ['END_IF', 'END_LOOP'] and block_stack:
                block_stack.pop()

    def show_tree_menu(self, event):
        """显示树形列表右键菜单"""
        item = self.steps_tree.identify_row(event.y)
        if item:
            self.steps_tree.selection_set(item)
            idx = self._get_selected_index()
            if idx is not None:
                act = self.steps[idx].get('action', '')
                if act in ['IF_IMAGE_FOUND', 'IF_TEXT_FOUND', 'ELSE', 'END_IF', 'LOOP_START', 'END_LOOP']:
                    self.tree_menu.entryconfig("屏蔽/启用选中步骤", state="disabled")
                else:
                    self.tree_menu.entryconfig("屏蔽/启用选中步骤", state="normal")
            self.tree_menu.post(event.x_root, event.y_root)

    def toggle_step_enabled(self):
        """切换选中步骤的启用/屏蔽状态"""
        idx = self._get_selected_index()
        if idx is not None:
            step = self.steps[idx]
            act = step.get('action', '')
            if act in ['IF_IMAGE_FOUND', 'IF_TEXT_FOUND', 'ELSE', 'END_IF', 'LOOP_START', 'END_LOOP']:
                messagebox.showwarning("提示", "不可屏蔽流程控制节点（条件、循环），以防止引发严重 BUG。", parent=self.root)
                return
            
            step['enabled'] = not step.get('enabled', True)
            self.update_listbox_display()

    def remove_step(self):
        # --- 升级: 适配 Treeview ---
        idx = self._get_selected_index()
        if idx is None: return
        
        # [修复] 使用 elif 确保逻辑互斥
        # 原代码问题: cancel_edit_mode 会将 editing_index 设为 None，
        # 导致后续的 if 判断永远为 False，索引调整失效
        if self.editing_index == idx:
            self.cancel_edit_mode()
        elif self.editing_index is not None and self.editing_index > idx:
            self.editing_index -= 1
            
        del self.steps[idx]
        self.update_listbox_display()
        
        # 尝试选中下一行
        children = self.steps_tree.get_children()
        if idx < len(children):
             self.steps_tree.selection_set(children[idx])
        elif children:
             self.steps_tree.selection_set(children[-1])

    def move_step(self, d):
        # --- 升级: 适配 Treeview ---
        idx = self._get_selected_index()
        if idx is None: return
        
        i = idx
        new_i = i - 1 if d == "up" else i + 1
        
        if 0 <= new_i < len(self.steps):
            self.steps.insert(new_i, self.steps.pop(i))
            
            # 同步更新 editing_index
            if self.editing_index == i: self.editing_index = new_i
            elif self.editing_index == new_i: self.editing_index = i
            self.update_listbox_display()
            
            # 保持选中移动后的项
            children = self.steps_tree.get_children()
            if 0 <= new_i < len(children):
                self.steps_tree.selection_set(children[new_i])
                self.steps_tree.see(children[new_i])

    def start_hotkey_listener(self):
        """切换回 Listener 模式"""
        if self.hotkey_listener:
            try:
                self.hotkey_listener.stop()
            except:
                pass
        threading.Thread(target=self._hotkey_listener_thread, daemon=True).start()

    def _hotkey_listener_thread(self):
        """快捷键监听线程"""
        try:
            self.hotkey_listener = keyboard.Listener(
                on_press=self.on_hotkey_press, 
                on_release=self.on_hotkey_release
            )
            self.hotkey_listener.start()
            self.hotkey_listener.join()
        except Exception as e: 
            msg = f"热键监听器启动失败: {e}\n\n快捷键将无法工作。请尝试重启程序。"
            self.root.after(0, messagebox.showerror, "严重错误", msg)

    def _get_key_name_from_key(self, key):
        """辅助函数：优先使用 vk 获取按键名称"""
        try:
            if hasattr(key, 'vk') and key.vk in VK_TO_PYNPUT:
                return VK_TO_PYNPUT[key.vk]
            if hasattr(key, 'name') and key.name:
                return key.name.lower()
            if hasattr(key, 'char') and key.char:
                return key.char.lower()
            return str(key).lower()
        except:
            return None

    def on_hotkey_press(self, key):
        """ 按键按下事件"""
        try:
            key_name = self._get_key_name_from_key(key)
            if not key_name: return
                
            if key_name in ['ctrl_l', 'ctrl_r']: key_name = 'ctrl'
            elif key_name in ['alt_l', 'alt_r', 'alt_gr']: key_name = 'alt'
            elif key_name in ['shift_l', 'shift_r']: key_name = 'shift'
            elif key_name in ['cmd_l', 'cmd_r', 'cmd']: key_name = 'cmd'
            
            if key_name not in self.held_keys:
                self.held_keys.add(key_name)
                
                run_mods, run_key = self._parse_hotkey(self.hotkey_run_str.get())
                if key_name == run_key and run_mods.issubset(self.held_keys):
                    self.root.after(0, self.safe_run_macro)
                
                stop_mods, stop_key = self._parse_hotkey(self.hotkey_stop_str.get())
                if key_name == stop_key and stop_mods.issubset(self.held_keys):
                    self.root.after(0, self.safe_stop_macro)
        except (AttributeError, KeyError) as e:
            print(f"[Hotkey] 按键解析错误: {e}")
        except Exception as e:
            print(f"[Hotkey] 未知错误 (press): {e}")

    def on_hotkey_release(self, key):
        """按键释放事件"""
        try:
            key_name = self._get_key_name_from_key(key)
            if not key_name: return
                
            if key_name in ['ctrl_l', 'ctrl_r']: key_name = 'ctrl'
            elif key_name in ['alt_l', 'alt_r', 'alt_gr']: key_name = 'alt'
            elif key_name in ['shift_l', 'shift_r']: key_name = 'shift'
            elif key_name in ['cmd_l', 'cmd_r', 'cmd']: key_name = 'cmd'
            
            if key_name in self.held_keys:
                self.held_keys.remove(key_name)
        except (AttributeError, KeyError) as e:
            print(f"[Hotkey] 按键解析错误: {e}")
        except Exception as e:
            print(f"[Hotkey] 未知错误 (release): {e}")

    @functools.lru_cache(maxsize=16)
    def _parse_hotkey(self, hotkey_str):
        """ 解析快捷键字符串（小写），返回 (modifiers, key)"""
        parts = [p.strip() for p in hotkey_str.lower().split('+')]
        key = parts[-1]
        modifiers = set(parts[:-1])
        return modifiers, key

    def restart_hotkey_listener(self):
        """停止并重新启动监听器"""
        if self.hotkey_listener:
            self.hotkey_listener.stop()
        self.start_hotkey_listener()

    def safe_run_macro(self):
        if not self.is_macro_running and self.editing_index is None:
            self.root.after(0, self.run_macro, True)
        
    def safe_stop_macro(self):
        """[即时中断] 通过 ctypes 向执行线程注入异常，无论其封锁在哪个阶段都能立刻中断。"""
        if not self.is_macro_running:
            return
        self.root.after(0, self.status_var.set, "正在停止...")
        # 同时设置标志位（兼容 WAIT 内的分段检查）
        if self.current_run_context:
            self.current_run_context['stop_requested'] = True
        # 强制向执行线程注入 MacroStopException
        t = self._macro_thread
        if t and t.is_alive():
            tid = t.ident
            if tid:
                import ctypes
                res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
                    ctypes.c_ulong(tid),
                    ctypes.py_object(macro_engine.MacroStopException)
                )
                if res == 0:
                    print("[中断] 警告: 线程 ID 无效，异常未注入")
                elif res > 1:
                    # 多个线程被影响，需要撤销
                    ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(tid), None)
                    print("[中断] 警告: 异常影响了多个线程，已撤销")
                else:
                    print("[中断] MacroStopException 已弹射到执行线程")
        
    def run_macro(self, hotkey=False):
        if self.is_macro_running or not self.steps: return
        stop_display = capitalize_hotkey_str(self.hotkey_stop_str.get())
        
        if not hotkey and not self.skip_confirm_var.get():
            if not messagebox.askyesno("运行", f"是否立即开始？(按 {stop_display} 停止)"): return
            
        self.loop_status_var.set("") 
        
        # [核心修复] 暴力清空之前的状态队列，防止积压
        while not self.status_queue.empty():
            try: self.status_queue.get_nowait()
            except queue.Empty: break
            
        self.run_btn.config(state="disabled")
        self.status_var.set(f"宏正在运行... [{stop_display}] 停止")
        
        # [新增] 创建迷你状态栏窗口（在最小化前）
        if not self.dont_minimize_var.get():
            self.mini_status_window = MiniStatusWindow(self.root, self.safe_stop_macro)
            self.mini_status_window.update_status(
                f"宏正在运行... [点击停止 或 {stop_display}]",
                ""
            )
            self.root.iconify()
        else:
            self.root.attributes('-topmost', True) 
        self.root.after(1500, self._start_macro_thread)

    def _start_macro_thread(self):
        self.is_macro_running = True
        self.current_run_context = {
            'stop_requested': False,
            'stop_key_str': self.hotkey_stop_str.get(),
            'enhanced_mode': self.enhanced_mode_var.get()
        }
        self._macro_thread = threading.Thread(target=self._run, args=(self.steps.copy(),), daemon=True)
        self._macro_thread.start()
        
    def _run(self, steps):
        try:
            macro_engine.execute_steps(steps, run_context=self.current_run_context, status_callback=self.update_loop_status)
        except macro_engine.MacroStopException:
            print("[宏] 已将循环强制中断")
        except Exception as e:
            self.root.after(0, lambda err=e: messagebox.showerror("错误", str(err)))
        finally:
            self.root.after(0, self._on_macro_complete)

    def _on_macro_complete(self):
        self.is_macro_running = False
        self.current_run_context = None
        
        # [新增] 销毁迷你状态栏窗口
        if self.mini_status_window:
            self.mini_status_window.destroy()
            self.mini_status_window = None
        
        self.root.deiconify()
        self.root.attributes('-topmost', False)
        self.run_btn.config(state="normal")
        self.update_status_bar_hotkeys() 

    def update_loop_status(self, text):
        self.status_queue.put(text)

    def _check_status_queue(self):
        """
        [补丁优化] 动态调整状态队列检查频率
        
        优化:
        - 运行时: 50ms (快速响应)
        - 空闲时: 500ms (节省CPU)
        """
        if not self.is_app_running: return
        
        # [补丁优化] 根据运行状态动态调整检查频率
        interval = STATUS_QUEUE_CHECK_INTERVAL_RUNNING if self.is_macro_running else STATUS_QUEUE_CHECK_INTERVAL_IDLE
        
        try:
            text = None
            count = 0
            while not self.status_queue.empty() and count < STATUS_QUEUE_MAX_BATCH:
                text = self.status_queue.get_nowait()
                count += 1
            
            if text:
                self.loop_status_var.set(text)
            
            # [新增] 同步更新迷你窗口（即使没有新状态，也要显示当前状态）
            if self.mini_status_window:
                stop_display = capitalize_hotkey_str(self.hotkey_stop_str.get())
                # 获取当前循环状态（可能为空或有值）
                current_loop_status = self.loop_status_var.get()
                self.mini_status_window.update_status(
                    f"宏正在运行... [点击停止 或 {stop_display}]",
                    current_loop_status  # 显示当前循环状态
                )
        except queue.Empty:
            pass
        except Exception as e:
            print(f"[StatusQueue] 错误: {e}")
            import traceback
            traceback.print_exc()  # [补丁优化] 记录完整堆栈
            
        self.root.after(interval, self._check_status_queue)

    def new_macro(self):
        if self.steps:
            if not messagebox.askyesno("新建", "清空当前宏？"): return
        self.steps = []
        self.editing_index = None
        self.last_test_location = None
        self.cancel_edit_mode()
        self.update_listbox_display()
        self.status_var.set("已新建空白宏。")

    def load_macro(self):
        f = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if f: self._load_file(f)

    def save_macro(self):
        f = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if f:
            try:
                # 将所有 numpy 类型转换为 Python 原生类型
                def convert_to_native(obj):
                    """递归转换所有值为 Python 原生类型"""
                    import numpy as np
                    if isinstance(obj, dict):
                        return {k: convert_to_native(v) for k, v in obj.items()}
                    elif isinstance(obj, list):
                        return [convert_to_native(item) for item in obj]
                    elif isinstance(obj, (np.integer, np.floating)):
                        return obj.item()  # numpy int/float -> Python int/float
                    else:
                        return obj
                
                native_steps = convert_to_native(self.steps)
                
                # 自定义格式：每行一个步骤对象，便于阅读
                with open(f, 'w', encoding='utf-8') as file:
                    file.write('[\n')
                    for i, step in enumerate(native_steps):
                        # 去掉默认缩进，使用紧凑格式
                        step_str = json.dumps(step, ensure_ascii=False)
                        if i < len(native_steps) - 1:
                            file.write(f'    {step_str},\n')
                        else:
                            file.write(f'    {step_str}\n')
                    file.write(']\n')
                
                messagebox.showinfo("成功", "宏已保存！")
                self.add_to_recent_files(f)
            except Exception as e: messagebox.showerror("失败", str(e))

    def _load_file(self, f):
        if not os.path.exists(f):
            messagebox.showerror("失败", "文件不存在")
            if f in self.recent_files: self.recent_files.remove(f); self.save_app_settings(); self.update_recent_files_menu()
            return
        try:
            self.cancel_edit_mode()
            with open(f, 'r', encoding='utf-8') as file: 
                data = json.load(file)
            
            # 验证JSON数据结构（已迁移到 core_engine.py）
            if not validate_macro_data(data):
                messagebox.showerror(
                    "加载失败", 
                    f"文件格式无效或损坏:\n{os.path.basename(f)}\n\n"
                    "可能原因:\n"
                    "• 不是有效的宏文件\n"
                    "• 文件被手动编辑导致格式错误\n"
                    "• 文件损坏"
                )
                return
            
            self.steps = data
            self.update_listbox_display()
            self.status_var.set(f"已加载: {os.path.basename(f)}")
            self.add_to_recent_files(f)
        except json.JSONDecodeError as e:
            messagebox.showerror(
                "JSON解析错误", 
                f"文件不是有效的JSON格式:\n{os.path.basename(f)}\n\n"
                f"错误详情: {str(e)}\n\n"
                "请检查文件是否被意外修改。"
            )
        except Exception as e: 
            messagebox.showerror("加载失败", f"无法加载文件:\n{str(e)}")
    
    # _validate_macro_data 已迁移到 core_engine.py

    def add_to_recent_files(self, f):
        f = os.path.abspath(f)
        if f in self.recent_files: self.recent_files.remove(f)
        self.recent_files.insert(0, f)
        self.recent_files = self.recent_files[:MAX_RECENT_FILES]
        self.update_recent_files_menu()
        self.save_app_settings()

    def update_recent_files_menu(self):
        self.recent_files_menu.delete(0, tk.END)
        for i, f in enumerate(self.recent_files):
            self.recent_files_menu.add_command(label=f"{i+1}. {os.path.basename(f)}", command=lambda p=f: self._load_file(p))

    def load_app_settings(self):
        """加载应用设置"""
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    d = json.load(f)
                    self.recent_files = d.get('recent_files', [])
                    self.current_theme.set(d.get('theme', 'litera'))
                    self.hotkey_run_str.set(d.get('hotkey_run', DEFAULT_HOTKEY_RUN))
                    self.hotkey_stop_str.set(d.get('hotkey_stop', DEFAULT_HOTKEY_STOP))
                    self.enhanced_mode_var.set(d.get('enhanced_mode', True))
        except:
            pass
        self.root.style.theme_use(self.current_theme.get())

    def save_app_settings(self):
        """保存应用设置"""
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump({
                    'recent_files': self.recent_files,
                    'theme': self.current_theme.get(),
                    'hotkey_run': self.hotkey_run_str.get(),
                    'hotkey_stop': self.hotkey_stop_str.get(),
                    'enhanced_mode': self.enhanced_mode_var.get()
                }, f, indent=2)
        except:
            pass

    def change_theme(self):
        self.root.style.theme_use(self.current_theme.get())
        self.root.style.configure(".", font=self.font_ui)
        self.save_app_settings()
        
    def check_hotkey_conflicts(self, show_success=True):
        if not HOTKEY_CHECK_AVAILABLE:
            print("[警告] 跳过快捷键冲突检测 (pywin32 未安装或非 Windows 系统)")
            return True 

        conflicts = []
        
        if not self._test_register_hotkey(self.hotkey_run_str.get(), 1):
            conflicts.append(f"运行快捷键 '{capitalize_hotkey_str(self.hotkey_run_str.get())}'")
        
        if not self._test_register_hotkey(self.hotkey_stop_str.get(), 2):
            conflicts.append(f"停止快捷键 '{capitalize_hotkey_str(self.hotkey_stop_str.get())}'")
            
        if conflicts:
            msg = "检测到快捷键冲突：\n\n" + "\n".join(conflicts) + "\n\n可能已被其他程序 (如 NVIDIA, QQ, 微信) 占用。\n请在设置中修改快捷键，否则热键可能无法工作。"
            self.root.after(0, messagebox.showwarning, "快捷键冲突", msg)
            return False
        elif show_success:
            pass 
        return True

    def _parse_hotkey_string_to_win32(self, hotkey_str):
        parts = hotkey_str.lower().split('+')
        modifiers = 0
        vk_key = None
        
        for part in parts:
            part = part.strip()
            if part in PYNPUT_MOD_TO_WIN_MOD:
                modifiers |= PYNPUT_MOD_TO_WIN_MOD[part]
            elif part in PYNPUT_TO_VK:
                vk_key = PYNPUT_TO_VK[part]
                
        return modifiers, vk_key

    def _test_register_hotkey(self, hotkey_str, hotkey_id):
        if not hotkey_str: return True
        try:
            modifiers, vk = self._parse_hotkey_string_to_win32(hotkey_str)
            if vk is None:
                print(f"无法解析快捷键进行冲突检测: {hotkey_str}")
                return True 
                
            hwnd = None 
            if ctypes.windll.user32.RegisterHotKey(hwnd, hotkey_id, modifiers, vk) == 0:
                return False
            else:
                ctypes.windll.user32.UnregisterHotKey(hwnd, hotkey_id)
                return True
        except Exception as e:
            print(f"快捷键检测时发生错误: {e}")
            return True


if __name__ == "__main__":
    import argparse
    
    # 命令行参数解析
    parser = argparse.ArgumentParser(description='MacroAssistant - 自动化宏工具')
    parser.add_argument('script_file', nargs='?', help='要执行的脚本文件 (.json)')
    parser.add_argument('--run', dest='run', help='执行指定脚本文件 (效果同直接传参)')
    parser.add_argument('--theme', dest='theme', default='litera', help='指定主题')
    args = parser.parse_args()
    
    # 确定要执行的脚本
    script_file = args.script_file or args.run
    
    if script_file:
        # 命令行模式：执行脚本
        if not os.path.exists(script_file):
            print(f"错误: 找不到脚本文件 {script_file}")
            sys.exit(1)
        
        print(f"[命令行] 准备执行脚本: {script_file}")
        
        try:
            # 加载脚本
            print(f"[命令行] 正在加载脚本...")
            with open(script_file, 'r', encoding='utf-8') as f:
                script_data = json.load(f)
            
            # 支持两种格式:
            # 1. {"steps": [...]} - GUI 导出的格式
            # 2. [...] - 直接是步骤列表
            if isinstance(script_data, list):
                steps = script_data
            else:
                steps = script_data.get('steps', [])
            
            if not steps:
                print("错误: 脚本中没有步骤")
                sys.exit(1)
            
            # 执行脚本
            print(f"[命令行] 共 {len(steps)} 个步骤，开始执行...")
            result = macro_engine.execute_steps(steps)
            
            if result:
                print("[命令行] 脚本执行成功")
            else:
                print("[命令行] 脚本执行失败")
                sys.exit(1)
                
        except Exception as e:
            import traceback
            error_msg = str(e).encode('utf-8', errors='replace').decode('utf-8')
            traceback_str = traceback.format_exc().encode('utf-8', errors='replace').decode('utf-8')
            print(f"执行错误: {error_msg}")
            print(f"错误详情:\n{traceback_str}")
            sys.exit(1)
    else:
        # GUI 模式
        pyautogui.FAILSAFE = False
        try:
            theme = args.theme
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f: theme = json.load(f).get('theme', 'litera')
        except: pass
        main_window = tb.Window(themename=theme)
        app = MacroApp(main_window)
        main_window.mainloop()
