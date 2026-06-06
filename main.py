"""
HLW8032 电力监测分析工具 - 主程序

基于 HLW8032 电量计量芯片的 PC 端电力监测与分析工具。
支持串口实时数据采集、功率曲线绘制、用电设备自动识别。

功能：
  1. 串口实时监测 - 电压/电流/功率/功率因数实时显示
  2. 功率曲线绘制 - 自动绘制功率时间曲线
  3. 设备自动识别 - 基于9项特征的5类设备识别（准确率>85%）
  4. 数据导出 - CSV 格式导出
  5. 文件解析 - 支持十六进制日志文件解析

运行方式：
  python main.py
  python main.py --file data.txt    # 直接解析文件
"""

import sys
import os
import time
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from datetime import datetime

import matplotlib
matplotlib.use('TkAgg')
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.dates as mdates

# 配置中文字体支持（exe 打包后系统字体不可用，需手动注册）
import matplotlib.font_manager as fm
import sys as _sys
import os as _os

def _setup_chinese_font():
    """注册中文字体——优先用打包字体，其次系统字体"""
    # 找到字体文件的真实路径（PyInstaller 打包后在 sys._MEIPASS 中）
    font_candidates = []
    if getattr(_sys, 'frozen', False):
        base = _sys._MEIPASS
        font_candidates.append(_os.path.join(base, 'msyh.ttc'))
    font_candidates.append(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'msyh.ttc'))

    reg_font = None
    for fp in font_candidates:
        if _os.path.exists(fp):
            try:
                fm.fontManager.addfont(fp)
                reg_font = fp
                break
            except Exception:
                continue

    if reg_font:
        prop = fm.FontProperties(fname=reg_font)
        font_name = prop.get_name()
        matplotlib.rcParams['font.sans-serif'] = [font_name] + matplotlib.rcParams['font.sans-serif']
    else:
        # 回退：尝试系统已有的中文字体
        avail = [f.name for f in fm.fontManager.ttflist
                 if any(k in f.name.lower() for k in ['yahei', 'simhei', 'simsun', 'kai', 'ming'])]
        if avail:
            matplotlib.rcParams['font.sans-serif'] = [avail[0]] + matplotlib.rcParams['font.sans-serif']

matplotlib.rcParams['axes.unicode_minus'] = False
_setup_chinese_font()

# 尝试导入串口库
try:
    import serial
    import serial.tools.list_ports
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False

# 导入自定义模块
from hlw8032_parser import (HLW8032Parser, HLW8032Sample, FRAME_SIZE,
                            parse_hex_file, parse_binary_file,
                            DEFAULT_VOLTAGE_COEFF, DEFAULT_CURRENT_COEFF)
from data_storage import DataStorage, PowerSample
from device_analyzer import (analyze, analyze_candidates, AnalysisResult,
                             CandidateDevice, DeviceMetrics, build_features)


# ============================================================
# 配置常量
# ============================================================

APP_TITLE = "HLW8032 电力监测分析工具"
APP_VERSION = "1.0.0"
WINDOW_SIZE = "1100x750"

# 深色主题配色
COLORS = {
    "bg": "#1a1a2e",
    "bg2": "#16213e",
    "bg3": "#0f3460",
    "fg": "#e0e0e0",
    "fg2": "#a0a0b0",
    "accent": "#64b5ff",
    "accent2": "#ffd166",
    "green": "#31d07f",
    "red": "#ff6b6b",
    "yellow": "#f6c34a",
}


# ============================================================
# 自定义样式
# ============================================================

def setup_styles():
    """配置 ttk 样式（深色主题）"""
    style = ttk.Style()
    style.theme_use('clam')

    style.configure('TFrame', background=COLORS["bg"])
    style.configure('TLabel', background=COLORS["bg"], foreground=COLORS["fg"])
    style.configure('TButton', background=COLORS["bg3"], foreground=COLORS["fg"],
                    borderwidth=1, focusthickness=0)
    style.map('TButton',
              background=[('active', COLORS["accent"])],
              foreground=[('active', '#ffffff')])

    style.configure('TNotebook', background=COLORS["bg"], borderwidth=0)
    style.configure('TNotebook.Tab', background=COLORS["bg2"], foreground=COLORS["fg2"],
                    padding=[20, 8], borderwidth=0)
    style.map('TNotebook.Tab',
              background=[('selected', COLORS["bg3"])],
              foreground=[('selected', COLORS["fg"])])

    style.configure('TCheckbutton', background=COLORS["bg"], foreground=COLORS["fg"])

    style.configure('TCombobox',
                    fieldbackground=COLORS["bg2"],
                    background=COLORS["bg2"],
                    foreground=COLORS["fg"],
                    selectbackground=COLORS["bg3"],
                    selectforeground=COLORS["fg"])
    style.map('TCombobox',
              fieldbackground=[('readonly', COLORS["bg2"])],
              foreground=[('readonly', COLORS["fg"])])

    style.configure('TEntry', fieldbackground=COLORS["bg2"], foreground=COLORS["fg"])

    # 大字号标签样式
    style.configure('Big.TLabel', font=('Consolas', 28, 'bold'))
    style.configure('Medium.TLabel', font=('Microsoft YaHei', 11))
    style.configure('Title.TLabel', font=('Microsoft YaHei', 16, 'bold'),
                    foreground=COLORS["accent"])
    style.configure('Value.TLabel', font=('Consolas', 14, 'bold'))
    style.configure('Green.TLabel', foreground=COLORS["green"])
    style.configure('Red.TLabel', foreground=COLORS["red"])
    style.configure('Yellow.TLabel', foreground=COLORS["accent2"])


