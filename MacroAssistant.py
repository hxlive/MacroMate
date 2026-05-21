# -*- coding: utf-8 -*-
# MacroAssistant.py
# 描述: 自动化宏的 GUI 界面
# 版本: 1.7.0
# 变更: 迁移部分代码至sys_utils.py和gui_utils.py,并修复一些小bug和优化了代码结构

# 使用: 
#   - GUI 模式: python MacroAssistant.py
#   - 命令行: python MacroAssistant.py script.json
#             python MacroAssistant.py --run script.json
#             python MacroAssistant.py --theme darkly (指定主题)

import os
import sys

# 允许在最早期通过命令行覆写日志编码（必须在 init_system_runtime 前）
for i, arg in enumerate(sys.argv):
    if arg.startswith('--log-encoding='):
        os.environ['MACROASSISTANT_STDIO_ENCODING'] = arg.split('=', 1)[1].strip()
    elif arg == '--log-encoding' and i + 1 < len(sys.argv):
        os.environ['MACROASSISTANT_STDIO_ENCODING'] = sys.argv[i + 1].strip()

import sys_utils  # [新增] 系统底层工具与初始化
sys_utils.init_system_runtime() # [新增] 初始化 DPI 感知与流重定向

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import json
import pyautogui
import threading
import copy
import ttkbootstrap as tb
import queue
import ctypes
from PIL import Image, ImageGrab, ImageTk
import webbrowser


# =================================================================
# 全局配置
# =================================================================
APP_VERSION = "1.7.0"
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

# [重构] 导入核心模块与工具类
try:
    import core_engine as macro_engine
    import ocr_engine
    import vlm_engine
    import gui_utils
    
    from sys_utils import (
        init_system_runtime, set_windows_app_id, 
        GlobalHotkeyManager, MouseTracker, RegionSelector, 
        HotkeySettingsDialog, VLMSettingsDialog, ImageTooltipManager, MiniStatusWindow
    )
    from gui_utils import (
        ParamWidgetFactory, parse_region_string, resource_path, get_icon_path,
        update_loop_params, update_run_params, param_display_to_internal, param_internal_to_display
    )
    from core_engine import HotkeyUtils, MacroSchema, validate_macro_data, MacroPersistence
except ImportError as e:
    messagebox.showerror("导入错误", f"缺少必要的模块文件或导入失败: {e}\n请确保所有 py 文件都在同一目录。")
    exit()

