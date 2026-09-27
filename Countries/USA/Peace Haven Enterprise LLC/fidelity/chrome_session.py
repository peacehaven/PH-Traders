"""Start (or find) an ordinary Chrome window and attach to it.

Fidelity refuses sign-ins from a browser a driver launched, so this script never launches
one that way. It starts a normal Chrome with a debugging port, you sign in by hand, and it
then reads the page you are looking at. Nothing about the browser is disguised, and no
password is stored anywhere.
"""
from __future__ import annotations

import logging
import os
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

log = logging.getLogger("fidelity")

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
]


class ChromeError(Exception):
    """Chrome could not be found, started, or reached."""


def port_is_open(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    with socket.socket() as probe:
        probe.settimeout(timeout)
        return probe.connect_ex((host, port)) == 0


def find_chrome(configured: str = "") -> str:
    if configured:
        if not Path(configured).exists():
            raise ChromeError(f"chrome.path in config.json points at {configured}, "
                              "which does not exist.")
        return configured
    for candidate in CHROME_PATHS:
        if candidate and Path(candidate).exists():
            return candidate
    raise ChromeError("Could not find chrome.exe. Install Google Chrome, or put its full "
                      'path in config.json under "chrome": {"path": "..."}.')


def start_chrome(config: dict, folder: Path, url: str) -> None:
    """Launch Chrome detached, so it keeps running after this script exits."""
    chrome = config.get("path") or ""
    port = int(config.get("debug_port", 9222))
    profile = Path(config.get("profile_dir", "chrome-profile"))
    if not profile.is_absolute():
        profile = folder / profile
    profile.mkdir(parents=True, exist_ok=True)

    command = [find_chrome(chrome), f"--remote-debugging-port={port}",
               f"--user-data-dir={profile}", url]
    log.info("Starting Chrome with its own profile at %s", profile)

    flags = 0
    if os.name == "nt":  # survive this terminal closing
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(command, creationflags=flags, close_fds=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    for _ in range(40):  # up to ~20s for the port to answer
        if port_is_open(port):
            return
        time.sleep(0.5)
    raise ChromeError(
        f"Chrome started but nothing is listening on port {port}.\n"
        "That usually means another Chrome already owns this profile folder. Close every "
        f"Chrome window and try again, or change chrome.profile_dir in config.json.")


def connect(config: dict, folder: Path, url: str) -> webdriver.Chrome:
    """Attach to the debugging Chrome, starting one first if needed."""
    port = int(config.get("debug_port", 9222))
    if port_is_open(port):
        log.info("Found a Chrome already listening on port %d.", port)
    else:
        start_chrome(config, folder, url)

    options = Options()
    options.debugger_address = f"127.0.0.1:{port}"
    try:
        return webdriver.Chrome(options=options)
    except Exception as error:  # driver/browser mismatch, port taken by something else
        raise ChromeError(f"Could not attach to Chrome on port {port}: {error}") from None


def use_live_tab(driver, prefer: str = "") -> None:
    """Point the driver at a tab that is actually open.

    Chrome discards its startup target once the real page loads, and chromedriver can
    attach to that dead one ("no such window: target window already closed"). Walking the
    handles and preferring the site's own tab avoids it.
    """
    try:
        handles = driver.window_handles
    except Exception as error:
        raise ChromeError(f"Chrome is not responding: {error}") from None
    if not handles:
        raise ChromeError("That Chrome window has no tabs open. Open the site in it and "
                          "run this again.")

    fallback = None
    for handle in handles:
        try:
            driver.switch_to.window(handle)
            url = driver.current_url
        except Exception:
            continue  # this tab is gone; try the next
        if prefer and prefer in url:
            return
        if fallback is None:
            fallback = handle
    if fallback is None:
        raise ChromeError("Every tab in that Chrome window closed while it was being read.")
    driver.switch_to.window(fallback)


def wait_until_signed_in(driver, config: dict, timeout: float) -> None:
    """Block until the account pages are visible. The person signs in themselves."""
    def ready(d):
        return is_signed_in(d, config)

    if ready(driver):
        log.info("Already signed in.")
        return

    print("\n" + "=" * 72, file=sys.stderr)
    print("  Sign in to Fidelity in the Chrome window that is open.", file=sys.stderr)
    print("  Nothing is typed for you - finish any security code as usual.", file=sys.stderr)
    print(f"  This continues by itself once you are in (waiting up to {int(timeout)}s).",
          file=sys.stderr)
    print("=" * 72 + "\n", file=sys.stderr)

    try:
        WebDriverWait(driver, timeout, poll_frequency=1).until(ready)
    except Exception:
        raise ChromeError(
            f"Still not signed in after {int(timeout)}s. Run it again once you are signed "
            "in, or raise sign_in_timeout_seconds in config.json.") from None
    log.info("Signed in.")


def read_grid_html(driver, selector: str, timeout: float) -> str:
    """Wait until the holdings grid has rows and has stopped changing, then return it."""
    state = {"rows": -1, "since": time.monotonic()}

    def settled(d):
        found = d.find_elements(By.CSS_SELECTOR, selector)
        if not found:
            return False
        count = len(found[0].find_elements(By.CSS_SELECTOR, "[row-index], tr, [role='row']"))
        now = time.monotonic()
        if count != state["rows"]:
            state.update(rows=count, since=now)
            return False
        if count > 1 and now - state["since"] >= 1.5:
            return found[0].get_attribute("outerHTML")
        return False

    try:
        return WebDriverWait(driver, timeout, poll_frequency=0.5).until(settled)
    except Exception:
        raise ChromeError(
            "The holdings grid did not appear. Open the Positions page in that Chrome "
            "window, then run this again.") from None

# --------------------------------------------------------------------------- #
# Signing in
# --------------------------------------------------------------------------- #

def load_credentials(service: str) -> tuple[str | None, str | None]:
    """Credentials come from Windows Credential Manager (keyring), or environment
    variables FIDELITY_USERNAME / FIDELITY_PASSWORD. They are never stored in this folder."""
    user, password = os.getenv("FIDELITY_USERNAME"), os.getenv("FIDELITY_PASSWORD")
    if user and password:
        return user, password
    try:
        import keyring

        user = keyring.get_password(service, "username")
        password = keyring.get_password(service, user) if user else None
        return (user, password) if user and password else (None, None)
    except Exception as error:  # keyring missing or no backend on this machine
        log.debug("No stored credentials (%s).", error)
        return None, None


def save_credentials(service: str) -> int:
    """Ask once and store in the operating system's credential store."""
    import getpass

    try:
        import keyring
    except ImportError:
        print("keyring is not installed. Run: pip install keyring", file=sys.stderr)
        return 1

    user = input("Fidelity username: ").strip()
    if not user:
        print("Nothing saved.", file=sys.stderr)
        return 1
    password = getpass.getpass("Fidelity password (typing is hidden): ")
    if not password:
        print("Nothing saved.", file=sys.stderr)
        return 1
    try:
        keyring.set_password(service, "username", user)
        keyring.set_password(service, user, password)
    except Exception as error:
        print(f"Could not save to the credential store: {error}", file=sys.stderr)
        return 1
    print(f"Saved for {user}. Stored by Windows, not in this folder.\n"
          "Remove it later with: python fidelity.py --forget-login")
    return 0


def forget_credentials(service: str) -> int:
    try:
        import keyring

        user = keyring.get_password(service, "username")
        if user:
            keyring.delete_password(service, user)
        keyring.delete_password(service, "username")
        print("Stored credentials removed.")
        return 0
    except Exception as error:
        print(f"Nothing removed: {error}", file=sys.stderr)
        return 1


def delay_for(config: dict, name: str, default: float) -> float:
    """A pause from config.json's "delays", clamped to something sane (0-30s)."""
    try:
        value = float(config.get("delays", {}).get(name, default))
    except (TypeError, ValueError):
        return default
    return max(0.0, min(value, 30.0))


def type_text(element, text: str, per_character: float) -> None:
    """Send the text a character at a time so the page's JavaScript sees each keystroke.

    Some sign-in forms track input events and ignore a value that arrives all at once.
    """
    if per_character <= 0:
        element.send_keys(text)
        return
    for character in text:
        element.send_keys(character)
        time.sleep(per_character)


def _first_visible(driver, selectors):
    for selector in selectors if isinstance(selectors, list) else [selectors]:
        for element in driver.find_elements(By.CSS_SELECTOR, selector):
            try:
                if element.is_displayed() and element.is_enabled():
                    return element
            except Exception:
                continue
    return None


def describe_form(driver, limit: int = 25) -> str:
    """List visible inputs and buttons - attribute names only, never their values."""
    lines = []
    try:
        elements = driver.find_elements(By.CSS_SELECTOR, "input, select, button")
    except Exception as error:
        return f"  (could not read the page: {error})"
    for element in elements:
        if len(lines) >= limit:
            break
        try:
            if not element.is_displayed():
                continue
            attributes = " ".join(
                f'{name}="{value}"' for name in ("type", "id", "name", "autocomplete",
                                                 "aria-label", "placeholder")
                if (value := element.get_attribute(name)))
            label = " ".join((element.text or "").split())[:30]
            lines.append(f"  <{element.tag_name} {attributes}> {label}".rstrip())
        except Exception:
            continue
    return "\n".join(lines) or "  (no visible inputs - is the page still loading?)"


# Fidelity draws its sign-in box with web components, so the inputs can sit inside shadow
# roots where an ordinary CSS lookup finds nothing. This walks into them.
DEEP_QUERY_JS = """
const selector = arguments[0];
const roots = [document];
const seen = new Set();
while (roots.length) {
  const root = roots.shift();
  if (!root || seen.has(root)) continue;
  seen.add(root);
  const hit = root.querySelector(selector);
  if (hit && hit.getClientRects().length) return hit;
  const all = root.querySelectorAll('*');
  for (const element of all) {
    if (element.shadowRoot) roots.push(element.shadowRoot);
  }
}
return null;
"""

# Setting .value directly bypasses the framework's own tracking, so use the native setter
# and fire the events a real keystroke would.
SET_VALUE_JS = """
const element = arguments[0], text = arguments[1];
const prototype = element instanceof HTMLTextAreaElement
  ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
const setter = Object.getOwnPropertyDescriptor(prototype, 'value').set;
setter.call(element, text);
element.dispatchEvent(new Event('input', {bubbles: true}));
element.dispatchEvent(new Event('change', {bubbles: true}));
"""


def deep_find(driver, selectors):
    """Find the first visible match, looking inside shadow roots as well."""
    for selector in selectors if isinstance(selectors, list) else [selectors]:
        try:
            element = driver.execute_script(DEEP_QUERY_JS, selector)
        except Exception as error:
            log.debug("Deep search failed for %s: %s", selector, error)
            continue
        if element is not None:
            log.debug("Found %s inside a shadow root.", selector)
            return element
    return None


def fill_field(driver, box, text: str, per_character: float) -> bool:
    """Type into the field, then check it actually took; fall back to the native setter."""
    try:
        box.clear()
    except Exception:
        pass
    type_text(box, text, per_character)
    try:
        if (box.get_attribute("value") or "") == text:
            return True
    except Exception:
        return True  # cannot read it back; assume the keystrokes landed
    log.info("The field ignored the keystrokes; setting it directly instead.")
    try:
        driver.execute_script(SET_VALUE_JS, box, text)
        return (box.get_attribute("value") or "") == text
    except Exception as error:
        log.warning("Could not fill the field: %s", error)
        return False


def save_login_page(driver, folder: Path) -> Path | None:
    """Keep the page that defeated us, so the selectors can be fixed."""
    try:
        debug = folder / "debug"
        debug.mkdir(exist_ok=True)
        path = debug / f"login-{time.strftime('%Y%m%d-%H%M%S')}.html"
        path.write_text(driver.page_source, encoding="utf-8")
        return path
    except Exception:
        return None


def wait_first_visible(driver, selectors, timeout: float):
    """Poll until one of the selectors matches something visible, or give up."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        element = _first_visible(driver, selectors) or deep_find(driver, selectors)
        if element is not None:
            return element
        time.sleep(0.4)
    return None


def type_credentials(driver, selectors: dict, username: str, password: str,
                     config: dict | None = None) -> bool:
    """Fill the sign-in form in the window you opened and submit it.

    This types into an ordinary Chrome - the browser is not modified or disguised in any
    way. If the fields are not on screen, this returns False and you sign in by hand.
    """
    config = config or {}
    time.sleep(delay_for(config, "before_typing_seconds", 1.5))

    # The fields are drawn by JavaScript, so wait for them rather than look once.
    patience = delay_for(config, "form_wait_seconds", 20.0)
    user_box = wait_first_visible(driver, selectors.get("username", []), patience)
    pass_box = wait_first_visible(driver, selectors.get("password", []), 3.0)
    if not (user_box and pass_box):
        saved = save_login_page(driver, Path(__file__).resolve().parent)
        log.error("COULD NOT FIND THE SIGN-IN FIELDS after %.0fs - nothing was typed.\n"
                  "  page: %s\n  looked for: %s\n  visible inputs:\n%s\n%s",
                  patience, driver.current_url,
                  selectors.get("username", []), describe_form(driver),
                  f"  page saved to {saved} - send me that file and I'll fix the selectors."
                  if saved else "")
        return False

    per_character = delay_for(config, "per_character_seconds", 0.08)
    log.info("Filling the sign-in form.")
    if not fill_field(driver, user_box, username, per_character):
        log.warning("The username field would not accept the text.")
        return False
    time.sleep(delay_for(config, "between_fields_seconds", 0.8))
    if not fill_field(driver, pass_box, password, per_character):
        log.warning("The password field would not accept the text.")
        return False
    time.sleep(delay_for(config, "before_submit_seconds", 1.2))

    submit = _first_visible(driver, selectors.get("submit", []))
    if submit:
        submit.click()
    else:
        pass_box.send_keys("\n")
    return True


def is_signed_in(driver, signed_in: dict) -> bool:
    marker = signed_in.get("marker_selector", "")
    hint = signed_in.get("url_contains", "")
    try:
        if marker and driver.find_elements(By.CSS_SELECTOR, marker):
            return True
        return bool(hint) and hint in driver.current_url
    except Exception:  # the tab went away mid-check
        use_live_tab(driver, hint.split("/")[0] if hint else "")
        if marker and driver.find_elements(By.CSS_SELECTOR, marker):
            return True
        return bool(hint) and hint in driver.current_url

# --------------------------------------------------------------------------- #
# Waiting for your sign-in without attaching anything
# --------------------------------------------------------------------------- #

def list_tabs(port: int) -> list[dict]:
    """Ask Chrome what it has open, over plain HTTP.

    This is a read-only request to the local debugging port. No driver is attached and no
    CDP session touches the page, so the site sees an ordinary browser while you sign in.
    """
    from urllib.request import urlopen

    try:
        with urlopen(f"http://127.0.0.1:{port}/json/list", timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as error:
        log.debug("Could not list tabs: %s", error)
        return []


def signed_in_tab(tabs: list[dict], url_contains: str) -> dict | None:
    """The first ordinary page whose address shows you are past the login."""
    for tab in tabs:
        if tab.get("type") != "page":
            continue
        url = tab.get("url", "")
        if url_contains and url_contains in url and "signin" not in url and "login" not in url:
            return tab
    return None


def wait_for_sign_in(port: int, url_contains: str, timeout: float,
                     config: dict | None = None) -> None:
    """Block until a signed-in tab appears. Nothing is attached while this waits."""
    if signed_in_tab(list_tabs(port), url_contains):
        log.info("Already signed in.")
        return

    print("\n" + "=" * 72, file=sys.stderr)
    print("  Sign in to Fidelity in the Chrome window that is open.", file=sys.stderr)
    print("  Nothing is attached to it while you do - it is an ordinary browser.", file=sys.stderr)
    print("  Once your account pages are showing, this continues by itself.", file=sys.stderr)
    print(f"  Waiting up to {int(timeout)}s.", file=sys.stderr)
    print("=" * 72 + "\n", file=sys.stderr)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if signed_in_tab(list_tabs(port), url_contains):
            settle = delay_for(config or {}, "after_sign_in_seconds", 3.0)
            log.info("Signed in. Letting the page settle for %.1fs, then reading it.", settle)
            time.sleep(settle)
            return
        time.sleep(2)
    raise ChromeError(f"Still not signed in after {int(timeout)}s. Sign in, then run it again.")


def ensure_chrome(config: dict, folder: Path, url: str) -> int:
    """Make sure a Chrome with the debugging port is running, and return the port."""
    port = int(config.get("debug_port", 9222))
    if port_is_open(port):
        log.info("Using the Chrome already open on port %d.", port)
    else:
        start_chrome(config, folder, url)
    return port


def attach(port: int, prefer: str = "") -> webdriver.Chrome:
    options = Options()
    options.debugger_address = f"127.0.0.1:{port}"
    try:
        driver = webdriver.Chrome(options=options)
    except Exception as error:
        raise ChromeError(f"Could not attach to Chrome on port {port}: {error}") from None
    use_live_tab(driver, prefer)
    return driver


def read_grid_snapshots(driver, selector: str, timeout: float,
                        config: dict | None = None) -> list[str]:
    """Capture the grid while scrolling, so virtualised rows all get seen.

    ag-Grid keeps only the visible rows in the page. Reading once gives you the top of the
    list and the totals of accounts further down, but not their holdings. This scrolls a
    screen at a time and keeps every snapshot; merging happens in positions.read_snapshots.
    """
    config = config or {}
    first = read_grid_html(driver, selector, timeout)
    snapshots = [first]

    grids = driver.find_elements(By.CSS_SELECTOR, selector)
    if not grids:
        return snapshots
    grid = grids[0]
    viewports = grid.find_elements(By.CSS_SELECTOR, ".ag-body-viewport")
    viewport = viewports[0] if viewports else None
    if viewport is None:
        driver.execute_script("window.scrollTo(0, 0);")

    pause = delay_for(config, "scroll_step_seconds", 0.6)
    previous = -1
    for step in range(60):  # plenty for a long positions list
        if viewport is not None:
            position = driver.execute_script(
                "const v = arguments[0];"
                "v.scrollTop = v.scrollTop + Math.max(v.clientHeight * 0.8, 200);"
                "return v.scrollTop;", viewport)
        else:
            position = driver.execute_script(
                "window.scrollBy(0, Math.max(window.innerHeight * 0.8, 200));"
                "return window.scrollY;")
        time.sleep(pause)
        snapshots.append(grid.get_attribute("outerHTML"))
        if position == previous:  # reached the bottom
            break
        previous = position

    log.info("Read the grid in %d passes while scrolling.", len(snapshots))
    return snapshots