# ============================================================
# 功率曲线绘制画布
# ============================================================

class PowerCurveCanvas(ttk.Frame):
    """功率曲线绘制组件（嵌入 matplotlib）"""

    def __init__(self, parent):
        super().__init__(parent)
        self.fig = Figure(figsize=(8, 3.5), dpi=100, facecolor=COLORS["bg"])
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor('#101820')

        # 边框颜色
        for spine in self.ax.spines.values():
            spine.set_color('#334455')

        self.ax.tick_params(colors=COLORS["fg2"], labelsize=8)
        self.ax.set_xlabel("时间", color=COLORS["fg2"], fontsize=9)
        self.ax.set_ylabel("功率 (W)", color=COLORS["fg2"], fontsize=9)
        self.ax.grid(True, alpha=0.2, color='#334455')

        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    def update(self, samples: list, title: str = "功率曲线"):
        """更新功率曲线
        
        Args:
            samples: PowerSample 列表
            title: 图表标题
        """
        self.ax.clear()
        self.ax.set_facecolor('#101820')

        if not samples or len(samples) < 2:
            self.ax.text(0.5, 0.5, "暂无数据", transform=self.ax.transAxes,
                         ha='center', va='center', color=COLORS["fg2"], fontsize=14)
            self.ax.set_title(title, color=COLORS["fg"], fontsize=11)
            self.canvas.draw()
            return

        times = [datetime.fromtimestamp(s.timestamp) for s in samples]
        powers = [s.power for s in samples]

        # 绘制功率折线
        self.ax.plot(times, powers, color=COLORS["accent2"], linewidth=1.2, alpha=0.9)

        # 填充区域
        self.ax.fill_between(times, 0, powers, color=COLORS["accent2"], alpha=0.1)

        # 末点标记
        if len(powers) > 0:
            self.ax.scatter(times[-1], powers[-1], color=COLORS["accent"],
                           s=30, zorder=5, edgecolors='white', linewidth=0.5)

        # 格式设置
        self.ax.set_title(title, color=COLORS["fg"], fontsize=11)
        self.ax.set_xlabel("时间", color=COLORS["fg2"], fontsize=9)
        self.ax.set_ylabel("功率 (W)", color=COLORS["fg2"], fontsize=9)
        self.ax.grid(True, alpha=0.2, color='#334455')
        self.ax.tick_params(colors=COLORS["fg2"], labelsize=8)

        # 时间轴格式化
        self.ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))

        # 自动调整布局
        self.fig.tight_layout()
        self.canvas.draw()


# ============================================================
# 串口监测标签页
# ============================================================

