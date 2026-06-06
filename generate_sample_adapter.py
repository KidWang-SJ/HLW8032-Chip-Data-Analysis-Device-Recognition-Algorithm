"""Generate simulated HLW8032 hex data - Power Adapter (65W laptop charger)"""
import random
import sys
sys.path.insert(0, r"c:\Users\Mr. Wang\Desktop\ESP32\hlw8032-power-analyzer")

# 65W laptop adapter: V≈220V, I≈0.54A, P≈65W, PF≈0.55
# Features: moderate power, low PF, some variation

NUM_FRAMES = 60

def make_frame(voltage, current, power):
    V_PARAM = 0x0124F8
    I_PARAM = 0x000028   # small current param for low current
    P_PARAM = 0x000FA0   # small power param

    V_REG = int(V_PARAM * 1.88 / voltage)
    I_REG = int(I_PARAM / current) if current > 0.001 else 1
    P_REG = int(P_PARAM * 1.88 / power) if power > 0.1 else 1

    V_REG = max(1, min(0xFFFFFF, V_REG))
    I_REG = max(1, min(0xFFFFFF, I_REG))
    P_REG = max(1, min(0xFFFFFF, P_REG))

    packet = bytearray(24)
    packet[0]  = 0x55
    packet[1]  = 0x5A

    packet[2]  = (V_PARAM >> 16) & 0xFF
    packet[3]  = (V_PARAM >> 8)  & 0xFF
    packet[4]  = V_PARAM & 0xFF
    packet[5]  = (V_REG >> 16) & 0xFF
    packet[6]  = (V_REG >> 8)  & 0xFF
    packet[7]  = V_REG & 0xFF

    packet[8]  = (I_PARAM >> 16) & 0xFF
    packet[9]  = (I_PARAM >> 8)  & 0xFF
    packet[10] = I_PARAM & 0xFF
    packet[11] = (I_REG >> 16) & 0xFF
    packet[12] = (I_REG >> 8)  & 0xFF
    packet[13] = I_REG & 0xFF

    packet[14] = (P_PARAM >> 16) & 0xFF
    packet[15] = (P_PARAM >> 8)  & 0xFF
    packet[16] = P_PARAM & 0xFF
    packet[17] = (P_REG >> 16) & 0xFF
    packet[18] = (P_REG >> 8)  & 0xFF
    packet[19] = P_REG & 0xFF

    packet[20] = random.randint(0, 255)
    packet[21] = 0x00
    packet[22] = 0x00

    cs = sum(packet[2:23]) & 0xFF
    packet[23] = cs

    return bytes(packet)

frames = []
for i in range(NUM_FRAMES):
    # Power adapter: moderate load with some fluctuation
    v = 221.5 + random.gauss(0, 0.6)
    # Current varies between 0.40-0.65A (simulating adapter load changes)
    i_base = 0.52 + 0.06 * (1 if i < 20 else -1 if i > 40 else 0)
    i_val = i_base + random.gauss(0, 0.03)
    # Low PF ~0.55-0.65 typical for small SMPS
    pf = 0.55 + random.gauss(0, 0.03)
    pf = max(0.45, min(0.70, pf))
    p = v * i_val * pf
    frame = make_frame(v, i_val, p)
    frames.append(frame)

# Write hex text file
output_path = r"c:\Users\Mr. Wang\Desktop\ESP32\hlw8032-power-analyzer\sample_data_adapter.txt"
with open(output_path, 'w') as f:
    f.write("# HLW8032 模拟数据 - 电源适配器/开关电源 (65W 笔记本适配器)\n")
    f.write("# 共 %d 帧，每帧24字节\n\n" % NUM_FRAMES)
    for i, frame in enumerate(frames):
        hex_str = ' '.join(f'{b:02X}' for b in frame)
        cs = sum(frame[2:23]) & 0xFF
        assert cs == frame[23], f"Frame {i} checksum error"
        f.write(hex_str + '\n')

# Also binary
bin_path = r"c:\Users\Mr. Wang\Desktop\ESP32\hlw8032-power-analyzer\sample_data_adapter.bin"
with open(bin_path, 'wb') as f:
    for frame in frames:
        f.write(frame)

print(f"Generated {NUM_FRAMES} HLW8032 frames (Power Adapter)")
print(f"Text: {output_path}")
print(f"Binary: {bin_path}")

# Self-verify
from hlw8032_parser import HLW8032Parser
parser = HLW8032Parser()
results = parser.feed_hex_text(open(output_path).read())
print(f"\nSelf-verify: parsed {len(results)} valid samples")
for i, r in enumerate(results[:5]):
    print(f"  #{i+1}: V={r.voltage:.2f}V I={r.current:.4f}A P={r.power:.2f}W PF={r.power_factor:.3f}")
avg_v = sum(r.voltage for r in results) / len(results)
avg_i = sum(r.current for r in results) / len(results)
avg_p = sum(r.power for r in results) / len(results)
avg_pf = sum(r.power_factor for r in results) / len(results)
print(f"  Avg: V={avg_v:.2f}V I={avg_i:.4f}A P={avg_p:.2f}W PF={avg_pf:.3f}")
