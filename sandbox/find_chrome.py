"""Where this computer's Chrome is, for the tests that open Studio in a headless browser (they drive it over the Chrome
DevTools protocol, so any Chromium browser works: Chrome, Chromium, Edge, Brave).

Looked for, in order: STUDIO_TEST_CHROME (a path, to choose one); the browser's commands on the PATH (Linux); where the
installers put them on macOS (/Applications/…app, ~/Applications) and Windows (Program Files, the user's AppData); then the
Chromium builds Playwright and Puppeteer download. A browser installed as an app isn't on the PATH, which is why looking
only there missed Chrome on a Mac or a Windows PC."""
import glob
import os
import re
import shutil
import sys

NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome", "microsoft-edge", "microsoft-edge-stable",
         "msedge", "brave-browser", "brave")
MAC_APPS = ("Google Chrome.app/Contents/MacOS/Google Chrome", "Chromium.app/Contents/MacOS/Chromium",
            "Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary", "Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "Brave Browser.app/Contents/MacOS/Brave Browser")
WIN_APPS = (r"Google\Chrome\Application\chrome.exe", r"Chromium\Application\chrome.exe", r"Microsoft\Edge\Application\msedge.exe",
            r"BraveSoftware\Brave-Browser\Application\brave.exe")


def _downloaded():
    """The newest Chromium a Playwright or Puppeteer install downloaded."""
    home = os.path.expanduser("~")
    roots = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "", os.path.join(home, ".cache", "ms-playwright"),
             os.path.join(home, "Library", "Caches", "ms-playwright"), os.path.join(os.environ.get("LOCALAPPDATA") or "", "ms-playwright"),
             "/opt/pw-browsers", os.path.join(home, ".cache", "puppeteer")]
    pats = ("chromium-*/chrome-linux*/chrome", "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium",
            "chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing", "chromium-*/chrome-win*/chrome.exe",
            "chrome/*/chrome-*/chrome", "chrome/*/chrome-*/chrome.exe")
    found = [p for r in roots if r and os.path.isdir(r) for pat in pats for p in glob.glob(os.path.join(r, pat)) if os.path.isfile(p)]
    def version(p):                                       # the build number in its folder (chromium-1208), else when it came
        m = re.search(r"chromium-(\d+)|chrome[/\\][a-z-]*?(\d+)", p)
        return (int(next(g for g in m.groups() if g)) if m else 0, os.path.getmtime(p))
    return max(found, key=version) if found else None


def find_chrome():
    """The path of a Chromium browser to test with, or None."""
    chosen = os.environ.get("STUDIO_TEST_CHROME")
    if chosen:
        return chosen if os.path.isfile(chosen) else None
    for name in NAMES:
        p = shutil.which(name)
        if p:
            return p
    if sys.platform == "darwin":
        for base in ("/Applications", os.path.expanduser("~/Applications")):
            for app in MAC_APPS:
                p = os.path.join(base, app)
                if os.path.isfile(p):
                    return p
    if os.name == "nt":
        for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")):
            for app in WIN_APPS:
                p = os.path.join(base or "", app)
                if base and os.path.isfile(p):
                    return p
    for p in ("/snap/bin/chromium", "/var/lib/flatpak/exports/bin/com.google.Chrome", "/var/lib/flatpak/exports/bin/org.chromium.Chromium"):
        if os.path.isfile(p):
            return p
    return _downloaded()


WHY_NOT = ("no Chrome, Chromium, Edge or Brave found: install one, or set STUDIO_TEST_CHROME to its path "
           "(e.g. /Applications/Google Chrome.app/Contents/MacOS/Google Chrome)")

if __name__ == "__main__":
    print(find_chrome() or WHY_NOT)