class SerialMonitorTab(ttk.Frame):
    """串口实时监测页面"""

    def __init__(self, parent, storage: DataStorage, on_sample):
        super().__init__(parent)
        self.storage = storage
        self.on_sample = on_sample  # 回调：通知主窗口有新数据
        self.parser = HLW8032Parser()
        self.serial_conn = None
        self.running = False
        self.buffer = bytearray()
        self.log_lines = []

        self._build_ui()
        self._refresh_ports()
        self.after(100, lambda: self._log(
            f"默认系数: V={self.parser.voltage_coeff}, I={self.parser.current_coeff} (点击「应用」修改)"))

    def _build_ui(self):
        # ----- 顶部控制栏 -----
        control_bar = ttk.Frame(self)
        control_bar.pack(fill=tk.X, padx=10, pady=(10, 5))

        ttk.Label(control_bar, text="串口:", style='Medium.TLabel').pack(side=tk.LEFT, padx=5)
        self.port_combo = ttk.Combobox(control_bar, width=14, state='readonly')
        self.port_combo.pack(side=tk.LEFT, padx=2)

        ttk.Label(control_bar, text="波特率:", style='Medium.TLabel').pack(side=tk.LEFT, padx=5)
        self.baud_combo = ttk.Combobox(control_bar, width=8, state='readonly',
                                       values=["2400", "4800", "9600", "19200", "38400", "115200"])
        self.baud_combo.set("9600")
        self.baud_combo.pack(side=tk.LEFT, padx=2)

        ttk.Button(control_bar, text="刷新", command=self._refresh_ports).pack(side=tk.LEFT, padx=5)
        self.connect_btn = ttk.Button(control_bar, text="连接", command=self._toggle_serial)
        self.connect_btn.pack(side=tk.LEFT, padx=5)

        # 系数调整（直接 Entry 方式，不依赖 StringVar）
        ttk.Label(control_bar, text="V系数:", style='Medium.TLabel').pack(side=tk.LEFT, padx=(20, 2))
        self.v_coeff_entry = tk.Entry(control_bar, width=6,
                                      bg='#f0f0f0', fg='#000000',
                                      insertbackground='#000000',
                                      font=('Consolas', 11, 'bold'),
                                      relief=tk.SUNKEN, borderwidth=2,
                                      justify='center')
        self.v_coeff_entry.insert(0, str(DEFAULT_VOLTAGE_COEFF))
        self.v_coeff_entry.pack(side=tk.LEFT, padx=2)

        ttk.Label(control_bar, text="I系数:", style='Medium.TLabel').pack(side=tk.LEFT, padx=5)
        self.i_coeff_entry = tk.Entry(control_bar, width=6,
                                      bg='#f0f0f0', fg='#000000',
                                      insertbackground='#000000',
                                      font=('Consolas', 11, 'bold'),
                                      relief=tk.SUNKEN, borderwidth=2,
                                      justify='center')
        self.i_coeff_entry.insert(0, str(DEFAULT_CURRENT_COEFF))
        self.i_coeff_entry.pack(side=tk.LEFT, padx=2)

        ttk.Button(control_bar, text="应用", command=self._apply_coefficients).pack(side=tk.LEFT, padx=5)
        ttk.Button(control_bar, text="清空数据", command=self._clear_data).pack(side=tk.LEFT, padx=5)

        # ----- 实时数值显示区 -----
        values_frame = tk.Frame(self, bg=COLORS["bg2"],
                                highlightbackground='#334455', highlightthickness=1)
        values_frame.pack(fill=tk.X, padx=10, pady=8)

        # 电压
        v_frame = tk.Frame(values_frame, bg=COLORS["bg2"])
        v_frame.pack(side=tk.LEFT, expand=True, padx=15, pady=10)
        tk.Label(v_frame, text="电压", bg=COLORS["bg2"], fg=COLORS["fg2"],
                 font=('Microsoft YaHei', 10)).pack()
        self.voltage_label = tk.Label(v_frame, text="--.--", bg=COLORS["bg2"],
                                      fg=COLORS["accent"], font=('Consolas', 30, 'bold'))
        self.voltage_label.pack()
        tk.Label(v_frame, text="V", bg=COLORS["bg2"], fg=COLORS["fg2"],
                 font=('Microsoft YaHei', 9)).pack()

        # 电流
        i_frame = tk.Frame(values_frame, bg=COLORS["bg2"])
        i_frame.pack(side=tk.LEFT, expand=True, padx=15, pady=10)
        tk.Label(i_frame, text="电流", bg=COLORS["bg2"], fg=COLORS["fg2"],
                 font=('Microsoft YaHei', 10)).pack()
        self.current_label = tk.Label(i_frame, text="--.---", bg=COLORS["bg2"],
                                      fg=COLORS["green"], font=('Consolas', 30, 'bold'))
        self.current_label.pack()
        tk.Label(i_frame, text="A", bg=COLORS["bg2"], fg=COLORS["fg2"],
                 font=('Microsoft YaHei', 9)).pack()

        # 功率
        p_frame = tk.Frame(values_frame, bg=COLORS["bg2"])
        p_frame.pack(side=tk.LEFT, expand=True, padx=15, pady=10)
        tk.Label(p_frame, text="功率", bg=COLORS["bg2"], fg=COLORS["fg2"],
                 font=('Microsoft YaHei', 10)).pack()
        self.power_label = tk.Label(p_frame, text="--.---", bg=COLORS["bg2"],
                                    fg=COLORS["accent2"], font=('Consolas', 30, 'bold'))
        self.power_label.pack()
        tk.Label(p_frame, text="W", bg=COLORS["bg2"], fg=COLORS["fg2"],
                 font=('Microsoft YaHei', 9)).pack()

        # 功率因数 + 累计用电
        pf_frame = tk.Frame(values_frame, bg=COLORS["bg2"])
        pf_frame.pack(side=tk.LEFT, expand=True, padx=15, pady=10)
        tk.Label(pf_frame, text="功率因数", bg=COLORS["bg2"], fg=COLORS["fg2"],
                 font=('Microsoft YaHei', 10)).pack()
        self.pf_label = tk.Label(pf_frame, text="--.--", bg=COLORS["bg2"],
                                 fg=COLORS["yellow"], font=('Consolas', 20, 'bold'))
        self.pf_label.pack()
        tk.Label(pf_frame, text="", bg=COLORS["bg2"], font=('Arial', 3)).pack()
        self.kwh_label = tk.Label(pf_frame, text="累计: 0.000 kWh", bg=COLORS["bg2"],
                                  fg=COLORS["fg2"], font=('Microsoft YaHei', 9))
        self.kwh_label.pack()

        # 样本计数
        self.sample_count_label = tk.Label(values_frame, text="0", bg=COLORS["bg2"],
                                           fg=COLORS["fg2"], font=('Consolas', 10))
        self.sample_count_label.pack(side=tk.BOTTOM, pady=3)

        # ----- 日志区 -----
        log_frame = ttk.Frame(self)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        ttk.Label(log_frame, text="数据日志:", style='Medium.TLabel').pack(anchor=tk.W)
        self.log_text = scrolledtext.ScrolledText(
            log_frame, height=12, font=('Consolas', 9),
            bg='#0d1117', fg=COLORS["fg2"], insertbackground='white',
            relief=tk.FLAT, borderwidth=0
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

    # ----------------------------------------------------------
    # 串口操作
    # ----------------------------------------------------------

    def _refresh_ports(self):
        """刷新可用串口列表，默认COM6"""
        if not HAS_SERIAL:
            self.port_combo['values'] = ["请安装 pyserial"]
            self.port_combo.set("COM6")
            return
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_combo['values'] = ports
        # 优先选 COM6，否则选第一个
        current = self.port_combo.get()
        if "COM6" in ports:
            self.port_combo.set("COM6")
        elif ports and not current:
            self.port_combo.set(ports[0])
        elif not ports:
            self.port_combo.set("COM6")
            self.port_combo['values'] = ["COM6 (未检测到)"]

    def _apply_coefficients(self):
        """应用用户输入的电压/电流系数到解析器（直接从 Entry 读取）"""
        old_v = self.parser.voltage_coeff
        old_i = self.parser.current_coeff
        try:
            v_str = self.v_coeff_entry.get().strip()
            i_str = self.i_coeff_entry.get().strip()
            if not v_str or not i_str:
                messagebox.showwarning("输入为空", "请输入有效的数值系数（如 1.88 和 1.0）")
                return
            v = float(v_str)
            i = float(i_str)
            if v <= 0 or i <= 0:
                messagebox.showwarning("输入错误", "系数必须大于 0")
                return
            self.parser.voltage_coeff = v
            self.parser.current_coeff = i
            self._log(f"系数已更新: V={old_v}→{v}, I={old_i}→{i}")
        except ValueError:
            messagebox.showwarning("输入错误", "无法解析系数值，请确保输入为数字")

    def _clear_data(self):
        self.storage.clear_samples()
        self._log("数据已清空")
        # 通知主窗口刷新
        self.on_sample(None)

    def _toggle_serial(self):
        if self.running:
            self._stop_serial()
        else:
            self._start_serial()

    def _start_serial(self):
        if not HAS_SERIAL:
            messagebox.showwarning("缺少库", "请先安装 pyserial: pip install pyserial")
            return

        port = self.port_combo.get()
        if not port:
            messagebox.showwarning("未选择串口", "请先选择串口")
            return

        try:
            self._apply_coefficients()
            baud_str = self.baud_combo.get()
            if not baud_str:
                messagebox.showwarning("未选择波特率", "请选择波特率")
                return
            baud = int(baud_str)
            self.serial_conn = serial.Serial(port, baud, timeout=0.05)
            self.running = True
            self.buffer.clear()
            self.connect_btn.config(text="断开")
            self._log(f"已连接: {port} @ {baud} bps")
            threading.Thread(target=self._read_serial_loop, daemon=True).start()
        except Exception as e:
            messagebox.showerror("串口错误", str(e))

    def _stop_serial(self):
        self.running = False
        if self.serial_conn:
            try:
                self.serial_conn.close()
            except:
                pass
            self.serial_conn = None
        self.connect_btn.config(text="连接")
        self._log("已断开")

    def _read_serial_loop(self):
        """串口读取循环（后台线程）"""
        while self.running:
            try:
                if self.serial_conn.in_waiting:
                    data = self.serial_conn.read(self.serial_conn.in_waiting)
                    self.buffer.extend(data)
                    samples = self.parser.feed_binary(self.buffer)
                    for s in samples:
                        self._on_new_sample(s)
            except Exception as e:
                self.after(0, lambda: self._log(f"读取错误: {e}"))
                self.after(0, self._stop_serial)
                break
            time.sleep(0.01)

    def _on_new_sample(self, sample: HLW8032Sample):
        """处理新的 HLW8032 采样"""
        # 记录到存储
        self.storage.record_sample(
            voltage=sample.voltage,
            current=sample.current,
            power=sample.power,
            power_factor=sample.power_factor,
            timestamp=sample.timestamp
        )

        # 更新 UI（需要在主线程执行）
        self.after(0, lambda: self._update_display(sample))

    def _update_display(self, sample: HLW8032Sample):
        """更新实时数值显示"""
        self.voltage_label.config(text=f"{sample.voltage:.2f}")
        self.current_label.config(text=f"{sample.current:.4f}")
        self.power_label.config(text=f"{sample.power:.3f}")
        self.pf_label.config(text=f"{sample.power_factor:.3f}")
        self.kwh_label.config(text=f"累计: {self.storage.total_kwh:.3f} kWh")
        self.sample_count_label.config(text=f"样本: {self.storage.sample_count}")

        # 日志
        t = datetime.fromtimestamp(sample.timestamp).strftime('%H:%M:%S')
        self._log(f"{t} | V={sample.voltage:.2f}V I={sample.current:.4f}A "
                  f"P={sample.power:.3f}W PF={sample.power_factor:.3f}")

        # 通知主窗口
        self.on_sample(sample)

    # ----------------------------------------------------------
    # 日志
    # ----------------------------------------------------------

    def _log(self, message):
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        # 限制行数
        lines = int(self.log_text.index('end-1c').split('.')[0])
        if lines > 500:
            self.log_text.delete('1.0', '10.0')


# ============================================================
# 文件解析标签页
# ============================================================

class FileParserTab(ttk.Frame):
    """文件解析页面 - 支持十六进制文本文件和二进制文件解析"""

    def __init__(self, parent, storage: DataStorage, on_sample, serial_tab=None):
        super().__init__(parent)
        self.storage = storage
        self.on_sample = on_sample
        self.serial_tab = serial_tab
        self.parser = HLW8032Parser()
        self._build_ui()

    def _build_ui(self):
        # 文件选择
        top = ttk.Frame(self)
        top.pack(fill=tk.X, padx=10, pady=10)

        ttk.Label(top, text="数据文件:", style='Medium.TLabel').pack(side=tk.LEFT, padx=5)
        self.file_path_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.file_path_var, width=60).pack(side=tk.LEFT, padx=5)
        ttk.Button(top, text="浏览", command=self._browse_file).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text="解析", command=self._parse_file).pack(side=tk.LEFT, padx=5)

        # 选项
        opts = tk.Frame(self, bg=COLORS["bg"])
        opts.pack(fill=tk.X, padx=10, pady=5)
        self.clear_first_var = tk.BooleanVar(value=True)
        tk.Checkbutton(opts, text="解析前清空旧数据", variable=self.clear_first_var,
                       bg=COLORS["bg"], fg=COLORS["fg2"],
                       selectcolor=COLORS["bg2"], activebackground=COLORS["bg"],
                       activeforeground=COLORS["fg"]).pack(side=tk.LEFT)
        tk.Label(opts, text="提示：支持 .txt .log（十六进制文本）和 .bin（二进制原始数据）",
                 bg=COLORS["bg"], fg=COLORS["fg2"], font=('Microsoft YaHei', 8)).pack(side=tk.LEFT, padx=20)

        # 结果区
        result_frame = ttk.Frame(self)
        result_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        ttk.Label(result_frame, text="解析结果:", style='Medium.TLabel').pack(anchor=tk.W)
        self.result_text = scrolledtext.ScrolledText(
            result_frame, height=22, font=('Consolas', 10),
            bg='#0d1117', fg=COLORS["fg2"], insertbackground='white',
            relief=tk.FLAT
        )
        self.result_text.pack(fill=tk.BOTH, expand=True)

        # 统计标签
        self.stats_label = ttk.Label(result_frame, text="未解析文件", style='Medium.TLabel')
        self.stats_label.pack(anchor=tk.W, pady=5)

    def _browse_file(self):
        path = filedialog.askopenfilename(
            filetypes=[("数据文件", "*.txt *.log *.csv *.bin"), ("所有文件", "*.*")]
        )
        if path:
            self.file_path_var.set(path)

    def _parse_file(self):
        path = self.file_path_var.get().strip()
        if not path:
            messagebox.showwarning("未选择文件", "请先选择数据文件")
            return
        if not os.path.exists(path):
            messagebox.showerror("文件不存在", f"找不到文件: {path}")
            return

        try:
            # 更新系数
            try:
                if self.serial_tab:
                    self.parser.voltage_coeff = float(self.serial_tab.v_coeff_entry.get())
                    self.parser.current_coeff = float(self.serial_tab.i_coeff_entry.get())
            except:
                pass

            self.result_text.delete('1.0', tk.END)

            # 判断文件类型
            ext = os.path.splitext(path)[1].lower()

            if ext == '.bin':
                # 二进制文件
                with open(path, 'rb') as f:
                    raw = f.read()
                buffer = bytearray(raw)
                raw_packets = self.parser.extract_packets(buffer)
                results = [self.parser.parse_packet(p) for p in raw_packets]
                results = [r for r in results if r]
            else:
                # 文本文件（十六进制）
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                results = self.parser.feed_hex_text(content)

            if not results:
                self.result_text.insert(tk.END, "未找到有效的 HLW8032 数据包。\n"
                                               "请确保文件包含十六进制 HLW8032 数据帧（24字节/帧）。\n")
                self.stats_label.config(text="未找到有效数据包")
                return

            # 清空旧数据
            if self.clear_first_var.get():
                self.storage.clear_samples()

            # 写入结果并记录到存储
            self.result_text.insert(tk.END, f"{'序号':>4}  {'时间':>8}  {'电压(V)':>10}  {'电流(A)':>10}  {'功率(W)':>10}  {'PF':>6}  {'状态':>6}\n")
            self.result_text.insert(tk.END, "-" * 80 + "\n")

            for i, r in enumerate(results, 1):
                dt = datetime.fromtimestamp(r.timestamp).strftime('%H:%M:%S')
                self.result_text.insert(tk.END,
                    f"{i:>4}  {dt:>8}  {r.voltage:>10.2f}  {r.current:>10.4f}  "
                    f"{r.power:>10.3f}  {r.power_factor:>6.3f}  0x{r.state:>02X}\n"
                )
                # 记录到存储
                self.storage.record_sample(
                    voltage=r.voltage, current=r.current, power=r.power,
                    power_factor=r.power_factor, timestamp=r.timestamp
                )

            # 统计
            avg_v = sum(r.voltage for r in results) / len(results)
            avg_i = sum(r.current for r in results) / len(results)
            avg_p = sum(r.power for r in results) / len(results)

            self.result_text.insert(tk.END, "-" * 80 + "\n")
            self.result_text.insert(tk.END,
                f"{'平均':>4}  {'':>8}  {avg_v:>10.2f}  {avg_i:>10.4f}  {avg_p:>10.3f}\n\n"
            )
            self.stats_label.config(
                text=f"解析完成：{len(results)} 个有效数据包 | "
                     f"平均 V={avg_v:.2f}V I={avg_i:.4f}A P={avg_p:.3f}W"
            )

            # 通知主窗口
            self.on_sample(None)

        except Exception as e:
            messagebox.showerror("解析错误", str(e))


