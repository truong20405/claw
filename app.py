from __future__ import annotations

import os
from pathlib import Path

import nodriver as uc
from dotenv import load_dotenv

from account_rotation import run_rotation
from app_config import apply_interval_override, load_rotation_config, parse_args


async def async_main() -> None:
    load_dotenv(Path(__file__).resolve().with_name(".env"), override=False)
    args = parse_args()
    if os.name != "nt" and not os.environ.get("DISPLAY"):
        args.headless = True
    config_path = Path(args.config).expanduser().resolve()
    config = load_rotation_config(config_path)
    apply_interval_override(config, args.interval_hours)

    try:
        await run_rotation(args, config["accounts"], config["interval_hours"])
    except KeyboardInterrupt:
        print("\nRotation stopped by user.")


def main() -> None:
    uc.loop().run_until_complete(async_main())


if __name__ == "__main__":
    main()
