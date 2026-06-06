"""
数据存储与管理模块

提供采样数据的存储、查询和累积用电量计算功能。
默认内存存储，可选 CSV 文件持久化。

功能：
  - record_sample(): 记录每次采样（含电压、电流、功率、功率因数）
  - get_recent_samples(): 获取最近 N 分钟的采样数据
  - get_samples_since(): 获取指定时间戳之后的采样数据
  - clear_samples(): 清空采样数据
  - 累积用电量计算（kWh）
"""

import csv
import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional


# ============================================================
# 配置常量
# ============================================================

MAX_SAMPLES = 900            # 最大采样数（约30分钟 @ 2s间隔）
DEFAULT_SAVE_DIR = os.path.join(os.path.expanduser("~"), "HLW8032_Data")


# ============================================================
# 数据结构
# ============================================================

@dataclass
class PowerSample:
    """单次功率采样记录"""
    timestamp: float = 0.0
    voltage: float = 0.0
    current: float = 0.0
    power: float = 0.0
    apparent_power: float = 0.0
    power_factor: float = 0.0

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "voltage": self.voltage,
            "current": self.current,
            "power": self.power,
            "apparent_power": self.apparent_power,
            "power_factor": self.power_factor
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PowerSample":
        return cls(
            timestamp=float(d.get("timestamp", 0)),
            voltage=float(d.get("voltage", 0)),
            current=float(d.get("current", 0)),
            power=float(d.get("power", 0)),
            apparent_power=float(d.get("apparent_power", 0)),
            power_factor=float(d.get("power_factor", 0))
        )


@dataclass
class DailyRecord:
    """每日用电记录"""
    date_key: str = ""
    total_kwh: float = 0.0


# ============================================================
# 数据存储管理器
# ============================================================

