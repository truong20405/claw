from __future__ import annotations

import argparse
import asyncio
import os
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

import nodriver as uc

from nodriver_utils import (
    CSS,
    TEXT,
    Locator,
    click_element,
    click_when_present,
    error_summary,
    find_element,
    replace_input,
    set_reactive_value,
    wait_for_attribute,
    wait_until_loaded,
)
from tempmail_flow import (
    close_tempmail_inbox,
    prepare_tempmail_inbox,
    wait_for_otp_from_tempmail,
)


GOOGLE_DRIVE_DOWNLOAD_URL = "https://drive.google.com/uc?export=download&id={file_id}"
WORKSPACE_WAIT_SECONDS = 120
POST_SEND_WAIT_SECONDS = 120
ENV_PLACEHOLDER_PATTERN = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")

TRY_NOW_BUTTON: Locator = (
    TEXT,
    "Try Now",
)
CREATE_NOW_BUTTON: Locator = (
    CSS,
    "button[data-track-id='claw_welcome_create_btn']",
)
TERMS_CHECKBOX: Locator = (CSS, "input.ant-checkbox-input[type='checkbox']")
ACCOUNT_INPUT: Locator = (CSS, "input[name='account']")
PASSWORD_INPUT: Locator = (CSS, "input[name='password']")
SIGN_IN_BUTTON: Locator = (
    TEXT,
    "Sign in",
)
SEND_EMAIL_BUTTON: Locator = (
    TEXT,
    "Send",
)
OTP_INPUT: Locator = (CSS, "input[name='ticket'][placeholder='Enter code']")
OTP_SUBMIT_BUTTON: Locator = (
    CSS,
    "button[type='submit']:not([disabled])",
)
CREATE_CONFIRMATION_CHECKBOX: Locator = (
    CSS,
    "button[role='checkbox'][aria-disabled='false']",
)
CONTINUE_CREATING_BUTTON: Locator = (
    CSS,
    "button[data-track-id='claw_create_confirm_btn']",
)
PROMPT_TEXTAREA: Locator = (
    CSS,
    "textarea[placeholder='Ask me anything! Hold Shift+Enter to start a new line.']",
)
SEND_PROMPT_BUTTON: Locator = (
    CSS,
    "button[data-track-id='claw_send_btn']",
)


async def fill_login_credentials(
    tab: uc.Tab,
    account: str,
    password: str,
    timeout: int = 5,
) -> bool:
    print("Waiting for login inputs...")
    try:
        account_input = await find_element(tab, ACCOUNT_INPUT, timeout)
        await replace_input(account_input, account)
        account_input = await find_element(tab, ACCOUNT_INPUT, timeout)
        await set_reactive_value(account_input, account)

        password_input = await find_element(tab, PASSWORD_INPUT, timeout)
        await replace_input(password_input, password)
        password_input = await find_element(tab, PASSWORD_INPUT, timeout)
        await set_reactive_value(password_input, password)
        print("Login credentials entered.")
        return True
    except Exception as error:
        print(f"Could not enter login credentials: {error_summary(error)}")
        return False


async def ensure_terms_accepted(tab: uc.Tab, timeout: int = 10) -> bool:
    print("Checking account terms checkbox...")
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            checkbox = await find_element(tab, TERMS_CHECKBOX, timeout=1)
            checked = await checkbox.apply("element => element.checked === true")
            if checked:
                print("Account terms accepted.")
                return True
            await click_element(checkbox)
        except Exception as error:
            last_error = error
        await asyncio.sleep(0.25)

    summary = error_summary(last_error) if last_error else "Timed out"
    print(f"Could not accept account terms: {summary}")
    return False


