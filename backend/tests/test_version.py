import importlib
import subprocess
from unittest.mock import patch

import pytest

import version


@pytest.fixture(autouse=True)
def restore_version_module():
    # Every test reloads `version` under patched conditions; put the real
    # module back afterwards so later tests in the session see a sane value.
    yield
    importlib.reload(version)


def _completed(stdout):
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def test_app_version_env_wins_and_git_is_never_invoked():
    # The deployed container has no .git at all, so the env path must not
    # depend on git being present.
    with patch.dict("os.environ", {"APP_VERSION": "2026.08.10+abc1234"}), \
         patch("subprocess.run") as run:
        reloaded = importlib.reload(version)
    assert reloaded.VERSION == "2026.08.10+abc1234"
    run.assert_not_called()


def test_falls_back_to_git_when_env_is_unset():
    with patch.dict("os.environ", {}, clear=True), \
         patch("subprocess.run", side_effect=[_completed("2026.08.10\n"),
                                              _completed("8fac644\n")]):
        reloaded = importlib.reload(version)
    assert reloaded.VERSION == "2026.08.10+8fac644"


def test_empty_app_version_is_treated_as_unset():
    with patch.dict("os.environ", {"APP_VERSION": ""}), \
         patch("subprocess.run", side_effect=[_completed("2026.08.10\n"),
                                              _completed("8fac644\n")]):
        reloaded = importlib.reload(version)
    assert reloaded.VERSION == "2026.08.10+8fac644"


def test_git_failure_falls_back_to_dev():
    with patch.dict("os.environ", {}, clear=True), \
         patch("subprocess.run", side_effect=subprocess.CalledProcessError(128, "git")):
        reloaded = importlib.reload(version)
    assert reloaded.VERSION == "dev"


def test_missing_git_binary_falls_back_to_dev():
    with patch.dict("os.environ", {}, clear=True), \
         patch("subprocess.run", side_effect=FileNotFoundError):
        reloaded = importlib.reload(version)
    assert reloaded.VERSION == "dev"


def test_git_timeout_falls_back_to_dev():
    with patch.dict("os.environ", {}, clear=True), \
         patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 5)):
        reloaded = importlib.reload(version)
    assert reloaded.VERSION == "dev"


def test_blank_git_output_falls_back_to_dev():
    with patch.dict("os.environ", {}, clear=True), \
         patch("subprocess.run", side_effect=[_completed("\n"), _completed("\n")]):
        reloaded = importlib.reload(version)
    assert reloaded.VERSION == "dev"


def test_import_never_raises_and_yields_a_non_empty_string():
    # Guards the real reason every failure path returns "dev": main.py imports
    # this at module scope, so a raise here breaks app startup.
    reloaded = importlib.reload(version)
    assert isinstance(reloaded.VERSION, str)
    assert reloaded.VERSION
