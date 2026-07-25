"""Transport security and absolute-deadline regression tests.

Module: HTTP safety harness
Purpose: prevent DNS rebinding, credential redirects and slow-trickle hangs
Created: 2026-07-24
Author: Codex
Dependencies: Python standard library
"""

from __future__ import annotations

import socket
import signal
import sys
import time
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.request import Request


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from pola_wechat_reader.http_client import (  # noqa: E402
    HttpClient,
    PinnedHTTPSConnection,
    SafeRedirectHandler,
    resolve_public_addresses,
)
from pola_wechat_reader.config import DEFAULTS  # noqa: E402
from pola_wechat_reader.models import ConfigError, SourceError  # noqa: E402
from pola_wechat_reader.sources import SourceFetcher  # noqa: E402
from wechat_monitor import (  # noqa: E402
    ProcessDeadlineExceeded,
    _cron_command,
    _process_deadline,
    _run_command,
)


class DripResponse:
    """A response that never ends and yields one byte per read."""

    status = 200
    headers: dict[str, str] = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read1(self, amount: int) -> bytes:
        return b"x"

    def geturl(self) -> str:
        return "https://provider.example/data"


class HttpSecurityTests(unittest.TestCase):
    def test_cross_origin_authenticated_redirect_is_blocked(self) -> None:
        request = Request(
            "https://provider.example/data",
            headers={"Authorization": "Bearer secret"},
        )
        request._pola_sensitive_headers = frozenset({"authorization"})
        with self.assertRaises(SourceError) as raised:
            SafeRedirectHandler().redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                "https://other.example/data",
            )
        self.assertEqual(raised.exception.code, "cross_origin_auth_redirect")

    def test_private_dns_answer_is_rejected(self) -> None:
        private = [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("127.0.0.1", 443),
            )
        ]
        with patch("socket.getaddrinfo", return_value=private):
            with self.assertRaises(SourceError):
                resolve_public_addresses("provider.example", 443)

    def test_connection_uses_the_single_validated_dns_answer(self) -> None:
        public = [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", 443),
            )
        ]
        raw_socket = MagicMock()
        wrapped_socket = MagicMock()
        context = MagicMock()
        context.wrap_socket.return_value = wrapped_socket
        connection = PinnedHTTPSConnection("provider.example", timeout=2)
        connection._context = context
        with (
            patch("socket.getaddrinfo", return_value=public) as lookup,
            patch("socket.socket", return_value=raw_socket),
        ):
            connection.connect()
        lookup.assert_called_once_with(
            "provider.example", 443, type=socket.SOCK_STREAM
        )
        raw_socket.connect.assert_called_once_with(("93.184.216.34", 443))
        context.wrap_socket.assert_called_once_with(
            raw_socket, server_hostname="provider.example"
        )
        self.assertIs(connection.sock, wrapped_socket)

    def test_absolute_deadline_stops_slow_trickle_response(self) -> None:
        moments = iter((0.0, 0.1, 0.2, 1.1))
        client = HttpClient(
            timeout_seconds=10,
            max_response_bytes=1024,
            retry_count=0,
            run_deadline_seconds=1,
            monotonic=lambda: next(moments),
        )
        opener = MagicMock()
        opener.open.return_value = DripResponse()
        with patch(
            "pola_wechat_reader.http_client.build_opener", return_value=opener
        ):
            with self.assertRaises(SourceError) as raised:
                client.request("GET", "https://provider.example/data")
        self.assertEqual(raised.exception.code, "run_deadline_exceeded")

    @unittest.skipUnless(hasattr(signal, "SIGALRM"), "Unix alarm required")
    def test_process_deadline_cannot_be_swallowed_as_socket_timeout(self) -> None:
        self.assertFalse(issubclass(ProcessDeadlineExceeded, OSError))
        with self.assertRaises(ProcessDeadlineExceeded):
            with _process_deadline(0.01):
                time.sleep(0.1)

    def test_invalid_env_header_error_never_contains_secret(self) -> None:
        secret = "do-not-persist-this-secret\n"
        client = MagicMock()
        fetcher = SourceFetcher(client, DEFAULTS)
        source = {
            "type": "json_api",
            "key": "provider",
            "independence_group": "provider",
            "completeness": "partial",
            "url": "https://provider.example/data",
            "method": "GET",
            "headers_from_env": {
                "Authorization": {
                    "env": "POLA_TEST_PROVIDER_KEY",
                    "prefix": "Bearer ",
                }
            },
            "field_map": {"title": "title", "url": "url"},
        }
        with patch.dict(
            "os.environ",
            {"POLA_TEST_PROVIDER_KEY": secret},
            clear=False,
        ):
            observation, articles = fetcher.fetch(
                {"id": "provider"},
                source,
                now=datetime.now(timezone.utc),
                observed_at="2026-07-24T00:00:00Z",
                lookback_hours=72,
            )
        self.assertEqual(observation.error_code, "invalid_secret_header")
        self.assertNotIn("do-not-persist", observation.error_message or "")
        self.assertEqual(articles, [])
        client.request.assert_not_called()

    def test_cli_loads_bounded_config_once_before_monitor(self) -> None:
        config = {"defaults": {"max_run_seconds": 30}}
        loaded = (config, [])
        result = {
            "report": {
                "run_id": "fixture",
                "run_status": "success",
                "coverage": "partial",
                "counts": {"new": 0, "source_failures": 0},
            },
            "json_path": "/tmp/report.json",
            "markdown_path": "/tmp/report.md",
            "exit_code": 0,
        }
        args = Namespace(
            config="/tmp/targets.json",
            state="/tmp/state.sqlite",
            output="/tmp/output",
            dry_run=True,
            lookback_hours=None,
            backfill=False,
        )
        with (
            patch("wechat_monitor.load_config", return_value=loaded) as loader,
            patch("wechat_monitor.run_monitor", return_value=result) as monitor,
            redirect_stdout(StringIO()),
        ):
            self.assertEqual(_run_command(args), 0)
        loader.assert_called_once_with(
            args.config,
            private_use_override=False,
        )
        self.assertIs(monitor.call_args.kwargs["loaded_config"], loaded)

    def test_cron_rejects_path_and_schedule_injection(self) -> None:
        for unsafe_state in (
            "/tmp/state.sqlite\n* * * * * /usr/bin/id",
            "/tmp/pola%state.sqlite",
        ):
            args = Namespace(
                config="/tmp/targets.json",
                state=unsafe_state,
                output="/tmp/output",
                schedule="17 * * * *",
                lookback_hours=72,
            )
            with self.assertRaises(ConfigError):
                _cron_command(args)
        schedule_args = Namespace(
            config="/tmp/targets.json",
            state="/tmp/state.sqlite",
            output="/tmp/output",
            schedule="17 * * *\n*",
            lookback_hours=72,
        )
        with self.assertRaises(ConfigError):
            _cron_command(schedule_args)


if __name__ == "__main__":
    unittest.main()
