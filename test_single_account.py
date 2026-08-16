"""
Test script: chạy 1 account duy nhất, browser hiển thị, log DEBUG.
Dùng để debug flow và kiểm tra các element trên trang.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import nodriver as uc
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().with_name(".env"), override=False)

from mimo_workflow import run_workflow
from nodriver_utils import build_browser, error_summary

# --- Config test ---
TEST_ACCOUNT  = "mi13@tempmail.id.vn"
TEST_PASSWORD = "nvt2005S!"
TEST_URL      = "https://aistudio.xiaomimimo.com/"
TEST_TIMEOUT  = 60
TEST_OTP_TIMEOUT = 120
# -------------------

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("test_run.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("test")


class FakeArgs:
    account       = TEST_ACCOUNT
    password      = TEST_PASSWORD
    url           = TEST_URL
    timeout       = TEST_TIMEOUT
    otp_timeout   = TEST_OTP_TIMEOUT
    headless      = False          # Hiển thị browser
    screenshot    = "test_screenshot.png"
    prompt_source = (
        "https://drive.google.com/file/d/"
        "1SXbCW-6bFvVvsq70xtb_rk3thTscc2cP/view?usp=drive_link"
    )


async def main() -> None:
    args = FakeArgs()
    log.info("=== TEST START: %s ===", TEST_ACCOUNT)
    browser = None
    try:
        browser = await asyncio.wait_for(
            build_browser(args.headless),
            timeout=60,
        )
        tab = await asyncio.wait_for(
            browser.get(args.url),
            timeout=60,
        )
        completed = await run_workflow(browser, tab, args)
        log.info("=== RESULT: %s ===", "SUCCESS" if completed else "FAILED")
    except Exception as error:
        log.error("=== CRASH: %s ===", error_summary(error))
    finally:
        if browser is not None:
            try:
                browser.stop()
            except Exception:
                pass


if __name__ == "__main__":
    uc.loop().run_until_complete(main())
