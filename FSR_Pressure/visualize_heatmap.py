"""
16x16 FSR 매트릭스 실시간 압력 히트맵 시각화
ESP32에서 "F,v0,v1,...,v255\n" 형식으로 전송되는 데이터를 받아 시각화한다.

사용 전 준비:
    pip install pyserial matplotlib numpy

VS Code에서 실행: 이 파일을 열고 우측 상단 Run 버튼 또는 터미널에서
    python visualize_heatmap.py
"""

import serial
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.animation as animation

# ---------------- 사용자 설정 ----------------
PORT = "COM5"     # 실제 포트로 변경 (Windows: COM5 등 / macOS,Linux: /dev/ttyUSB0, /dev/cu.usbserial-XXXX 등)
BAUD = 921600     # 펌웨어의 Serial.begin() 값과 반드시 일치
ROWS, COLS = 16, 16

FLIP_UD = False  # 상하가 반대로 보이면 True로 변경
FLIP_LR = True   # 좌우가 반대로 보이면 True로 변경

# ---------------- 시리얼 초기화 ----------------
ser = serial.Serial(PORT, BAUD, timeout=1)
ser.reset_input_buffer()

fig, ax = plt.subplots(figsize=(6, 6))
data = np.zeros((ROWS, COLS))
im = ax.imshow(data, cmap="inferno", vmin=0, vmax=4095, origin="upper")
cbar = plt.colorbar(im, ax=ax)
cbar.set_label("ADC raw value (0~4095)")
ax.set_title("FSR 16x16 실시간 압력 히트맵")
ax.set_xlabel("Column")
ax.set_ylabel("Row")


def read_frame():
    """'F,'로 시작하는 한 프레임(256개 값)을 읽어 16x16 배열로 반환"""
    while True:
        raw = ser.readline().decode(errors="ignore").strip()
        if not raw.startswith("F,"):
            continue
        values = raw[2:].split(",")
        if len(values) != ROWS * COLS:
            continue  # 손상된 프레임은 버리고 다음 줄 시도
        try:
            arr = np.array(values, dtype=int).reshape(ROWS, COLS)
        except ValueError:
            continue
        return arr


def update(_frame):
    arr = read_frame()
    if FLIP_UD:
        arr = np.flipud(arr)
    if FLIP_LR:
        arr = np.fliplr(arr)
    im.set_data(arr)
    
  
   
    return [im]


ani = animation.FuncAnimation(fig, update, interval=30, blit=True, cache_frame_data=False)
plt.tight_layout()
plt.show()

ser.close()