# ============================================================
# 设备分析标签页
# ============================================================

class DeviceAnalysisTab(ttk.Frame):
    """用电设备识别分析页面"""

    def __init__(self, parent, storage: DataStorage):
        super().__init__(parent)
        self.storage = storage
        self._build_ui()

    def _build_ui(self):
        # 标题
        title_bar = ttk.Frame(self)
        title_bar.pack(fill=tk.X, padx=10, pady=(10, 5))

        ttk.Label(title_bar, text="用电设备识别分析", style='Title.TLabel').pack(side=tk.LEFT)
        ttk.Button(title_bar, text="刷新分析", command=self.refresh).pack(side=tk.RIGHT, padx=5)

        # 分析参数
        params_frame = tk.Frame(self, bg=COLORS["bg"])
        params_frame.pack(fill=tk.X, padx=10, pady=5)

        tk.Label(params_frame, text="分析窗口:", bg=COLORS["bg"], fg=COLORS["fg2"],
                 font=('Microsoft YaHei', 9)).pack(side=tk.LEFT, padx=5)
        self.window_var = tk.StringVar(value="30")
        window_combo = ttk.Combobox(params_frame, textvariable=self.window_var,
                                    width=5, values=["5", "10", "15", "30", "60"])
        window_combo.pack(side=tk.LEFT, padx=2)
        tk.Label(params_frame, text="分钟", bg=COLORS["bg"], fg=COLORS["fg2"],
                 font=('Microsoft YaHei', 9)).pack(side=tk.LEFT)

        # 识别结果区
        result_container = ttk.Frame(self)
        result_container.pack(fill=tk.X, padx=10, pady=8)

        # 左侧 - 识别结果
        result_left = tk.Frame(result_container, bg=COLORS["bg2"],
                               highlightbackground='#334455', highlightthickness=1)
        result_left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))

        tk.Label(result_left, text="识别结果", bg=COLORS["bg2"], fg=COLORS["accent2"],
                 font=('Microsoft YaHei', 14, 'bold')).pack(anchor=tk.W, padx=15, pady=(10, 5))
        self.device_type_label = tk.Label(result_left, text="--", bg=COLORS["bg2"],
                                          fg=COLORS["accent2"],
                                          font=('Microsoft YaHei', 22, 'bold'))
        self.device_type_label.pack(anchor=tk.W, padx=15)

        self.confidence_label = tk.Label(result_left, text="可信度: --%", bg=COLORS["bg2"],
                                         fg=COLORS["green"],
                                         font=('Microsoft YaHei', 12, 'bold'))
        self.confidence_label.pack(anchor=tk.W, padx=15, pady=(0, 5))

        self.summary_label = tk.Label(result_left, text="--", bg=COLORS["bg2"],
                                      fg=COLORS["fg"], font=('Microsoft YaHei', 10),
                                      wraplength=280, justify=tk.LEFT)
        self.summary_label.pack(anchor=tk.W, padx=15, pady=(0, 10))

        # 右侧 - 候选设备
        result_right = tk.Frame(result_container, bg=COLORS["bg2"],
                                highlightbackground='#334455', highlightthickness=1)
        result_right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(5, 0))

        tk.Label(result_right, text="候选设备", bg=COLORS["bg2"], fg=COLORS["fg2"],
                 font=('Microsoft YaHei', 11, 'bold')).pack(anchor=tk.W, padx=15, pady=(10, 5))

        self.candidates_frame = tk.Frame(result_right, bg=COLORS["bg2"])
        self.candidates_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.candidate_labels = []
        for _ in range(5):
            lbl = tk.Label(self.candidates_frame, text="", bg=COLORS["bg2"],
                          fg=COLORS["fg2"], font=('Microsoft YaHei', 9),
                          anchor=tk.W)
            lbl.pack(fill=tk.X, padx=5, pady=2)
            self.candidate_labels.append(lbl)

        # 特征参数区
        features_frame = tk.Frame(self, bg=COLORS["bg2"],
                                  highlightbackground='#334455', highlightthickness=1)
        features_frame.pack(fill=tk.X, padx=10, pady=8)

        tk.Label(features_frame, text="特征参数", bg=COLORS["bg2"], fg=COLORS["fg2"],
                 font=('Microsoft YaHei', 11, 'bold')).pack(anchor=tk.W, padx=15, pady=(10, 2))

        self.features_text = tk.Text(features_frame, height=9, font=('Consolas', 9),
                                     bg=COLORS["bg"], fg=COLORS["fg2"],
                                     relief=tk.FLAT, borderwidth=0,
                                     wrap=tk.WORD)
        self.features_text.pack(fill=tk.X, padx=15, pady=(0, 10))
        self.features_text.config(state=tk.DISABLED)

        # 数据统计
        stats_frame = tk.Frame(self, bg=COLORS["bg2"],
                               highlightbackground='#334455', highlightthickness=1)
        stats_frame.pack(fill=tk.X, padx=10, pady=5)
        self.stats_text = tk.Label(stats_frame, text="采样数: 0 | 累计用电: 0.000 kWh",
                                   bg=COLORS["bg2"], fg=COLORS["fg2"],
                                   font=('Microsoft YaHei', 10))
        self.stats_text.pack(padx=15, pady=10, anchor=tk.W)

    def refresh(self):
        """刷新设备识别分析"""
        try:
            minutes = int(self.window_var.get())
        except ValueError:
            minutes = 30

        samples = self.storage.get_recent_samples(minutes)
        stats = self.storage.get_stats()

        # 更新统计
        self.stats_text.config(
            text=f"采样数: {stats['count']} (最近{minutes}分钟) | "
                 f"累计用电: {self.storage.total_kwh:.3f} kWh"
        )

        # 执行设备识别
        result = analyze(samples)

        # 更新识别结果
        self.device_type_label.config(text=result.display_name)
        self.confidence_label.config(
            text=f"可信度: {result.confidence}%",
            fg=COLORS["green"] if result.confidence >= 70 else
               (COLORS["yellow"] if result.confidence >= 40 else COLORS["fg2"])
        )
        self.summary_label.config(text=result.summary)

        # 更新候选列表
        for i, lbl in enumerate(self.candidate_labels):
            if i < len(result.candidates):
                c = result.candidates[i]
                lbl.config(text=f"{i+1}. {c.display_name}  ({c.confidence}%)",
                          fg=COLORS["green"] if c.confidence >= 70 else
                             (COLORS["yellow"] if c.confidence >= 40 else COLORS["fg2"]))
            else:
                lbl.config(text="")

        # 更新特征参数
        self.features_text.config(state=tk.NORMAL)
        self.features_text.delete('1.0', tk.END)
        for line in result.features:
            self.features_text.insert(tk.END, line + "\n")
        self.features_text.config(state=tk.DISABLED)