async def submit_sign_in(tab: uc.Tab, timeout: int = 10) -> bool:
    print("Submitting sign-in...")
    try:
        account_input = await find_element(tab, ACCOUNT_INPUT, timeout)
        password_input = await find_element(tab, PASSWORD_INPUT, timeout)
        await account_input.apply("element => element.blur()")
        await password_input.apply("element => element.blur()")

        sign_in_button = await find_element(tab, SIGN_IN_BUTTON, timeout)
        position = await sign_in_button.get_position()
        if not position or position.width <= 0 or position.height <= 0:
            raise RuntimeError("The visible Sign in button had no clickable position.")
        await sign_in_button.mouse_click()
        print("Sign-in submitted with a trusted mouse event.")

        await asyncio.sleep(5)
        try:
            await find_element(tab, SEND_EMAIL_BUTTON, timeout=1)
            return True
        except Exception:
            pass

        try:
            password_input = await find_element(tab, PASSWORD_INPUT, timeout=1)
        except Exception:
            return True

        await password_input.apply("element => element.focus()")
        key_options = {
            "code": "Enter",
            "key": "Enter",
            "windows_virtual_key_code": 13,
            "native_virtual_key_code": 13,
        }
        await tab.send(uc.cdp.input_.dispatch_key_event("rawKeyDown", **key_options))
        await tab.send(
            uc.cdp.input_.dispatch_key_event(
                "char",
                text="\r",
                unmodified_text="\r",
                **key_options,
            )
        )
        await tab.send(uc.cdp.input_.dispatch_key_event("keyUp", **key_options))
        print("Sign-in retried with a trusted Enter key event.")
        return True
    except Exception as error:
        print(f"Could not submit sign-in form: {error_summary(error)}")
        return False


async def submit_otp(tab: uc.Tab, otp: str, timeout: int = 10) -> bool:
    print("Waiting for the verification-code input...")
    try:
        ticket_input = await find_element(tab, OTP_INPUT, timeout)
        await replace_input(ticket_input, otp)
        submit_button = await find_element(tab, OTP_SUBMIT_BUTTON, timeout)
        await click_element(submit_button)
        print("OTP submitted.")
        return True
    except Exception as error:
        print(f"Could not submit the OTP: {error_summary(error)}")
        return False


async def ensure_creation_confirmation(tab: uc.Tab, timeout: int = 10) -> bool:
    print("Checking the creation confirmation...")
    try:
        checkbox = await find_element(tab, CREATE_CONFIRMATION_CHECKBOX, timeout)
        if checkbox.attrs.get("aria-checked") != "true":
            await click_element(checkbox)
            await wait_for_attribute(
                tab,
                CREATE_CONFIRMATION_CHECKBOX,
                "aria-checked",
                "true",
                timeout,
            )
            print("Creation confirmation checked.")
        else:
            print("Creation confirmation was already checked.")

        continue_button = await find_element(tab, CONTINUE_CREATING_BUTTON, timeout)
        await click_element(continue_button)
        print("Clicked 'Continue Creating'.")
        return True
    except Exception as error:
        print(f"Could not confirm creation: {error_summary(error)}")
        return False


def google_drive_download_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.netloc not in {"drive.google.com", "www.drive.google.com"}:
        return url

    parts = [part for part in parsed.path.split("/") if part]
    file_id = ""
    if len(parts) >= 3 and parts[0] == "file" and parts[1] == "d":
        file_id = parts[2]
    else:
        file_id = parse_qs(parsed.query).get("id", [""])[0]

    if not file_id:
        return url
    return GOOGLE_DRIVE_DOWNLOAD_URL.format(file_id=file_id)


def cache_busted_url(url: str) -> str:
    parsed = urlsplit(url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.append(("_prompt_ts", str(int(time.time() * 1000))))
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(query),
            parsed.fragment,
        )
    )


