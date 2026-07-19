from __future__ import annotations

import asyncio
import json

import numpy as np
import serial
from serial import SerialException

from app.core.config import settings


class FSRSerialReader:
    def __init__(self, port: str | None = None, baud_rate: int | None = None) -> None:
        # 기본 포트/보드레이트는 설정값을 사용한다.
        self.port: str = port or settings.serial_port
        self.baud_rate: int = baud_rate or settings.baud_rate
        self._serial: serial.Serial | None = None
        self.is_dummy_mode: bool = False
        self.rows: int = settings.fsr_rows
        self.cols: int = settings.fsr_cols

    async def connect(self) -> None:
        # 실제 시리얼 연결에 실패하면 자동으로 더미 모드로 전환한다.
        try:
            self._serial = await asyncio.to_thread(
                serial.Serial,
                self.port,
                self.baud_rate,
                timeout=0.1,
            )
            self.is_dummy_mode = False
        except (SerialException, OSError):
            self._serial = None
            self.is_dummy_mode = True

    async def read_frame(self) -> np.ndarray:
        # 더미 모드에서는 항상 동일 구조(16x16)의 시뮬레이션 데이터를 반환한다.
        if self.is_dummy_mode or self._serial is None or not self._serial.is_open:
            return self.generate_dummy_frame()

        raw_line: str = await asyncio.to_thread(self._serial.readline)
        decoded: str = raw_line.decode("utf-8", errors="ignore").strip()
        if not decoded:
            return self.generate_dummy_frame()

        parsed_values: list[float] | None = self._parse_line(decoded)
        if parsed_values is None or len(parsed_values) != self.rows * self.cols:
            return self.generate_dummy_frame()

        frame: np.ndarray = np.asarray(parsed_values, dtype=np.float32).reshape(self.rows, self.cols)
        return frame

    def generate_dummy_frame(self) -> np.ndarray:
        # 좌우 엉덩이 하중을 가우시안 2개로 근사해 착석 압력 분포를 시뮬레이션한다.
        y_grid, x_grid = np.meshgrid(
            np.arange(self.rows, dtype=np.float32),
            np.arange(self.cols, dtype=np.float32),
            indexing="ij",
        )

        left_peak = 110.0 * np.exp(-(((x_grid - 5.5) ** 2) + ((y_grid - 10.0) ** 2)) / (2.0 * 2.1**2))
        right_peak = 115.0 * np.exp(-(((x_grid - 10.5) ** 2) + ((y_grid - 10.2) ** 2)) / (2.0 * 2.0**2))
        center_bias = 25.0 * np.exp(-(((x_grid - 8.0) ** 2) + ((y_grid - 8.5) ** 2)) / (2.0 * 3.8**2))
        noise = np.random.normal(loc=0.0, scale=1.5, size=(self.rows, self.cols)).astype(np.float32)

        frame: np.ndarray = left_peak + right_peak + center_bias + noise
        frame = np.clip(frame, 0.0, None).astype(np.float32)
        return frame

    async def disconnect(self) -> None:
        # 연결된 시리얼 리소스를 안전하게 해제한다.
        if self._serial and self._serial.is_open:
            await asyncio.to_thread(self._serial.close)
        self._serial = None

    def _parse_line(self, line: str) -> list[float] | None:
        # JSON 배열 또는 CSV 문자열 둘 다 수신 가능하도록 파싱한다.
        try:
            payload = json.loads(line)
            if isinstance(payload, list):
                return [float(v) for v in payload]
            if isinstance(payload, dict) and isinstance(payload.get("values"), list):
                return [float(v) for v in payload["values"]]
        except json.JSONDecodeError:
            pass
        except (TypeError, ValueError):
            return None

        try:
            return [float(token) for token in line.split(",")]
        except ValueError:
            return None
