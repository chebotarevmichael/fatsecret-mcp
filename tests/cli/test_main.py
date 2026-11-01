"""The command line: four commands, and configuration problems stop it with a clear message."""

from __future__ import annotations

import pytest

from fatsecret_mcp.cli.main import main
from tests.cli.conftest import ENVIRON


@pytest.fixture(autouse=True)
def no_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*ENVIRON, "FATSECRET_MCP_BASE_URL", "FATSECRET_MCP_USER"):
        monkeypatch.delenv(name, raising=False)


def configure(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    for name, value in (ENVIRON | overrides).items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize("argv", [[], ["serve"], ["add-user"], ["check"], ["sync-catalog"]])
def test_every_command_names_missing_configuration(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as stopped:
        main(argv)

    assert stopped.value.code == "fatsecret-mcp: FATSECRET_CONSUMER_KEY is not set"


def test_serving_over_http_requires_a_public_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)

    with pytest.raises(SystemExit) as stopped:
        main(["serve", "--transport", "http"])

    assert stopped.value.code == "fatsecret-mcp: FATSECRET_MCP_BASE_URL is required over HTTP"


def test_over_stdio_a_household_of_several_needs_the_acting_user(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)

    with pytest.raises(SystemExit) as stopped:
        main([])

    assert "FATSECRET_MCP_USER must name the User to act as" in str(stopped.value.code)


def test_check_fails_with_exit_code_1_on_an_invalid_configuration(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(monkeypatch, FATSECRET_MCP_USERS='[{"id": "jakub"}]')

    with pytest.raises(SystemExit) as stopped:
        main(["check"])

    assert stopped.value.code == 1
    assert "Configuration: Invalid Household configuration" in capsys.readouterr().out
