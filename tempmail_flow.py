from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import urljoin

import nodriver as uc


log = logging.getLogger("claw.tempmail")


from nodriver_utils import (
    CSS,
    TEXT,
    Locator,
    click_element,
    click_when_present,
    error_summary,
    find_element,
    find_elements,
    navigate,
    replace_input,
    set_reactive_value,
    wait_until_loaded,
)


TEMPMAIL_URL = "https://tempmail.id.vn/en"
CUSTOM_MAIL_INPUT: Locator = (
    CSS,
    "input[wire\\:model='customMail'], input[placeholder='Input custom mail']",
)
CREATE_MAIL_BUTTON: Locator = (TEXT, "Create")
INBOX_MESSAGE_LINKS: Locator = (CSS, "a[href*='/message/']")
REFRESH_INBOX_BUTTON: Locator = (TEXT, "Refresh")

XIAOMI_OTP_PATTERN = re.compile(
    r"verification\s+code\s*(?:is)?\s*:\s*([0-9]{4,8})",
    re.IGNORECASE,
)
CONTEXTUAL_OTP_PATTERN = re.compile(
    r"(?:otp|one[- ]?time(?:\s+pass(?:word|code))?|verification|verify|security)"
    r"[^\d]{0,80}([0-9]{4,8})",
    re.IGNORECASE,
)
GENERIC_OTP_PATTERN = re.compile(r"(?<!\d)([0-9]{4,8})(?!\d)")
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
EMAIL_PATTERN = re.compile(
    r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,}",
    re.IGNORECASE,
)


@dataclass
class TempMailInbox:
    tab: uc.Tab
    original_tab: uc.Tab
    inbox_url: str
    baseline_message_urls: set[str]


async def fill_custom_email(
    tab: uc.Tab,
    username: str,
    timeout: int = 5,
) -> bool:
    log.debug("Waiting for custom mail input...")
    try:
        input_element = await find_element(tab, CUSTOM_MAIL_INPUT, timeout)
        await replace_input(input_element, username)
        await set_reactive_value(input_element, username)
        log.info("Custom mailbox name entered: %s", username)
        return True
    except Exception as error:
        log.warning("Could not enter the custom mailbox name: %s", error_summary(error))
        return False


def extract_otp(text: str) -> str | None:
    for pattern in (XIAOMI_OTP_PATTERN, CONTEXTUAL_OTP_PATTERN):
        match = pattern.search(text)
        if match:
            return match.group(1)
    if "xiaomi" in text.casefold() or "mimo" in text.casefold():
        match = GENERIC_OTP_PATTERN.search(text)
        if match:
            return match.group(1)
    return None


def html_to_text(content: str) -> str:
    return html.unescape(HTML_TAG_PATTERN.sub(" ", content))


async def read_current_email_text(tab: uc.Tab) -> str:
    text_parts: list[str] = []
    for frame_element in await tab.select_all("iframe[srcdoc]", timeout=0):
        source = frame_element.attrs.get("srcdoc")
        if source:
            text_parts.append(html_to_text(source))

    try:
        for frame in await tab.get_frames():
            try:
                text_parts.append(html_to_text(await frame.get_content()))
            except Exception:
                pass
    except Exception:
        pass

    if not text_parts:
        body = await find_element(tab, (CSS, "body"), timeout=2)
        text_parts.append(body.text_all)
    return "\n".join(text_parts)


async def refresh_inbox(tab: uc.Tab) -> None:
    refresh_buttons = await find_elements(tab, REFRESH_INBOX_BUTTON)
    if refresh_buttons:
        await click_element(refresh_buttons[0])


async def message_urls(tab: uc.Tab) -> list[str]:
    message_links = await find_elements(tab, INBOX_MESSAGE_LINKS)
    urls = []
    for link in message_links:
        href = link.attrs.get("href")
        if href:
            urls.append(urljoin(tab.target.url, href))
    return list(dict.fromkeys(urls))


