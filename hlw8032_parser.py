"""
HLW8032 电量计量芯片数据解析模块

HLW8032 是合力为推出的单相电能计量芯片，内置 24 位 Σ-Δ 型 ADC，
通过 UART (4800bps, 8N1) 以 24 字节固定帧格式输出数据。

本模块实现了与 ESP32 固件完全一致的解析算法：
  - 校验和验证（字节2~22 累加取低8位 == 字节23）
  - 状态位解析（判断电压/电流/功率数据是否有效）
  - 系数校准（电压系数、电流系数可调）

数据帧格式（24 字节）：
  [0]    状态寄存器 (0x55=正常, 0xFx=异常, bit3=电压异常, bit2=电流异常, bit1=功率异常)
  [1]    检测寄存器 (固定 0x5A)
  [2:4]  电压参数寄存器 (24bit, 大端)
  [5:7]  电压寄存器     (24bit, 大端)
  [8:10] 电流参数寄存器 (24bit, 大端)
  [11:13]电流寄存器     (24bit, 大端)
  [14:16]功率参数寄存器 (24bit, 大端)
  [17:19]功率寄存器     (24bit, 大端)
  [20]   数据更新标志
  [21:22]校验寄存器 (16bit)
  [23]   校验和 (字节2~22 累加取低8位)

计算公式：
  电压(V) = (电压参数 / 电压寄存器) × 电压系数
  电流(A) = (电流参数 / 电流寄存器) × 电流系数
  功率(W) = (功率参数 / 功率寄存器) × 电压系数 × 电流系数

参考文档：HLW8032 数据手册 v1.3
"""

import re
import time
from dataclasses import dataclass, field
from typing import Optional


# ============================================================
# 常量定义
# ============================================================

FRAME_SIZE = 24
DEFAULT_VOLTAGE_COEFF = 1.88
DEFAULT_CURRENT_COEFF = 1.0

# 数据有效性阈值
MAX_VALID_VOLTAGE = 500.0   # 电压上限 (V)
MAX_VALID_CURRENT = 100.0   # 电流上限 (A)
MAX_VALID_POWER = 25000.0   # 功率上限 (W)


# ============================================================
# 解析结果数据结构
# ============================================================

@dataclass
class HLW8032Sample:
    """单次 HLW8032 采样数据"""
    timestamp: float = 0.0
    voltage: float = 0.0
    current: float = 0.0
    power: float = 0.0
    apparent_power: float = 0.0
    power_factor: float = 0.0
    state: int = 0
    update_flag: int = 0
    raw_packet: bytes = field(default_factory=bytes)
    is_valid: bool = False


# ============================================================
# HLW8032 数据解析器
# ============================================================

