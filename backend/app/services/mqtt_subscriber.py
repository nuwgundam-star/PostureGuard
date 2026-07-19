from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import numpy as np
from fastapi_mqtt import FastMQTT, MQTTConfig

from app.core.config import settings

if TYPE_CHECKING:
    from app.api.websocket import ConnectionManager

logger = logging.getLogger(__name__)

# ESP32 가 publish 하는 16x16 압력 데이터 토픽
FSR_DATA_TOPIC = "postguard/fsr/data"


class MQTTSubscriber:
    def __init__(self) -> None:
        # 브로커 미연결 상태에서도 서버는 살아있도록 더미 모드 플래그 유지
        self.is_dummy_mode: bool = True
        self._manager: ConnectionManager | None = None
        self._client: FastMQTT | None = None
        # 수신 카운터 (주기적으로 로그를 남겨 파이프라인 동작 가시화)
        self._frame_count: int = 0

    async def connect(self, manager: ConnectionManager) -> None:
        # 브로커 연결 후 FSR 토픽을 구독한다. 실패 시 예외는 상위 lifespan 에서 흡수.
        self._manager = manager
        client = FastMQTT(
            config=MQTTConfig(
                host=settings.mqtt_broker_host,
                port=settings.mqtt_broker_port,
                keepalive=60,
            )
        )

        @client.on_connect()
        def _on_connect(c, flags, rc, properties) -> None:
            # 연결 직후 토픽 구독 등록
            client.client.subscribe(FSR_DATA_TOPIC)
            logger.info("MQTT 브로커 연결됨 - %s 구독", FSR_DATA_TOPIC)

        @client.subscribe(FSR_DATA_TOPIC)
        async def _on_fsr_message(c, topic, payload, qos, properties) -> None:
            # 수신 페이로드를 numpy 16x16 으로 변환 후 broadcast
            await self._handle_message(payload)

        await client.mqtt_startup()
        self._client = client
        self.is_dummy_mode = False

    async def disconnect(self) -> None:
        # 브로커 연결 해제 및 클라이언트 정리
        if self._client is not None:
            try:
                await self._client.mqtt_shutdown()
            finally:
                self._client = None
        self.is_dummy_mode = True

    async def _handle_message(self, payload: bytes) -> None:
        # 수신 페이로드를 검증한 뒤 ConnectionManager 버퍼에 제출.
        # stream loop 가 PostureFrame 파이프라인(cop/fft/risk) 을 통해 broadcast 한다.
        if self._manager is None:
            return

        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.warning("MQTT 페이로드 JSON 파싱 실패: %s", exc)
            return

        pressure_map = data.get("pressure_map")
        if not isinstance(pressure_map, list):
            logger.warning("pressure_map 누락 또는 형식 오류")
            return

        try:
            frame = np.asarray(pressure_map, dtype=np.float32)
        except (TypeError, ValueError) as exc:
            logger.warning("pressure_map numpy 변환 실패: %s", exc)
            return

        if frame.shape != (settings.fsr_rows, settings.fsr_cols):
            logger.warning("pressure_map shape 불일치: %s", frame.shape)
            return

        received_at = datetime.now(timezone.utc)
        self._manager.submit_mqtt_frame(frame, received_at)

        # 파이프라인 동작 가시화: 1초(20프레임) 단위로 INFO 로그
        self._frame_count += 1
        if self._frame_count % settings.sample_rate == 0:
            logger.info(
                "MQTT 프레임 누적 %d 개 제출 (%s)",
                self._frame_count,
                FSR_DATA_TOPIC,
            )


mqtt_subscriber = MQTTSubscriber()