async def wait_for_new_email_otp(
    inbox: TempMailInbox,
    timeout: int,
) -> str | None:
    tab = inbox.tab
    deadline = time.monotonic() + timeout
    processed_urls: set[str] = set()
    poll_error_reported = False
    while time.monotonic() < deadline:
        try:
            current_urls = await message_urls(tab)
        except Exception as error:
            if not poll_error_reported:
                log.warning("TempMail inbox could not be read; retrying: %s", error_summary(error))
                poll_error_reported = True
            await asyncio.sleep(2)
            continue

        new_urls = [
            url
            for url in current_urls
            if url not in inbox.baseline_message_urls and url not in processed_urls
        ]
        if not new_urls:
            try:
                await refresh_inbox(tab)
            except Exception as error:
                if not poll_error_reported:
                    log.warning("TempMail refresh failed; polling will continue: %s", error_summary(error))
                    poll_error_reported = True
            await asyncio.sleep(2)
            continue

        for message_url in new_urls:
            processed_urls.add(message_url)
            log.info("New email received. Opening it...")
            try:
                await navigate(tab, message_url, timeout=10)
            except Exception as error:
                log.warning("Could not open the new email; retrying inbox: %s", error_summary(error))
                if time.monotonic() < deadline:
                    try:
                        await navigate(tab, inbox.inbox_url, timeout=10)
                    except Exception:
                        pass
                continue
            message_deadline = min(deadline, time.monotonic() + 10)
            while time.monotonic() < message_deadline:
                try:
                    otp = extract_otp(await read_current_email_text(tab))
                    if otp:
                        return otp
                except Exception:
                    pass
                await asyncio.sleep(1)

            if time.monotonic() < deadline:
                try:
                    await navigate(tab, inbox.inbox_url, timeout=10)
                except Exception as error:
                    if not poll_error_reported:
                        log.warning("Could not return to the TempMail inbox; retrying: %s", error_summary(error))
                        poll_error_reported = True
    return None


async def prepare_tempmail_inbox(
    browser: uc.Browser,
    email: str,
    original_tab: uc.Tab,
    timeout: int = 5,
) -> TempMailInbox | None:
    expected_email = email.strip().casefold()
    if not EMAIL_PATTERN.fullmatch(expected_email):
        raise ValueError(f"Cannot create TempMail for an invalid email address: {email}")
    username = expected_email.split("@", 1)[0]
    if not username:
        raise ValueError("Cannot create tempmail address: account email is empty.")

    log.debug("Mailbox prefix: %s", username)
    tempmail_tab: uc.Tab | None = None
    try:
        tempmail_tab = await asyncio.wait_for(
            browser.get(TEMPMAIL_URL, new_tab=True),
            timeout=max(30, timeout),
        )
        await wait_until_loaded(tempmail_tab, timeout)
        if not await fill_custom_email(tempmail_tab, username, timeout):
            raise RuntimeError("Could not prepare the custom mailbox.")
        if not await click_when_present(
            tempmail_tab,
            CREATE_MAIL_BUTTON,
            "Create Email button",
            timeout,
        ):
            raise RuntimeError("Could not create the custom mailbox.")
        mailbox = await find_element(tempmail_tab, (TEXT, expected_email), timeout)
        displayed_addresses = {
            match.casefold() for match in EMAIL_PATTERN.findall(mailbox.text_all)
        }
        if expected_email not in displayed_addresses:
            raise RuntimeError(
                "TempMail created a different mailbox than the Xiaomi account."
            )
        await tempmail_tab
        inbox_url = tempmail_tab.target.url
        baseline_urls = set(await message_urls(tempmail_tab))
        log.info("TempMail ready; recorded %d existing email(s)", len(baseline_urls))
        await original_tab.bring_to_front()
        return TempMailInbox(
            tab=tempmail_tab,
            original_tab=original_tab,
            inbox_url=inbox_url,
            baseline_message_urls=baseline_urls,
        )
    except Exception as error:
        log.warning("Could not prepare TempMail: %s", error_summary(error))
        if tempmail_tab is not None:
            try:
                await tempmail_tab.close()
            except Exception:
                pass
        try:
            await original_tab.bring_to_front()
        except Exception:
            pass
        return None


async def close_tempmail_inbox(inbox: TempMailInbox) -> None:
    try:
        try:
            await asyncio.wait_for(inbox.tab.close(), timeout=5)
        except Exception as error:
            log.warning("Could not close TempMail cleanly: %s", error_summary(error))
    finally:
        try:
            await inbox.original_tab.bring_to_front()
            log.debug("Returned to the login tab.")
        except Exception as error:
            log.warning("Could not restore the login tab: %s", error_summary(error))


async def wait_for_otp_from_tempmail(
    inbox: TempMailInbox,
    otp_timeout: int = 120,
) -> str | None:
    try:
        await inbox.tab.bring_to_front()
        log.info("Waiting for a new email for up to %ds...", otp_timeout)
        otp = await wait_for_new_email_otp(inbox, otp_timeout)
        log.info("OTP extracted successfully." if otp else "OTP was not received in time.")
        return otp
    except Exception as error:
        log.warning("TempMail OTP flow failed: %s", error_summary(error))
        return None
    finally:
        await close_tempmail_inbox(inbox)
