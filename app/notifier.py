"""Windows notifications with action buttons.

Shown through Windows PowerShell's built-in WinRT bindings, so no extra
package is needed. Buttons open `mldo:` links, which Windows hands to
app/actions.py in a fresh process. That is why they keep working from the
Action Center even after the organizer has restarted.
"""
import base64
import os
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import BASE, CFG

AUMID = "MLDownloadOrganizer"
APP_NAME = "Download Organizer"
SCHEME = "mldo"

_PS_SHOW = r"""
$null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
$null = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($env:MLDO_TOAST_XML)
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:MLDO_AUMID).Show($toast)
"""
# Encoded so the script needs no shell quoting; the XML travels in an env var.
_PS_ENCODED = base64.b64encode(_PS_SHOW.encode("utf-16-le")).decode("ascii")

_registered = False

def enabled():
    return os.name == "nt" and CFG.get("notifications", True)

def register():
    """Per-user registry entries (HKCU, no admin needed): the name shown on
    the notifications, and the mldo: link handler the buttons use."""
    import winreg
    pythonw = Path(sys.executable).with_name("pythonw.exe")   # no console flash
    if not pythonw.exists():
        pythonw = Path(sys.executable)
    handler = BASE / "app" / "actions.py"
    classes = r"Software\Classes"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{classes}\AppUserModelId\{AUMID}") as k:
        winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{classes}\{SCHEME}") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, f"URL:{APP_NAME}")
        winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                          rf"{classes}\{SCHEME}\shell\open\command") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, f'"{pythonw}" "{handler}" "%1"')

def link(action, row_id):
    return f"{SCHEME}:{action}?id={row_id}"

def toast_xml(title, *lines, actions=(), launch=None):
    """lines: up to two body lines under the title. actions: (button label,
    mldo link) pairs. launch: link for a click on the body."""
    launch_attr = f" launch={quoteattr(launch)} activationType=\"protocol\"" if launch else ""
    texts = "".join(f"<text>{escape(t)}</text>" for t in (title, *lines) if t)
    buttons = "".join(
        f"<action content={quoteattr(label)} arguments={quoteattr(target)}"
        f" activationType=\"protocol\"/>"
        for label, target in actions)
    return (f"<toast duration=\"long\"{launch_attr}>"
            f"<visual><binding template=\"ToastGeneric\">{texts}</binding></visual>"
            + (f"<actions>{buttons}</actions>" if buttons else "")
            + "</toast>")

def show(title, *lines, actions=(), launch=None):
    global _registered
    if not enabled():
        return
    if not _registered:
        register()
        _registered = True
    env = dict(os.environ, MLDO_TOAST_XML=toast_xml(title, *lines, actions=actions,
                                                    launch=launch),
               MLDO_AUMID=AUMID)
    # Fire and forget: PowerShell takes ~0.5 s to start and must not stall the watcher.
    subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", _PS_ENCODED],
        env=env, creationflags=subprocess.CREATE_NO_WINDOW,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def _name(path):
    return Path(path).name or str(path) if path else ""

def notify(result):
    """Turn a mover result into a notification. Silent for no-op statuses.
    Wherever a folder is involved, show its name and, below it, the full path."""
    if not result:
        return
    status, rid, name = result.get("status"), result.get("id"), result.get("file", "")
    folder = result.get("folder")

    if status == "moved":
        show(f"Moved {name}", f"→ {_name(folder)}", folder,
             actions=[("Undo", link("undo", rid)), ("Wrong folder", link("correct", rid))],
             launch=link("open", rid))
    elif status in ("skipped_low_confidence", "dry_run"):
        title = (f"Dry run: would move {name}" if status == "dry_run"
                 else f"Not sure where {name} goes")
        actions = [("Choose folder", link("correct", rid))]
        if folder:
            actions.insert(0, ("Move to guess", link("accept", rid)))
        show(title, f"Best guess: {_name(folder) or 'none'}", folder, actions=actions)
    elif status == "corrected":
        show(f"Moved {name}", f"→ {_name(folder)}  (learned for next time)", folder,
             actions=[("Undo", link("undo", rid))], launch=link("open", rid))
    elif status == "undone":
        restored = result.get("restored_to")
        show(f"Restored {name}", "Back in Downloads", str(Path(restored).parent) if restored else "")
    elif status == "error_missing_folder":
        show(f"Couldn't move {name}", "That folder no longer exists:", folder,
             actions=[("Choose folder", link("correct", rid))])
    elif status in ("error", "undo_failed_missing"):
        show(f"Couldn't move {name}" if name else APP_NAME,
             result.get("error") or "The file is no longer where it was moved to.")

if __name__ == "__main__":
    # Quick check that notifications show up:  python app/notifier.py
    show(APP_NAME, "Notifications are working.")