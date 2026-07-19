from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import cv2
import mediapipe as mp


class MediaPipeService:
    def __init__(self, camera_index: int = 0) -> None:
        # 카메라 인덱스와 내부 리소스 초기화
        self.camera_index: int = camera_index
        self._executor: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=1)
        self._cap: cv2.VideoCapture | None = None
        self._pose: Any | None = None
        self.is_dummy_mode: bool = False

    async def start(self) -> None:
        # OpenCV/MediaPipe 초기화는 스레드 풀에서 수행해 이벤트 루프를 막지 않는다.
        def _init_camera() -> tuple[cv2.VideoCapture | None, Any | None]:
            cap = cv2.VideoCapture(self.camera_index)
            if not cap.isOpened():
                cap.release()
                return None, None

            # 20Hz stream loop 가 FullHD 캡처를 못 따라가면 cap.read() 가
            # buffer 에 쌓인 stale frame 을 반환 -> Pose 가 첫 detect 결과를
            # 반복 산출하며 stuck. 버퍼를 1 로 줄여 항상 최신 frame 만 보관.
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            # tracking-lost 회복력을 위해 model_complexity 상향 + confidence 하향.
            # 자세 전환 직후 사람을 다시 detect 가능하게 한다.
            pose = mp.solutions.pose.Pose(
                static_image_mode=False,
                model_complexity=2,
                enable_segmentation=False,
                min_detection_confidence=0.3,
                min_tracking_confidence=0.3,
            )
            return cap, pose

        loop = asyncio.get_running_loop()
        cap, pose = await loop.run_in_executor(self._executor, _init_camera)
        self._cap = cap
        self._pose = pose
        self.is_dummy_mode = cap is None or pose is None

    async def get_landmarks(self) -> list[dict[str, float]] | None:
        # 카메라 사용 불가 시에도 동일 스키마(33개 랜드마크) 더미 데이터를 제공한다.
        if self.is_dummy_mode or self._cap is None or self._pose is None:
            return self.generate_dummy_landmarks()

        def _read_landmarks() -> list[dict[str, float]] | None:
            assert self._cap is not None
            assert self._pose is not None

            # buffer 에 쌓인 묵은 frame 을 grab 으로 버리고 retrieve 로 최신 1장만 디코드.
            self._cap.grab()
            ok, frame = self._cap.retrieve()
            if not ok or frame is None:
                return None

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            result = self._pose.process(rgb)
            if not result.pose_landmarks:
                return None

            # 핵심 landmark (nose, 양 ear, 양 shoulder) 평균 visibility 가 낮으면
            # Solutions API 의 zeros-stuck 반환 케이스로 보고 lost 신호 전파.
            mp_landmarks = result.pose_landmarks.landmark
            key_idx = (0, 7, 8, 11, 12)
            avg_vis = sum(mp_landmarks[i].visibility for i in key_idx) / len(key_idx)
            if avg_vis < 0.5:
                return None

            landmarks: list[dict[str, float]] = []
            for lm in mp_landmarks:
                landmarks.append(
                    {
                        "x": float(lm.x),
                        "y": float(lm.y),
                        "z": float(lm.z),
                        "visibility": float(lm.visibility),
                    }
                )
            return landmarks

        loop = asyncio.get_running_loop()
        landmarks = await loop.run_in_executor(self._executor, _read_landmarks)
        # lost(pose 미검출)는 더미 대칭좌표로 위장하지 말고 None 그대로 전파.
        # caller(_build_posture_frame) 가 last-good hold + tracking_ok=False 로 표시.
        return landmarks

    def generate_dummy_landmarks(self) -> list[dict[str, float]]:
        # 정상 자세(정면, 어깨 수평, 몸통 수직)에 가까운 33개 랜드마크를 생성한다.
        landmarks: list[dict[str, float]] = [
            {"x": 0.50, "y": 0.15, "z": -0.02, "visibility": 0.99},  # 0 nose
        ]

        # 기본값으로 몸 중앙 부근에 랜드마크를 채운 뒤 주요 포인트를 덮어쓴다.
        while len(landmarks) < 33:
            landmarks.append({"x": 0.50, "y": 0.50, "z": 0.00, "visibility": 0.95})

        # 어깨/엉덩이/귀/눈 등 주요 포인트를 안정 자세 기준으로 보정
        landmarks[11] = {"x": 0.43, "y": 0.33, "z": -0.05, "visibility": 0.98}  # left shoulder
        landmarks[12] = {"x": 0.57, "y": 0.33, "z": -0.05, "visibility": 0.98}  # right shoulder
        landmarks[23] = {"x": 0.45, "y": 0.56, "z": -0.02, "visibility": 0.98}  # left hip
        landmarks[24] = {"x": 0.55, "y": 0.56, "z": -0.02, "visibility": 0.98}  # right hip
        landmarks[7] = {"x": 0.47, "y": 0.14, "z": -0.01, "visibility": 0.97}  # left ear
        landmarks[8] = {"x": 0.53, "y": 0.14, "z": -0.01, "visibility": 0.97}  # right ear

        return landmarks

    async def stop(self) -> None:
        # 카메라/포즈 리소스를 정리하고 스레드 풀을 종료한다.
        def _release() -> None:
            if self._pose is not None:
                self._pose.close()
            if self._cap is not None and self._cap.isOpened():
                self._cap.release()

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, _release)
        self._pose = None
        self._cap = None
        self._executor.shutdown(wait=False, cancel_futures=True)
        # ws 재연결 시 _startup_pipeline -> start() 가 같은 executor 에
        # run_in_executor 를 시도하면 "cannot schedule new futures after
        # shutdown" 으로 더미 fallback 에 추락한다. 다음 start() 가 즉시
        # 쓸 수 있도록 새 executor 로 교체한다.
        self._executor = ThreadPoolExecutor(max_workers=1)