class DataStorage:
    """采样数据存储管理器
    
    功能：
    - 内存中维护最近 MAX_SAMPLES 条采样
    - 自动计算累积用电量
    - 可选 CSV 导出/导入
    
    Usage:
        storage = DataStorage()
        storage.record_sample(voltage=220.0, current=1.5, power=330.0)
        recent = storage.get_recent_samples(30)  # 最近30分钟
    """

    def __init__(self, save_to_file: bool = False, save_dir: str = None):
        self._samples: deque = deque(maxlen=MAX_SAMPLES)
        self._total_kwh: float = 0.0
        self._last_record_time: Optional[float] = None
        self._last_power: float = 0.0

        # 文件持久化
        self.save_to_file = save_to_file
        self.save_dir = save_dir or DEFAULT_SAVE_DIR
        if self.save_to_file:
            os.makedirs(self.save_dir, exist_ok=True)

    # ----------------------------------------------------------
    # 数据记录
    # ----------------------------------------------------------

    def record_sample(self, voltage: float, current: float, power: float,
                      power_factor: float = None, timestamp: float = None) -> PowerSample:
        """记录一次采样数据
        
        每次记录时自动计算累积用电量（梯形积分法）：
            kWh += (上次功率 + 本次功率) / 2 / 1000 * 时间差(小时)
        
        Args:
            voltage: 电压 (V)
            current: 电流 (A)
            power: 有功功率 (W)
            power_factor: 功率因数，为None时自动计算
            timestamp: Unix时间戳，为None时取当前时间
            
        Returns:
            记录的 PowerSample 对象
        """
        now = timestamp or time.time()

        # 计算功率因数
        if power_factor is None:
            apparent = voltage * current
            pf = power / apparent if apparent > 0 else 0.0
            pf = max(0.0, min(1.0, pf))
        else:
            pf = power_factor
            apparent = power / pf if pf > 0 else voltage * current

        # 累积用电量计算（梯形法）
        if self._last_record_time is not None:
            time_diff_hours = (now - self._last_record_time) / 3600.0
            if time_diff_hours > 0 and time_diff_hours < 24:
                avg_power = (self._last_power + power) / 2.0
                kwh = (avg_power / 1000.0) * time_diff_hours
                self._total_kwh += kwh

        self._last_record_time = now
        self._last_power = power

        sample = PowerSample(
            timestamp=now,
            voltage=voltage,
            current=current,
            power=power,
            apparent_power=apparent,
            power_factor=pf
        )
        self._samples.append(sample)

        # 自动保存到 CSV
        if self.save_to_file:
            self._append_to_csv(sample)

        return sample

    # ----------------------------------------------------------
    # 数据查询
    # ----------------------------------------------------------

    def get_recent_samples(self, minutes: float = 30) -> list:
        """获取最近 N 分钟内的采样数据
        
        Args:
            minutes: 时间窗口（分钟）
            
        Returns:
            PowerSample 列表，按时间升序排列
        """
        cutoff = time.time() - minutes * 60.0
        return [s for s in self._samples if s.timestamp >= cutoff]

    def get_samples_since(self, start_time: float) -> list:
        """获取指定时间戳之后的采样数据
        
        Args:
            start_time: 起始 Unix 时间戳
            
        Returns:
            PowerSample 列表
        """
        if not start_time:
            return []
        return [s for s in self._samples if s.timestamp >= start_time]

    def get_all_samples(self) -> list:
        """获取所有采样数据"""
        return list(self._samples)

    def get_latest_sample(self) -> Optional[PowerSample]:
        """获取最新一条采样"""
        return self._samples[-1] if self._samples else None

    # ----------------------------------------------------------
    # 统计信息
    # ----------------------------------------------------------

    @property
    def sample_count(self) -> int:
        """当前采样数"""
        return len(self._samples)

    @property
    def total_kwh(self) -> float:
        """累积用电量 (kWh)"""
        return self._total_kwh

    def get_stats(self) -> dict:
        """获取当前数据统计摘要"""
        samples = list(self._samples)
        if not samples:
            return {
                "count": 0,
                "total_kwh": 0.0,
                "latest_v": 0.0,
                "latest_i": 0.0,
                "latest_p": 0.0
            }

        latest = samples[-1]
        return {
            "count": len(samples),
            "total_kwh": self._total_kwh,
            "latest_v": latest.voltage,
            "latest_i": latest.current,
            "latest_p": latest.power
        }

    # ----------------------------------------------------------
    # 数据管理
    # ----------------------------------------------------------

    def clear_samples(self):
        """清空所有采样数据"""
        self._samples.clear()
        self._last_record_time = None
        self._last_power = 0.0
        # 注意：不清除累积用电量

    def reset_all(self):
        """完全重置（包括用电量）"""
        self._samples.clear()
        self._total_kwh = 0.0
        self._last_record_time = None
        self._last_power = 0.0

    # ----------------------------------------------------------
    # CSV 文件持久化
    # ----------------------------------------------------------

    def _get_csv_path(self) -> str:
        """获取当天 CSV 文件路径"""
        date_str = time.strftime("%Y-%m-%d")
        return os.path.join(self.save_dir, f"hlw8032_{date_str}.csv")

    def _append_to_csv(self, sample: PowerSample):
        """追加一条采样到 CSV 文件"""
        csv_path = self._get_csv_path()
        file_exists = os.path.exists(csv_path)

        try:
            with open(csv_path, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow([
                        "timestamp", "datetime", "voltage_V", "current_A",
                        "power_W", "apparent_power_VA", "power_factor"
                    ])
                dt = time.strftime("%Y-%m-%d %H:%M:%S",
                                   time.localtime(sample.timestamp))
                writer.writerow([
                    f"{sample.timestamp:.3f}", dt,
                    f"{sample.voltage:.2f}", f"{sample.current:.4f}",
                    f"{sample.power:.3f}", f"{sample.apparent_power:.2f}",
                    f"{sample.power_factor:.3f}"
                ])
        except Exception as e:
            print(f"CSV 保存失败: {e}")

    def export_csv(self, filepath: str = None, samples: list = None) -> str:
        """导出采样数据为 CSV 文件
        
        Args:
            filepath: 导出路径，为None时自动生成
            samples: 要导出的数据，为None时导出全部
            
        Returns:
            导出文件路径
        """
        if filepath is None:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            filepath = os.path.join(self.save_dir, f"hlw8032_export_{timestamp}.csv")

        data = samples if samples is not None else list(self._samples)
        os.makedirs(os.path.dirname(filepath) or '.', exist_ok=True)

        with open(filepath, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp", "datetime", "voltage_V", "current_A",
                "power_W", "apparent_power_VA", "power_factor"
            ])
            for s in data:
                dt = time.strftime("%Y-%m-%d %H:%M:%S",
                                   time.localtime(s.timestamp))
                writer.writerow([
                    f"{s.timestamp:.3f}", dt,
                    f"{s.voltage:.2f}", f"{s.current:.4f}",
                    f"{s.power:.3f}", f"{s.apparent_power:.2f}",
                    f"{s.power_factor:.3f}"
                ])

        return filepath

    def import_csv(self, filepath: str) -> int:
        """从 CSV 文件导入数据
        
        Returns:
            导入的采样数
        """
        count = 0
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    sample = PowerSample.from_dict(row)
                    self._samples.append(sample)
                    count += 1
        except Exception as e:
            print(f"CSV 导入失败: {e}")
        return count