# ============================================================
# 主应用程序窗口
# ============================================================

class App:
    """HLW8032 电力监测分析工具 主窗口"""

    def __init__(self, root):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry(WINDOW_SIZE)
        self.root.minsize(900, 600)

        # 深色背景
        self.root.configure(bg=COLORS["bg"])

        # 数据存储
        self.storage = DataStorage(save_to_file=False)

        # 设备分析刷新定时器
        self._analysis_timer = None
        self._analysis_needs_refresh = False
        self._sample_count_at_last_analysis = 0

        self._build_ui()

    def _build_ui(self):
        # 标题栏
        title_bar = tk.Frame(self.root, bg=COLORS["bg3"], height=40)
        title_bar.pack(fill=tk.X)
        title_bar.pack_propagate(False)

        tk.Label(title_bar, text=APP_TITLE, bg=COLORS["bg3"], fg=COLORS["fg"],
                 font=('Microsoft YaHei', 14, 'bold')).pack(side=tk.LEFT, padx=15, pady=5)
        tk.Label(title_bar, text=f"v{APP_VERSION}", bg=COLORS["bg3"], fg=COLORS["fg2"],
                 font=('Microsoft YaHei', 9)).pack(side=tk.LEFT, padx=5)

        # 导出按钮
        ttk.Button(title_bar, text="导出 CSV", command=self._export_csv).pack(side=tk.RIGHT, padx=10)

        # 标签页
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # 串口监测
        self.serial_tab = SerialMonitorTab(self.notebook, self.storage, self._on_new_sample)
        self.notebook.add(self.serial_tab, text="串口监测")

        # 功率曲线
        self.curve_tab = PowerCurveCanvas(self.notebook)
        self.notebook.add(self.curve_tab, text="功率曲线")

        # 文件解析
        self.file_tab = FileParserTab(self.notebook, self.storage, self._on_new_sample, self.serial_tab)
        self.notebook.add(self.file_tab, text="文件解析")

        # 设备分析
        self.analysis_tab = DeviceAnalysisTab(self.notebook, self.storage)
        self.notebook.add(self.analysis_tab, text="设备识别")

        # 标签页切换时刷新
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # 定期刷新功率曲线和设备分析
        self._start_auto_refresh()

    def _on_new_sample(self, sample):
        """收到新采样时的回调"""
        self._analysis_needs_refresh = True

    def _on_tab_changed(self, event):
        """标签页切换时的回调"""
        tab_id = self.notebook.select()
        tab_index = self.notebook.index(tab_id)

        if tab_index == 1:  # 功率曲线
            self._refresh_curve()
        elif tab_index == 3:  # 设备识别
            self.analysis_tab.refresh()

    def _refresh_curve(self):
        """刷新功率曲线"""
        samples = self.storage.get_recent_samples(30)
        stats = self.storage.get_stats()
        title = f"功率曲线 (样本: {stats['count']}, 累计: {self.storage.total_kwh:.3f} kWh)"
        self.curve_tab.update(samples, title)

    def _start_auto_refresh(self):
        """启动自动刷新（每2秒检查功率曲线，每30秒刷新设备分析）"""
        def _loop():
            # 检查是否需要刷新（有新数据）
            current_count = self.storage.sample_count
            if current_count > self._sample_count_at_last_analysis:
                # 功率曲线在选中时刷新
                tab_id = self.notebook.select()
                tab_index = self.notebook.index(tab_id)
                if tab_index == 1:  # 功率曲线标签页
                    self._refresh_curve()

                # 设备识别每30秒或有显著新数据时刷新
                if (self._analysis_needs_refresh and
                    current_count - self._sample_count_at_last_analysis >= 5):
                    if tab_index == 3:
                        self.analysis_tab.refresh()
                    self._analysis_needs_refresh = False
                    self._sample_count_at_last_analysis = current_count

            # 串口监测页面实时更新已在 SerialMonitorTab 中处理

            self._analysis_timer = self.root.after(2000, _loop)

        self._analysis_timer = self.root.after(2000, _loop)

    def _export_csv(self):
        """导出为 CSV 文件"""
        filepath = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV 文件", "*.csv")],
            title="导出数据"
        )
        if filepath:
            path = self.storage.export_csv(filepath)
            messagebox.showinfo("导出成功", f"数据已导出到:\n{path}\n\n共 {self.storage.sample_count} 条记录")

    def on_close(self):
        """关闭窗口时的清理"""
        if self.serial_tab.running:
            self.serial_tab._stop_serial()
        if self._analysis_timer:
            self.root.after_cancel(self._analysis_timer)
        self.root.destroy()