def capitalize_hotkey_str(s): return HotkeyUtils.format_hotkey_display(s)


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
                
                # [重构] 使用 sys_utils 设置 AppUserModelID
                sys_utils.set_windows_app_id(APP_VERSION)
                print(f"[Info] AppUserModelID 已设置: {APP_VERSION}")
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
        self._stop_in_progress = False   # 防重复停止标志
        self._run_pending = False        # 延迟启动挂起标志
        self._pending_run_id = None      # 延迟启动的 after ID
        
        # [新增] 迷你状态栏窗口
        self.mini_status_window = None
        
        self.hotkey_run_str = tb.StringVar(value=DEFAULT_HOTKEY_RUN)
        self.hotkey_stop_str = tb.StringVar(value=DEFAULT_HOTKEY_STOP)
        self.hotkey_manager = GlobalHotkeyManager(
            self.root,
            get_run_str_cb=self.hotkey_run_str.get,
            get_stop_str_cb=self.hotkey_stop_str.get,
            trigger_run_cb=self.safe_run_macro,
            trigger_stop_cb=self.safe_stop_macro
        )
        
        self.current_theme = tb.StringVar(value=self.root.style.theme_use())
        self.skip_confirm_var = tb.BooleanVar(value=False)
        self.dont_minimize_var = tb.BooleanVar(value=False)
        self.enhanced_mode_var = tb.BooleanVar(value=True)
        self.run_enabled_var = tb.BooleanVar(value=False)
        self.recent_files = []
        self.status_queue = queue.Queue()
        
        # [变更] 使用 MouseTracker 类替代原有的 job 和 func
        self.mouse_pos_var = tb.StringVar()
        self.mouse_tracker = MouseTracker(self.root, self.mouse_pos_var)
        
        self._last_mini_status = (None, None)
        
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

        try:
            self._init_menu()
            self._init_ui()
        except Exception as e:
            self.root.deiconify()
            self.root.update()
            messagebox.showerror("初始化失败", f"UI 构建出错:\n{str(e)}")
            self.root.quit()
            return

        # 初始化悬浮预览管理器
        self.tooltip_manager = ImageTooltipManager(self.steps_tree, lambda: self.steps)

        self.load_app_settings()
        self.update_recent_files_menu()
        self.update_status_bar_hotkeys()
        self.root.after(500, self.hotkey_manager.check_conflicts)
        self.hotkey_manager.start_listener()
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
        # [修复H-7] 应用可能在检测期间已关闭，需先检查生命周期
        if not self.is_app_running:
            return
        self.available_ocr_engines = engines
        self.available_ocr_keys = [e[0] for e in engines]
        if 'none' in self.available_ocr_keys:
            print("[警告] 未找到任何可用的OCR引擎 (RapidOCR, Tesseract, WinOCR)。")
            try:
                self.status_var.set("WARN 未找到可用 OCR 引擎，文本查找功能不可用。")
            except Exception:
                pass
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
        settings_menu.add_command(label="⌨ 快捷键设置...", command=self.open_hotkey_settings)
        settings_menu.add_separator()
        settings_menu.add_command(label="🤖 AI 设置...", command=self.open_vlm_settings)

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
        
        enhanced_check = ttk.Checkbutton(check_frame, text="开启增强模式 (OCR 多级缩放匹配)", variable=self.enhanced_mode_var, bootstyle="success-round-toggle")
        enhanced_check.grid(row=1, column=0, sticky="w", padx=2, pady=(0, 5))
        
        run_enabled_check = ttk.Checkbutton(check_frame, text="启用 RUN 步骤 (注意安全风险)", variable=self.run_enabled_var, bootstyle="danger-round-toggle")
        run_enabled_check.grid(row=1, column=1, sticky="w", padx=2, pady=(0, 5))
        
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
        
        if not self.hotkey_manager.check_conflicts(show_success=False):
            messagebox.showwarning("冲突警告", "快捷键已保存，但检测到冲突。\n请确保没有其他程序占用它。", parent=self.root)
        
        self.hotkey_manager.restart_listener()
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
        about_dialog.withdraw()  # 立即隐藏，防止闪烁
        self._about_dialog_ref = about_dialog
        about_dialog.title("关于")
        about_dialog.geometry("500x400")  # 足够宽度显示完整链接
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
        about_dialog.deiconify()  # 位置确定后再显示
        
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
                # [修复] 使用 load()+copy() 确保图像数据在文件关闭后仍有效
                from PIL import ImageTk
                with Image.open(icon_path) as _raw:
                    _raw.load()
                    _icon_copy = _raw.copy()
                resized_img = _icon_copy.resize((96, 96), Image.Resampling.LANCZOS)
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
        
        # [变更] 使用 MouseTracker 类停止
        self.mouse_tracker.stop()
            
        if self.hotkey_manager and self.hotkey_manager.listener:
            print("[Info] 正在停止快捷键监听器...")
            try:
                self.hotkey_manager.listener.stop()
                self.hotkey_manager.listener.join(timeout=0.5) 
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
        
        # [重构] 准备回调函数字典
        callbacks = {
            'on_select_region': self.on_select_region,
            'browse_image': self.browse_image,
            'on_test_find_image_click': self.on_test_find_image_click,
            'on_test_find_text_click': self.on_test_find_text_click,
            'on_test_ai_command_click': self.on_test_ai_command_click,
            'update_loop_params': self.update_loop_params,
            'update_run_params': self.update_run_params,
            'mouse_tracker': self.mouse_tracker,
            'mouse_pos_var': self.mouse_pos_var
        }

        # [重构] 使用工厂模式构建参数表单
        res = self.widget_factory.build_action_form(
            action_key, 
            self.param_frame, 
            self.param_widgets, 
            self.available_ocr_keys, 
            callbacks
        )

        # 处理特殊返回 (如 OCR 不可用时自动切回图像模式)
        if res == "SWITCH_TO_FIND_IMAGE":
            self.action_type.set(MacroSchema.ACTION_TRANSLATIONS['FIND_IMAGE'])
            self.update_param_fields(None)
            return


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
        self.root.after(300, lambda: self._do_select_region(entry_widget))
        
    def _do_select_region(self, entry_widget):
        
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
            self._run_test_after_iconify(self._test_find_image, (path, conf, region_box))
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
            self._run_test_after_iconify(self._test_find_text, (text, lang, engine, region_box))
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
            self._run_test_after_iconify(self._test_ai_command, (instruction, region_box))
        except: messagebox.showerror("错误", "参数无效")

    def _run_test_after_iconify(self, func, args, attempts=0):
        if self.root.state() == 'iconic' or attempts >= 15:
            self.root.after(250, lambda: self._run_test_thread(func, args))
            return
        self.root.after(100, lambda: self._run_test_after_iconify(func, args, attempts + 1))

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
        error_msg = str(e)
        messagebox.showerror("错误", error_msg)
        self.update_status_bar_hotkeys()

    def browse_image(self):
        """浏览图片文件（保持向后兼容）"""
        f = filedialog.askopenfilename(filetypes=[("PNG", "*.png"), ("All", "*.*")])
        if f: 
            f = os.path.abspath(f) 
            self.param_widgets['path'].delete(0, tk.END)
            self.param_widgets['path'].insert(0, f)

    def add_or_update_step(self):
        """添加或更新步骤 (已重构：使用工厂收集数据)"""
        action = MacroSchema.ACTION_KEYS_TO_NAME.get(self.action_type.get())
        if not action: return
        
        # [重构] 使用工厂模式收集并验证数据
        params, error = self.widget_factory.collect_step_data(action, self.param_widgets, self.FULL_OCR_KEY_MAP)
        if error:
            messagebox.showwarning("输入错误", error)
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

    def safe_run_macro(self):
        # [修复BUG-5] 步骤为空时给出明确提示，而非静默无响应
        if not self.is_macro_running and self.editing_index is None:
            if not self.steps:
                self.root.after(0, lambda: self.status_var.set("提示: 宏为空，请先添加步骤再运行"))
                return
            self.root.after(0, self.run_macro, True)
        
    def safe_stop_macro(self):
        """[即时中断] 通过 ctypes 向执行线程注入异常，无论其封锁在哪个阶段都能立刻中断。"""
        if self._stop_in_progress:
            return
        if self._run_pending:
            self._run_pending = False
            if self._pending_run_id is not None:
                self.root.after_cancel(self._pending_run_id)
                self._pending_run_id = None
            self.status_var.set("已取消待执行的宏")
            return
        if not self.is_macro_running:
            return
        self._stop_in_progress = True
        self.root.after(0, self.status_var.set, "正在停止...")
        # 同时设置标志位（兼容 WAIT 内的分段检查）
        if self.current_run_context:
            self.current_run_context['stop_requested'] = True
        # 强制向执行线程注入 MacroStopException
        t = self._macro_thread
        if t and t.is_alive():
            tid = t.ident
            if tid:
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

        run_steps = [s for s in self.steps if s.get('action') == 'RUN' and s.get('enabled', True)]
        if run_steps and self.run_enabled_var.get() and not hotkey and not self.skip_confirm_var.get():
            if not messagebox.askyesno(
                "安全警告",
                f"此宏包含 {len(run_steps)} 个执行外部命令的步骤（RUN）。\n\n"
                "执行外部命令可能存在安全风险，请确保来源可信。\n\n"
                "是否继续运行？\n"
                "(可在左下角开关中永久禁用 RUN 步骤)"
            ): return
            
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
        self._run_pending = True
        self._pending_run_id = self.root.after(600, self._start_macro_thread)

    def _start_macro_thread(self):
        self._run_pending = False
        self._pending_run_id = None
        self.is_macro_running = True
        self.current_run_context = {
            'stop_requested': False,
            'stop_key_str': self.hotkey_stop_str.get(),
            'enhanced_mode': self.enhanced_mode_var.get(),
            'run_enabled': self.run_enabled_var.get()
        }
        self._macro_thread = threading.Thread(target=self._run, args=(copy.deepcopy(self.steps),), daemon=True)
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
        self._stop_in_progress = False
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
            
            # [新增] 同步更新迷你窗口（仅在内容变化时刷新）
            if self.mini_status_window:
                stop_display = capitalize_hotkey_str(self.hotkey_stop_str.get())
                current_loop_status = self.loop_status_var.get()
                new_status = (f"宏正在运行... [点击停止 或 {stop_display}]", current_loop_status)
                if new_status != self._last_mini_status:
                    self._last_mini_status = new_status
                    self.mini_status_window.update_status(new_status[0], new_status[1])
        except queue.Empty:
            pass
        except Exception as e:
            print(f"[StatusQueue] 错误: {e}")
            
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
        """保存宏 (已重构)"""
        f = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if f:
            try:
                MacroPersistence.save(f, self.steps)
                messagebox.showinfo("成功", "宏已保存！")
                self.add_to_recent_files(f)
            except Exception as e: messagebox.showerror("失败", str(e))

    def _load_file(self, f):
        """从文件加载宏 (已重构)"""
        if not os.path.exists(f):
            messagebox.showerror("失败", "文件不存在")
            if f in self.recent_files: 
                self.recent_files.remove(f); self.save_app_settings(); self.update_recent_files_menu()
            return
        try:
            self.cancel_edit_mode()
            data = MacroPersistence.load(f)
            
            # 验证JSON数据结构
            if not validate_macro_data(data):
                messagebox.showerror("加载失败", f"文件格式无效或损坏:\n{os.path.basename(f)}")
                return
            
            self.steps = data
            self.update_listbox_display()
            self.status_var.set(f"已加载: {os.path.basename(f)}")
            self.add_to_recent_files(f)
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
                    self.run_enabled_var.set(d.get('run_enabled', False))
                    self.skip_confirm_var.set(d.get('skip_confirm', False))
                    self.dont_minimize_var.set(d.get('dont_minimize', False))
        except Exception as e:
            print(f"[设置] 加载应用设置失败: {e}")
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
                    'enhanced_mode': self.enhanced_mode_var.get(),
                    'run_enabled': self.run_enabled_var.get(),
                    'skip_confirm': self.skip_confirm_var.get(),
                    'dont_minimize': self.dont_minimize_var.get()
                }, f, indent=2)
        except Exception as e:
            print(f"[设置] 保存应用设置失败: {e}")

    def change_theme(self):
        self.root.style.theme_use(self.current_theme.get())
        self.root.style.configure(".", font=self.font_ui)
        self.save_app_settings()
        



