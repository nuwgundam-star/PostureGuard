"""ESP32 더미 publisher.

실제 ESP32 가 도착하기 전까지 MQTT 파이프라인을 검증하기 위한 스크립트.
postguard/fsr/data 토픽으로 16x16 압력 맵(JSON) 을 20Hz 로 publish 한다.

실행 (backend 디렉토리에서):
    venv\\Scripts\\python.exe mock_esp32.py
"""

from __future__ import annotations

import json
import signal
import sys
import time
from datetime import datetime, timezone

import numpy as np
import paho.mqtt.client as mqtt

BROKER_HOST = "localhost"
BROKER_PORT = 1883
TOPIC = "postguard/fsr/data"
RATE_HZ = 20
ROWS = 16
COLS = 16


def generate_dummy_frame() -> np.ndarray:
    # 좌우 엉덩이 하중을 가우시안 2개로 근사해 착석 압력 분포를 시뮬레이션
    y_grid, x_grid = np.meshgrid(
        np.arange(ROWS, dtype=np.float32),
        np.arange(COLS, dtype=np.float32),
        indexing="ij",
    )
    left_peak = 110.0 * np.exp(-(((x_grid - 5.5) ** 2) + ((y_grid - 10.0) ** 2)) / (2.0 * 2.1**2))
    right_peak = 115.0 * np.exp(-(((x_grid - 10.5) ** 2) + ((y_grid - 10.2) ** 2)) / (2.0 * 2.0**2))
    center_bias = 25.0 * np.exp(-(((x_grid - 8.0) ** 2) + ((y_grid - 8.5) ** 2)) / (2.0 * 3.8**2))
    noise = np.random.normal(loc=0.0, scale=1.5, size=(ROWS, COLS)).astype(np.float32)
    frame = left_peak + right_peak + center_bias + noise
    return np.clip(frame, 0.0, None).astype(np.float32)


_stop = False


def _handle_sigint(_signum, _frame) -> None:
    global _stop
    _stop = True


def main() -> int:
    signal.signal(signal.SIGINT, _handle_sigint)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="mock_esp32")
    client.connect(BROKER_HOST, BROKER_PORT, keepalive=30)
    client.loop_start()
    print(f"[mock_esp32] {BROKER_HOST}:{BROKER_PORT} -> {TOPIC} @{RATE_HZ}Hz (Ctrl+C 로 종료)")

    interval = 1.0 / RATE_HZ
    sent = 0
    next_tick = time.monotonic()
    try:
        while not _stop:
            frame = generate_dummy_frame()
            payload = json.dumps(
                {
                    "pressure_map": frame.tolist(),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
            client.publish(TOPIC, payload, qos=0)
            sent += 1
            if sent % RATE_HZ == 0:
                print(f"[mock_esp32] published {sent} frames")

            next_tick += interval
            sleep_for = next_tick - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)
            else:
                # 지연 누적 시 타이밍 재기준화
                next_tick = time.monotonic()
    finally:
        client.loop_stop()
        client.disconnect()
        print(f"[mock_esp32] 종료 (총 {sent} frame)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