def read_prompt_source(prompt_source: str | Path) -> str:
    source = str(prompt_source)
    parsed = urlsplit(source)
    if parsed.scheme in {"http", "https"}:
        request = Request(
            cache_busted_url(google_drive_download_url(source)),
            headers={
                "User-Agent": "Mozilla/5.0",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
        )
        with urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8-sig")

    return Path(source).expanduser().read_text(encoding="utf-8-sig")


def load_prompt(prompt_source: str | Path) -> str:
    prompt_text = read_prompt_source(prompt_source).strip()
    if not prompt_text:
        raise ValueError(f"Prompt source is empty: {prompt_source}")

    variable_names = set(ENV_PLACEHOLDER_PATTERN.findall(prompt_text))
    missing_names = sorted(name for name in variable_names if not os.environ.get(name))
    if missing_names:
        raise ValueError(
            "Missing environment variable(s) required by the prompt: "
            + ", ".join(missing_names)
        )
    return ENV_PLACEHOLDER_PATTERN.sub(
        lambda match: os.environ[match.group(1)], prompt_text
    )


async def send_prompt_after_creation(
    tab: uc.Tab,
    prompt_source: str | Path,
    wait_seconds: int = WORKSPACE_WAIT_SECONDS,
    timeout: int = 30,
) -> bool:
    try:
        print("Loading the latest prompt...")
        prompt_text = load_prompt(prompt_source)
        print(f"Waiting up to {wait_seconds + timeout}s for the workspace...")
        textarea = await find_element(
            tab,
            PROMPT_TEXTAREA,
            timeout=wait_seconds + timeout,
        )
        await set_reactive_value(textarea, prompt_text)
        send_button = await find_element(tab, SEND_PROMPT_BUTTON, timeout)
        await click_element(send_button)
        print("Prompt sent.")
        print(
            f"Waiting {POST_SEND_WAIT_SECONDS}s after sending before closing "
            "the browser..."
        )
        await tab.sleep(POST_SEND_WAIT_SECONDS)
        return True
    except Exception as error:
        print(f"Could not send prompt: {error_summary(error)}")
        return False


async def prepare_verification_page(
    tab: uc.Tab,
    account: str,
    password: str,
) -> bool:
    await click_when_present(tab, TRY_NOW_BUTTON, "Try Now", timeout=15)
    await click_when_present(tab, CREATE_NOW_BUTTON, "Create Now", timeout=15)
    if not await ensure_terms_accepted(tab, timeout=15):
        return False
    if not await fill_login_credentials(tab, account, password, timeout=15):
        return False
    if not await submit_sign_in(tab, timeout=15):
        return False
    try:
        await find_element(tab, SEND_EMAIL_BUTTON, timeout=30)
        print("Verification email page is ready.")
        return True
    except Exception as error:
        await tab
        current_url = urlsplit(tab.target.url)
        current_page = f"{current_url.netloc}{current_url.path}"
        print(f"Verification email page was not ready: {error_summary(error)}")
        print(f"Current page after sign-in: {current_page}")
        return False


async def complete_creation_flow(
    tab: uc.Tab,
    otp: str,
    args: argparse.Namespace,
) -> bool:
    if not await submit_otp(tab, otp):
        return False
    if not await click_when_present(
        tab,
        CREATE_NOW_BUTTON,
        "Create Now after OTP",
        timeout=10,
    ):
        return False
    if not await ensure_creation_confirmation(tab):
        return False
    return await send_prompt_after_creation(tab, args.prompt_source)


async def save_screenshot(tab: uc.Tab, screenshot_path: str) -> None:
    output_path = Path(screenshot_path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image_format = "png" if output_path.suffix.lower() == ".png" else "jpeg"
    await tab.save_screenshot(str(output_path), format=image_format)
    print(f"Screenshot saved: {output_path}")


async def run_workflow(
    browser: uc.Browser,
    tab: uc.Tab,
    args: argparse.Namespace,
) -> bool:
    account = args.account.strip()
    password = args.password
    print(f"Opening: {args.url}")
    await wait_until_loaded(tab, args.timeout)
    await tab
    print(f"Loaded URL: {tab.target.url}")
    print(f"Page title: {tab.target.title}")

    if not await prepare_verification_page(tab, account, password):
        return False

    inbox = await prepare_tempmail_inbox(
        browser,
        account,
        original_tab=tab,
        timeout=15,
    )
    if inbox is None:
        return False

    if not await click_when_present(tab, SEND_EMAIL_BUTTON, "Send Email"):
        await close_tempmail_inbox(inbox)
        return False

    otp = await wait_for_otp_from_tempmail(inbox, args.otp_timeout)
    completed = bool(otp) and await complete_creation_flow(tab, otp, args)
    if args.screenshot:
        await save_screenshot(tab, args.screenshot)
    return completed
