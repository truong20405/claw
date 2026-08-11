from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timedelta

from mimo_workflow import run_workflow
from nodriver_utils import build_browser, error_summary

log = logging.getLogger("claw.rotation")
FAILED_CYCLE_BACKOFF_SECONDS = 5 * 60





async def run_account_session(
    args: argparse.Namespace, account: str, password: str
) -> bool:
    args.account = account
    args.password = password
    browser = None
    try:
        browser = await asyncio.wait_for(
            build_browser(args.headless),
            timeout=max(30, args.timeout),
        )
        tab = await asyncio.wait_for(
            browser.get(args.url),
            timeout=max(30, args.timeout),
        )
        completed = await run_workflow(browser, tab, args)
        return completed
    except Exception as error:
        log.error("Account session failed: %s", error_summary(error))
        return False
    finally:
        if browser is not None:
            try:
                browser.stop()
            except Exception as error:
                log.warning("Could not close Chrome cleanly: %s", error_summary(error))


async def run_rotation(
    args: argparse.Namespace,
    accounts: list[dict[str, str]],
    interval_hours: float,
) -> None:
    interval_seconds = interval_hours * 60 * 60
    account_index = 0
    consecutive_failures = 0
    loop = asyncio.get_running_loop()
    next_run = loop.time()

    log.info(
        "Rotation started: %d account(s), interval %.2fh",
        len(accounts),
        interval_hours,
    )

    while True:
        account_data = accounts[account_index]
        account = account_data["account"]
        log.info(
            "Running account %d/%d: %s",
            account_index + 1,
            len(accounts),
            account,
        )
        completed = await run_account_session(
            args, account, account_data["password"]
        )
        if completed:
            log.info("Account completed: %s", account)
        else:
            log.warning("Account failed: %s", account)

        account_index = (account_index + 1) % len(accounts)

        if not completed:
            consecutive_failures += 1
            next_run = loop.time()
            if consecutive_failures >= len(accounts):
                log.warning(
                    "All accounts failed; waiting %ds before retry",
                    FAILED_CYCLE_BACKOFF_SECONDS,
                )
                await asyncio.sleep(FAILED_CYCLE_BACKOFF_SECONDS)
                consecutive_failures = 0
                continue
            log.info(
                "Switching immediately to %s",
                accounts[account_index]["account"],
            )
            continue

        consecutive_failures = 0
        next_run = loop.time() + interval_seconds
        wait_seconds = max(0.0, next_run - loop.time())
        next_run_at = datetime.now().astimezone() + timedelta(seconds=wait_seconds)
        log.info(
            "Next: %s at %s (in %.2fh)",
            accounts[account_index]["account"],
            next_run_at.isoformat(timespec="seconds"),
            wait_seconds / 3600,
        )
        await asyncio.sleep(wait_seconds)
