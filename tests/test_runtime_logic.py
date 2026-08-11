from __future__ import annotations

import argparse
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import account_rotation
import mimo_workflow
import tempmail_flow
from nodriver_utils import TEXT, find_element
from tempmail_flow import TempMailInbox


class FakeElement:
    def __init__(self, text: str) -> None:
        self.text_all = text

    async def get_position(self):
        return type("Position", (), {"width": 100, "height": 40})()


class StaleElement:
    @property
    def text_all(self) -> str:
        raise RuntimeError("stale node")


class RuntimeLogicTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_locator_uses_css_candidates(self) -> None:
        tab = AsyncMock()
        tab.select_all.return_value = [
            StaleElement(),
            FakeElement("Create account"),
            FakeElement("Try Now"),
        ]

        element = await find_element(tab, (TEXT, "Try Now"), timeout=1)

        self.assertEqual(element.text_all, "Try Now")
        tab.select_all.assert_awaited()
        self.assertFalse(hasattr(tab, "xpath") and tab.xpath.await_count)

    async def test_otp_ignores_baseline_and_opens_new_email(self) -> None:
        inbox = TempMailInbox(
            tab=AsyncMock(),
            original_tab=AsyncMock(),
            inbox_url="https://tempmail.id.vn/en/inbox",
            baseline_message_urls={"https://tempmail.id.vn/message/old"},
        )
        with (
            patch(
                "tempmail_flow.message_urls",
                AsyncMock(
                    side_effect=[
                        ["https://tempmail.id.vn/message/old"],
                        [
                            "https://tempmail.id.vn/message/old",
                            "https://tempmail.id.vn/message/new",
                        ],
                    ]
                ),
            ),
            patch("tempmail_flow.refresh_inbox", AsyncMock()),
            patch("tempmail_flow.navigate", AsyncMock()) as navigate,
            patch(
                "tempmail_flow.read_current_email_text",
                AsyncMock(return_value="Verification code is: 731905"),
            ),
            patch("tempmail_flow.asyncio.sleep", AsyncMock()),
        ):
            otp = await tempmail_flow.wait_for_new_email_otp(inbox, timeout=5)

        self.assertEqual(otp, "731905")
        navigate.assert_awaited_once_with(
            inbox.tab,
            "https://tempmail.id.vn/message/new",
            timeout=10,
        )

    def test_otp_does_not_accept_unrelated_numeric_text(self) -> None:
        self.assertIsNone(tempmail_flow.extract_otp("Invoice number: 731905"))
        self.assertEqual(
            tempmail_flow.extract_otp("Xiaomi security email: 731905"),
            "731905",
        )

    def test_google_drive_prompt_url_is_converted_to_direct_download(self) -> None:
        url = (
            "https://drive.google.com/file/d/"
            "1SXbCW-6bFvVvsq70xtb_rk3thTscc2cP/view?usp=drive_link"
        )

        self.assertEqual(
            mimo_workflow.google_drive_download_url(url),
            "https://drive.google.com/uc?export=download&id="
            "1SXbCW-6bFvVvsq70xtb_rk3thTscc2cP",
        )

    def test_load_prompt_reads_url_and_replaces_env_placeholders(self) -> None:
        response = MagicMock()
        response.__enter__.return_value.read.return_value = (
            b"hello ${TELEGRAM_BOT_TOKEN}"
        )

        with (
            patch("mimo_workflow.time.time", return_value=123.456),
            patch("mimo_workflow.urlopen", return_value=response) as open_url,
            patch.dict("mimo_workflow.os.environ", {"TELEGRAM_BOT_TOKEN": "token"}),
        ):
            prompt = mimo_workflow.load_prompt("https://example.com/prompt.txt")

        self.assertEqual(prompt, "hello token")
        request = open_url.call_args.args[0]
        self.assertIn("_prompt_ts=123456", request.full_url)
        self.assertEqual(request.headers["Cache-control"], "no-cache")
        self.assertEqual(request.headers["Pragma"], "no-cache")

    async def test_terms_checkbox_is_rechecked_after_click(self) -> None:
        tab = AsyncMock()
        checkbox = AsyncMock()
        checkbox.apply.side_effect = [False, True]

        with (
            patch("mimo_workflow.find_element", AsyncMock(return_value=checkbox)),
            patch("mimo_workflow.click_element", AsyncMock()) as click,
            patch("mimo_workflow.asyncio.sleep", AsyncMock()),
        ):
            accepted = await mimo_workflow.ensure_terms_accepted(tab, timeout=2)

        self.assertTrue(accepted)
        click.assert_awaited_once_with(checkbox)
        self.assertEqual(checkbox.apply.await_count, 2)

    async def test_sign_in_retries_with_enter_when_password_page_remains(self) -> None:
        tab = AsyncMock()
        account_input = AsyncMock()
        password_input = AsyncMock()
        sign_in_button = AsyncMock()
        sign_in_button.get_position.return_value = type(
            "Position", (), {"width": 100, "height": 40}
        )()

        find = AsyncMock(
            side_effect=[
                account_input,
                password_input,
                sign_in_button,
                TimeoutError("Send button not found"),
                password_input,
            ]
        )
        with (
            patch("mimo_workflow.find_element", find),
            patch("mimo_workflow.asyncio.sleep", AsyncMock()),
        ):
            submitted = await mimo_workflow.submit_sign_in(tab, timeout=2)

        self.assertTrue(submitted)
        sign_in_button.mouse_click.assert_awaited_once()
        self.assertEqual(tab.send.await_count, 3)

    async def test_failed_account_skips_wait_successful_account_waits(self) -> None:
        args = argparse.Namespace()
        accounts = [
            {"account": "first", "password": "one"},
            {"account": "second", "password": "two"},
        ]

        class StopAfterSuccess(Exception):
            pass

        session = AsyncMock(side_effect=[False, True])
        sleep = AsyncMock(side_effect=StopAfterSuccess)
        with (
            patch("account_rotation.run_account_session", session),
            patch("account_rotation.asyncio.sleep", sleep),
        ):
            with self.assertRaises(StopAfterSuccess):
                await account_rotation.run_rotation(args, accounts, interval_hours=4)

        self.assertEqual(session.await_count, 2)
        self.assertEqual(session.await_args_list[0].args[1], "first")
        self.assertEqual(session.await_args_list[1].args[1], "second")
        self.assertAlmostEqual(sleep.await_args.args[0], 4 * 60 * 60, delta=1)

    async def test_all_failed_accounts_back_off_after_one_cycle(self) -> None:
        args = argparse.Namespace()
        accounts = [
            {"account": "first", "password": "one"},
            {"account": "second", "password": "two"},
        ]

        class StopAfterBackoff(Exception):
            pass

        session = AsyncMock(side_effect=[False, False])
        sleep = AsyncMock(side_effect=StopAfterBackoff)
        with (
            patch("account_rotation.run_account_session", session),
            patch("account_rotation.asyncio.sleep", sleep),
        ):
            with self.assertRaises(StopAfterBackoff):
                await account_rotation.run_rotation(args, accounts, interval_hours=4)

        self.assertEqual(session.await_count, 2)
        sleep.assert_awaited_once_with(account_rotation.FAILED_CYCLE_BACKOFF_SECONDS)


if __name__ == "__main__":
    unittest.main()
