"""E2E 검증용 1회성 WebSocket 클라이언트.

ws://localhost:8000/ws/posture 에 접속 -> 3개 메시지 수신 후 종료.
각 메시지에서 pressure_map shape, cop, risk.level 을 출력한다.

ConnectionManager 는 두 종류 페이로드를 broadcast 할 수 있다:
  - PostureFrame: pressure.pressure_map / cop / risk{level,score,reasons}
  - MQTT subscriber: source="mqtt", pressure_map(top-level), timestamp
스크립트는 두 형식 모두 처리한다.
"""

from __future__ import annotations

import asyncio
import json

import websockets

WS_URL = "ws://127.0.0.1:8000/ws/posture"
TARGET_COUNT = 3


def _pressure_shape(data: dict) -> tuple[int, int] | None:
    # PostureFrame 형식 우선 시도, 아니면 최상위 pressure_map 사용
    pressure = data.get("pressure")
    if isinstance(pressure, dict):
        pmap = pressure.get("pressure_map")
    else:
        pmap = data.get("pressure_map")
    if not isinstance(pmap, list) or not pmap or not isinstance(pmap[0], list):
        return None
    return (len(pmap), len(pmap[0]))


def _summarize_cop(data: dict) -> str:
    cop = data.get("cop")
    if not isinstance(cop, dict):
        return "n/a"
    cop_x = cop.get("cop_x")
    cop_y = cop.get("cop_y")
    total = cop.get("total_pressure")
    return f"(x={cop_x:.3f}, y={cop_y:.3f}, total={total:.1f})"


def _risk_level(data: dict) -> str:
    risk = data.get("risk")
    if not isinstance(risk, dict):
        return "n/a"
    level = risk.get("level")
    return str(level)


async def main() -> int:
    print(f"[client] connecting to {WS_URL}")
    async with websockets.connect(WS_URL) as ws:
        print("[client] connected, waiting messages...")
        for i in range(1, TARGET_COUNT + 1):
            raw = await ws.recv()
            data = json.loads(raw)
            source = data.get("source", "stream")
            shape = _pressure_shape(data)
            cop = _summarize_cop(data)
            risk = _risk_level(data)
            print(
                f"[msg {i}/{TARGET_COUNT}] source={source} "
                f"pressure_map.shape={shape} cop={cop} risk.level={risk}"
            )
    print("[client] 3 messages received, exiting OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