if __name__ == "__main__":
    import argparse
    
    # 命令行参数解析
    parser = argparse.ArgumentParser(description='MacroAssistant - 自动化宏工具')
    parser.add_argument('script_file', nargs='?', help='要执行的脚本文件 (.json)')
    parser.add_argument('--run', dest='run', help='执行指定脚本文件 (效果同直接传参)')
    parser.add_argument('--enable-run', action='store_true', help='允许命令行模式执行 RUN 步骤；默认禁用')
    parser.add_argument('--theme', dest='theme', default='litera', help='指定主题')
    parser.add_argument('--log-encoding', dest='log_encoding', default='', help='指定日志输出编码（如 utf-8 或 gbk）')
    args = parser.parse_args()
    
    # 确定要执行的脚本
    script_file = args.script_file or args.run
    
    if script_file:
        # 命令行模式：执行脚本
        if not os.path.exists(script_file):
            print(f"[CLI] ERROR: Script file not found: {script_file}")
            sys.exit(1)
        
        print(f"[CLI] Start script: {script_file}")
        
        try:
            # 加载脚本
            print("[CLI] Loading script...")
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
                print("[CLI] ERROR: No steps in script")
                sys.exit(1)
            
            # 执行脚本
            print(f"[CLI] Total steps: {len(steps)}, running...")
            run_context = {'run_enabled': args.enable_run}
            if not args.enable_run:
                print("[CLI] RUN steps are disabled by default. Use --enable-run to allow RUN actions.")
            result = macro_engine.execute_steps(steps, run_context=run_context)
            
            if result:
                print("[CLI] Script finished successfully")
            else:
                print("[CLI] Script failed")
                sys.exit(1)
                
        except Exception as e:
            import traceback
            error_msg = str(e)
            traceback_str = traceback.format_exc()
            print(f"[CLI] ERROR: {error_msg}")
            print(f"[CLI] TRACEBACK:\n{traceback_str}")
            sys.exit(1)
    else:
        # GUI 模式
        pyautogui.FAILSAFE = False
        try:
            theme = args.theme
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f: theme = json.load(f).get('theme', 'litera')
        except Exception:
            pass
        main_window = tb.Window(themename=theme)
        app = MacroApp(main_window)
        main_window.mainloop()