class HLW8032Parser:
    """HLW8032 芯片数据解析器
    
    支持两种输入模式：
    1. 二进制模式：直接接收 ESP32 串口透传的原始字节流
    2. 十六进制文本模式：解析文本中的十六进制字符串（如 "55 5A ..."）
    
    Usage:
        parser = HLW8032Parser(voltage_coeff=1.88, current_coeff=1.0)
        
        # 二进制解析
        buffer = bytearray()
        buffer.extend(serial_data)
        samples = parser.feed_binary(buffer)
        
        # 十六进制文本解析
        samples = parser.feed_hex_text("55 5A 02 98 88 ...")
    """

    def __init__(self, voltage_coeff: float = DEFAULT_VOLTAGE_COEFF,
                 current_coeff: float = DEFAULT_CURRENT_COEFF):
        self.voltage_coeff = voltage_coeff
        self.current_coeff = current_coeff
        self._text_buffer = ""

    # ----------------------------------------------------------
    # 底层数据读取
    # ----------------------------------------------------------

    @staticmethod
    def read_u24(data: bytes, offset: int) -> int:
        """读取 24 位大端无符号整数"""
        return (data[offset] << 16) | (data[offset + 1] << 8) | data[offset + 2]

    # ----------------------------------------------------------
    # 数据包验证
    # ----------------------------------------------------------

    @staticmethod
    def is_valid_packet(packet: bytes) -> bool:
        """验证 HLW8032 数据包是否有效
        
        检查项：
        1. 长度 == 24 字节
        2. 字节[1] == 0x5A（检测寄存器固定值）
        3. 字节[0] == 0x55（正常状态）或高4位 == 0xF（异常状态）
        4. 校验和：字节[2]~[22] 累加取低8位 == 字节[23]
        """
        if len(packet) != FRAME_SIZE:
            return False
        if packet[1] != 0x5A:
            return False

        state = packet[0]
        if state != 0x55 and (state & 0xF0) != 0xF0:
            return False

        checksum = sum(packet[2:23]) & 0xFF
        return checksum == packet[23]

    # ----------------------------------------------------------
    # 单包解析
    # ----------------------------------------------------------

    def parse_packet(self, packet: bytes) -> Optional[HLW8032Sample]:
        """解析单个 24 字节数据包
        
        Args:
            packet: 24 字节原始数据包
            
        Returns:
            HLW8032Sample 对象，解析失败返回 None
        """
        if not self.is_valid_packet(packet):
            return None

        state = packet[0]
        is_normal = (state == 0x55)

        # 读取寄存器值
        voltage_param = self.read_u24(packet, 2)
        voltage_reg   = self.read_u24(packet, 5)
        current_param = self.read_u24(packet, 8)
        current_reg   = self.read_u24(packet, 11)
        power_param   = self.read_u24(packet, 14)
        power_reg     = self.read_u24(packet, 17)

        voltage = 0.0
        current = 0.0
        power   = 0.0

        # 电压：正常状态 或 非电压异常位 (bit3=0)
        if is_normal or not (state & 0x08):
            if voltage_reg == 0:
                return None
            voltage = (voltage_param / voltage_reg) * self.voltage_coeff

        # 电流：正常状态 或 非电流异常位 (bit2=0)
        if is_normal or not (state & 0x04):
            if current_reg == 0:
                return None
            current = (current_param / current_reg) * self.current_coeff

        # 功率：正常状态 或 非功率异常位 (bit1=0)
        if is_normal or not (state & 0x02):
            if power_reg == 0:
                return None
            power = (power_param / power_reg) * self.voltage_coeff * self.current_coeff

        # 合理性检查
        if (voltage < 0 or voltage > MAX_VALID_VOLTAGE or
            current < 0 or current > MAX_VALID_CURRENT or
            power < 0   or power > MAX_VALID_POWER):
            return None

        # 计算衍生量
        apparent_power = voltage * current
        power_factor = power / apparent_power if apparent_power > 0 else 0.0
        power_factor = max(0.0, min(1.0, power_factor))

        return HLW8032Sample(
            timestamp=time.time(),
            voltage=voltage,
            current=current,
            power=power,
            apparent_power=apparent_power,
            power_factor=power_factor,
            state=state,
            update_flag=packet[20],
            raw_packet=bytes(packet),
            is_valid=True
        )

    # ----------------------------------------------------------
    # 从字节流中提取数据包
    # ----------------------------------------------------------

    def extract_packets(self, buffer: bytearray) -> list:
        """从字节缓冲区中提取所有有效数据包
        
        采用滑动窗口扫描，找到有效帧后跳过 24 字节继续搜索。
        处理完后清理已消费的字节，只保留尾部不足一帧的残留数据。
        
        Args:
            buffer: 可变字节缓冲区（会被原地修改）
            
        Returns:
            提取到的原始数据包列表 (list[bytes])
        """
        packets = []
        i = 0
        buf_len = len(buffer)

        while i + FRAME_SIZE <= buf_len:
            packet = buffer[i:i + FRAME_SIZE]
            if self.is_valid_packet(packet):
                packets.append(bytes(packet))
                i += FRAME_SIZE
            else:
                i += 1

        # 清理已消费的字节
        del buffer[:i]
        # 保留尾部残留（不足24字节），但限制最大缓冲区
        if len(buffer) > FRAME_SIZE * 4:
            del buffer[:len(buffer) - FRAME_SIZE + 1]

        return packets

    # ----------------------------------------------------------
    # 十六进制文本解析
    # ----------------------------------------------------------

    def feed_hex_text(self, text: str) -> list:
        """解析十六进制文本中的 HLW8032 数据
        
        适用于：
        - 粘贴串口调试助手的十六进制输出
        - 解析日志文件中的十六进制数据
        - 文件导入（.txt / .log）
        
        Args:
            text: 包含十六进制字节的文本字符串
            
        Returns:
            解析出的 HLW8032Sample 列表
        """
        hex_values = [int(x, 16) for x in re.findall(r'\b[0-9A-Fa-f]{2}\b', text)]
        buffer = bytearray(hex_values)
        raw_packets = self.extract_packets(buffer)
        return [s for p in raw_packets if (s := self.parse_packet(p)) is not None]

    # ----------------------------------------------------------
    # 二进制数据解析（串口实时输入）
    # ----------------------------------------------------------

    def feed_binary(self, buffer: bytearray) -> list:
        """处理串口接收的二进制数据
        
        Args:
            buffer: 累积的字节缓冲区（会被原地修改）
            
        Returns:
            解析出的 HLW8032Sample 列表
        """
        raw_packets = self.extract_packets(buffer)
        return [s for p in raw_packets if (s := self.parse_packet(p)) is not None]


# ============================================================
# 便捷函数
# ============================================================

def parse_hex_file(filepath: str, voltage_coeff: float = DEFAULT_VOLTAGE_COEFF,
                   current_coeff: float = DEFAULT_CURRENT_COEFF) -> list:
    """解析十六进制数据文件"""
    parser = HLW8032Parser(voltage_coeff, current_coeff)
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        return parser.feed_hex_text(content)
    except FileNotFoundError:
        print(f"文件不存在: {filepath}")
        return []
    except Exception as e:
        print(f"解析文件失败: {e}")
        return []


def parse_binary_file(filepath: str, voltage_coeff: float = DEFAULT_VOLTAGE_COEFF,
                      current_coeff: float = DEFAULT_CURRENT_COEFF) -> list:
    """解析二进制数据文件"""
    parser = HLW8032Parser(voltage_coeff, current_coeff)
    try:
        with open(filepath, 'rb') as f:
            raw = f.read()
        buffer = bytearray(raw)
        raw_packets = parser.extract_packets(buffer)
        return [s for p in raw_packets if (s := parser.parse_packet(p)) is not None]
    except FileNotFoundError:
        print(f"文件不存在: {filepath}")
        return []
    except Exception as e:
        print(f"解析文件失败: {e}")
        return []
