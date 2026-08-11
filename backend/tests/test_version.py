import importlib
import os
import re
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


def _git_repo_available():
    module_dir = os.path.dirname(os.path.abspath(version.__file__))
    try:
        subprocess.run(["git", "rev-parse", "--git-dir"], capture_output=True,
                       timeout=5, check=True, cwd=module_dir)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def test_real_git_output_matches_the_documented_format():
    # The only test that runs _DATE_CMD and _SHA_CMD for real -- every other
    # test mocks subprocess, so a typo in either would otherwise pass the
    # whole suite. Repository presence is probed separately rather than
    # inferred from a None return: _git_version() also returns None when our
    # own git commands are malformed, so skipping on None would swallow
    # exactly the defect this test exists to catch.
    if not _git_repo_available():
        pytest.skip("no git repository available")
    resolved = version._git_version()
    assert resolved is not None
    assert re.fullmatch(r"\d{4}\.\d{2}\.\d{2}\+[0-9a-f]{7,}", resolved)
