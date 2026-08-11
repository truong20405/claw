from __future__ import annotations

import os
from pathlib import Path

import nodriver as uc
from dotenv import load_dotenv

from account_rotation import run_account_session, run_rotation
from app_config import apply_interval_override, load_rotation_config, parse_args


async def async_main() -> None:
    load_dotenv(Path(__file__).resolve().with_name(".env"), override=False)
    args = parse_args()
    if os.name != "nt" and not os.environ.get("DISPLAY"):
        args.headless = True
    config_path = Path(args.config).expanduser().resolve()
    config = load_rotation_config(config_path)
    apply_interval_override(config, args.interval_hours)

    if args.test_rotation:
        if args.keep_open and not args.headless:
            raise ValueError("--keep-open cannot be used with --test-rotation.")
        await run_rotation(
            args,
            config["accounts"],
            interval_hours=0,
            max_runs=len(config["accounts"]),
        )
        return

    run_once = args.once or bool(args.account.strip())
    if run_once:
        first_account = config["accounts"][0]
        account = args.account.strip() or first_account["account"]
        password = args.password or first_account["password"]
        success = await run_account_session(args, account, password)
        if not success:
            raise SystemExit(1)
        return

    if args.keep_open and not args.headless:
        raise ValueError("--keep-open cannot be used with continuous rotation.")

    try:
        await run_rotation(args, config["accounts"], config["interval_hours"])
    except KeyboardInterrupt:
        print("\nRotation stopped by user.")


def main() -> None:
    uc.loop().run_until_complete(async_main())


if __name__ == "__main__":
    main()
