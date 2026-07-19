"""Vite 대시보드 스크린샷 캡처.

mock_esp32 + uvicorn 이 이미 실행 중인 상태에서 localhost:3000 으로 접속해
WebSocket 데이터가 들어와 히트맵/CoP/RiskBadge 가 채워진 후 스크린샷을 저장한다.

저장 경로: backend/screenshots/dashboard.png
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

URL = "http://localhost:3000"
OUTPUT = Path(__file__).parent / "screenshots" / "dashboard.png"


async def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(viewport={"width": 1280, "height": 720})
        page = await context.new_page()

        # 콘솔 로그 캡처 (디버깅용)
        page.on("console", lambda msg: print(f"[console.{msg.type}] {msg.text}"))
        page.on("pageerror", lambda err: print(f"[pageerror] {err}"))

        print(f"[screenshot] navigating to {URL}")
        await page.goto(URL, wait_until="networkidle")

        # LIVE 표시(.LIVE 텍스트)가 떠야 WebSocket 이 붙은 것
        try:
            await page.wait_for_function(
                "() => document.body.innerText.includes('LIVE')",
                timeout=10_000,
            )
            print("[screenshot] LIVE 상태 확인")
        except Exception as exc:
            print(f"[screenshot] LIVE 미확인 (계속 진행): {exc}")

        # 데이터 흐름 안정화 대기 (히트맵 색상 + cop 좌표 채워지도록)
        await page.wait_for_timeout(1500)

        await page.screenshot(path=str(OUTPUT), full_page=True)
        print(f"[screenshot] saved: {OUTPUT}")

        # 디버깅용 페이지 상태 일부 출력
        body_text = await page.evaluate("() => document.body.innerText")
        snippet = body_text[:600].replace("\n", " | ")
        print(f"[screenshot] body excerpt: {snippet}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
