"""Generate simulated HLW8032 hex data file"""
import random
import struct

# Simulate an 1800W electric kettle:
# V=220V, I=8.18A, P=1800W, PF≈0.998
# Actual HLW8032 register ratios:
#   Voltage ratio  ≈ 117.0 (for 220V / 1.88 coeff)
#   Current ratio  ≈ 8.18  (for 8.18A / 1.0 coeff)
#   Power ratio    ≈ 957.4 (for 1800W / 1.88 coeff)

NUM_FRAMES = 60

def make_frame(voltage, current, power, pf):
    """Build a 24-byte HLW8032 frame"""
    # Fixed register values (typical HLW8032 module)
    V_PARAM = 0x0124F8   # voltage param
    I_PARAM = 0x0005DC   # current param (1500)
    P_PARAM = 0x02A300   # power param

    # Compute registers from desired values
    # V = (V_param / V_reg) * 1.88  =>  V_reg = V_param * 1.88 / V
    # I = (I_param / I_reg) * 1.0   =>  I_reg = I_param / I
    # P = (P_param / P_reg) * 1.88  =>  P_reg = P_param * 1.88 / P
    V_REG = int(V_PARAM * 1.88 / voltage)
    I_REG = int(I_PARAM / current) if current > 0.01 else 1
    P_REG = int(P_PARAM * 1.88 / power) if power > 1 else 1

    # Clamp to valid 24-bit range
    V_REG  = max(1, min(0xFFFFFF, V_REG))
    I_REG  = max(1, min(0xFFFFFF, I_REG))
    P_REG  = max(1, min(0xFFFFFF, P_REG))

    packet = bytearray(24)
    packet[0]  = 0x55                      # state: normal
    packet[1]  = 0x5A                      # detection

    # Voltage param & reg
    packet[2]  = (V_PARAM >> 16) & 0xFF
    packet[3]  = (V_PARAM >> 8)  & 0xFF
    packet[4]  = V_PARAM & 0xFF
    packet[5]  = (V_REG >> 16) & 0xFF
    packet[6]  = (V_REG >> 8)  & 0xFF
    packet[7]  = V_REG & 0xFF

    # Current param & reg
    packet[8]  = (I_PARAM >> 16) & 0xFF
    packet[9]  = (I_PARAM >> 8)  & 0xFF
    packet[10] = I_PARAM & 0xFF
    packet[11] = (I_REG >> 16) & 0xFF
    packet[12] = (I_REG >> 8)  & 0xFF
    packet[13] = I_REG & 0xFF

    # Power param & reg
    packet[14] = (P_PARAM >> 16) & 0xFF
    packet[15] = (P_PARAM >> 8)  & 0xFF
    packet[16] = P_PARAM & 0xFF
    packet[17] = (P_REG >> 16) & 0xFF
    packet[18] = (P_REG >> 8)  & 0xFF
    packet[19] = P_REG & 0xFF

    packet[20] = random.randint(0, 255)    # update flag
    packet[21] = 0x00                       # checksum reg (high)
    packet[22] = 0x00                       # checksum reg (low)

    # checksum = sum(bytes 2-22) & 0xFF
    cs = sum(packet[2:23]) & 0xFF
    packet[23] = cs

    return bytes(packet)

# Generate frames - simulate kettle running for ~2 minutes
frames = []
for i in range(NUM_FRAMES):
    # Add small random variations to simulate real measurement noise
    v = 221.5 + random.gauss(0, 0.8)                    # ~220V ±1V
    i_val = 8.12 + random.gauss(0, 0.05)                # ~8.15A (1800W kettle)
    p = v * i_val * 0.998                               # P = V*I*PF
    frame = make_frame(v, i_val, p, 0.998)
    frames.append(frame)

# Write hex text file
output_path = r"c:\Users\Mr. Wang\Desktop\ESP32\hlw8032-power-analyzer\sample_data.txt"
with open(output_path, 'w') as f:
    f.write("# HLW8032 模拟数据 - 电热水壶 (1800W)\n")
    f.write("# 共 %d 帧，每帧24字节，4800bps UART\n\n" % NUM_FRAMES)
    for i, frame in enumerate(frames):
        hex_str = ' '.join(f'{b:02X}' for b in frame)
        # Verify checksum
        cs = sum(frame[2:23]) & 0xFF
        assert cs == frame[23], f"Frame {i} checksum error"
        f.write(hex_str + '\n')

# Also write as binary file for testing
bin_path = r"c:\Users\Mr. Wang\Desktop\ESP32\hlw8032-power-analyzer\sample_data.bin"
with open(bin_path, 'wb') as f:
    for frame in frames:
        f.write(frame)

print(f"Generated {NUM_FRAMES} HLW8032 frames")
print(f"Text file: {output_path}")
print(f"Binary file: {bin_path}")

# Verify by parsing back
import sys
sys.path.insert(0, r"c:\Users\Mr. Wang\Desktop\ESP32\hlw8032-power-analyzer")
from hlw8032_parser import HLW8032Parser

parser = HLW8032Parser()
results = parser.feed_hex_text(open(output_path).read())
print(f"\nSelf-verify: parsed {len(results)} valid samples")
for i, r in enumerate(results[:5]):
    print(f"  #{i+1}: V={r.voltage:.2f}V I={r.current:.4f}A P={r.power:.2f}W PF={r.power_factor:.3f}")
print(f"  ... (total {len(results)} samples)")