# ============================================================
# 程序入口
# ============================================================

def main():
    """主函数"""
    setup_styles()

    # 命令行参数：直接解析文件
    if len(sys.argv) >= 2 and sys.argv[1] == '--file' and len(sys.argv) >= 3:
        filepath = sys.argv[2]
        print(f"HLW8032 文件解析模式")
        print(f"文件: {filepath}")
        results = parse_hex_file(filepath)
        if not results:
            # 尝试二进制文件
            results = parse_binary_file(filepath)
        if results:
            print(f"\n解析到 {len(results)} 个有效数据包")
            print(f"{'序号':>4}  {'电压(V)':>10}  {'电流(A)':>10}  {'功率(W)':>10}  {'PF':>6}")
            print("-" * 50)
            for i, r in enumerate(results[:50], 1):
                print(f"{i:>4}  {r.voltage:>10.2f}  {r.current:>10.4f}  "
                      f"{r.power:>10.3f}  {r.power_factor:>6.3f}")
            if len(results) > 50:
                print(f"... 还有 {len(results) - 50} 条")
            avg_v = sum(r.voltage for r in results) / len(results)
            avg_i = sum(r.current for r in results) / len(results)
            avg_p = sum(r.power for r in results) / len(results)
            print(f"\n平均: V={avg_v:.2f}V I={avg_i:.4f}A P={avg_p:.3f}W")
        else:
            print("未找到有效的 HLW8032 数据包")
        return

    # GUI 模式
    root = tk.Tk()
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)

    # 居中显示
    root.update_idletasks()
    w = root.winfo_width()
    h = root.winfo_height()
    x = (root.winfo_screenwidth() // 2) - (w // 2)
    y = (root.winfo_screenheight() // 2) - (h // 2)
    root.geometry(f"+{x}+{y}")

    root.mainloop()


if __name__ == "__main__":
    main()
