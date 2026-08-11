from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta

from mimo_workflow import run_workflow
from nodriver_utils import build_browser, error_summary


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
        print(f"Account session failed: {error_summary(error)}")
        return False
    finally:
        if browser is not None:
            try:
                browser.stop()
            except Exception as error:
                print(f"Could not close Chrome cleanly: {error_summary(error)}")


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

    print(
        f"Rotation started with {len(accounts)} account(s), "
        f"one account every {interval_hours:g} hour(s)."
    )
    print("Press Ctrl+C to stop.")

    while True:
        account_data = accounts[account_index]
        account = account_data["account"]
        print(
            f"\n[{datetime.now().astimezone().isoformat(timespec='seconds')}] "
            f"Running account {account_index + 1}/{len(accounts)}: {account}"
        )
        completed = await run_account_session(
            args, account, account_data["password"]
        )
        print(f"Account session {'completed' if completed else 'failed'}: {account}")

        account_index = (account_index + 1) % len(accounts)

        if not completed:
            consecutive_failures += 1
            next_run = loop.time()
            if consecutive_failures >= len(accounts):
                print(
                    "All accounts failed in this cycle; waiting 5 minutes before "
                    "retrying to avoid a tight failure loop."
                )
                await asyncio.sleep(FAILED_CYCLE_BACKOFF_SECONDS)
                consecutive_failures = 0
                continue
            print(
                f"Session failed; switching immediately to "
                f"{accounts[account_index]['account']}."
            )
            continue

        consecutive_failures = 0
        next_run = loop.time() + interval_seconds
        wait_seconds = max(0.0, next_run - loop.time())
        next_run_at = datetime.now().astimezone() + timedelta(seconds=wait_seconds)
        print(
            f"Next account: {accounts[account_index]['account']} at "
            f"{next_run_at.isoformat(timespec='seconds')} "
            f"(in {wait_seconds / 3600:.2f} hours)."
        )
        await asyncio.sleep(wait_seconds)
