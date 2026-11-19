"""The browser checks find this computer's Chrome wherever it's installed (sandbox/find_chrome.py): a path given in
STUDIO_TEST_CHROME, a command on the PATH, a Mac app in /Applications or ~/Applications (not on the PATH: why the browser
checks used to be skipped on a Mac), a Chromium Playwright downloaded (the newest build)."""
import os
import sys

import pytest

import find_chrome as fc


def exe(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("#!/bin/sh\n")
    os.chmod(path, 0o755)
    return str(path)


@pytest.fixture
def bare(monkeypatch, tmp_path):
    """A computer with nothing on the PATH, its home in tmp_path."""
    monkeypatch.delenv("STUDIO_TEST_CHROME", raising=False)
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(fc.os.path, "expanduser", lambda p: p.replace("~", str(tmp_path), 1))
    monkeypatch.setattr(fc.os.path, "isdir", lambda p, _real=os.path.isdir: _real(p) and (p.startswith(str(tmp_path)) or p == "/Applications"))
    return tmp_path


def test_a_path_given_is_used_and_a_wrong_one_says_so(bare, monkeypatch):
    monkeypatch.setenv("STUDIO_TEST_CHROME", exe(bare / "my chrome" / "chrome"))
    assert fc.find_chrome() == str(bare / "my chrome" / "chrome")
    monkeypatch.setenv("STUDIO_TEST_CHROME", str(bare / "nowhere"))
    assert fc.find_chrome() is None and "STUDIO_TEST_CHROME" in fc.WHY_NOT


def test_a_command_on_the_path(bare):
    assert fc.find_chrome() in (None, *[p for p in ("/snap/bin/chromium",) if os.path.isfile(p)])
    exe(bare / "bin" / "chromium")
    assert fc.find_chrome() == str(bare / "bin" / "chromium")


def test_a_mac_app_not_on_the_path(bare, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    app = exe(bare / "Applications" / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome")
    assert fc.find_chrome() == app


def test_the_newest_chromium_playwright_downloaded(bare, monkeypatch):
    cache = bare / ".cache" / "ms-playwright"
    exe(cache / "chromium-1208" / "chrome-linux64" / "chrome")
    exe(cache / "chromium-1194" / "chrome-linux" / "chrome")
    monkeypatch.setattr(fc, "NAMES", ())
    monkeypatch.setattr(fc.os.path, "isfile", lambda p, _real=os.path.isfile: _real(p) and str(p).startswith(str(bare)))
    assert fc.find_chrome() == str(cache / "chromium-1208" / "chrome-linux64" / "chrome")
