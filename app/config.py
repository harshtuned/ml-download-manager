import json
import os
import stat
from pathlib import Path

# Anchor everything to the project folder so the app works no matter which
# directory it is launched from (Task Scheduler, double-click, `cd app`, ...).
BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"

with open(BASE / "config.json", "r", encoding="utf-8") as f:
    CFG = json.load(f)

DOWNLOADS = Path(CFG["downloads_dir"]).expanduser().resolve()

_HIDDEN_ATTRS = (getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0)
                 | getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 0))

def norm(path):
    """Comparable form of a path: absolute, normalized, case-folded on Windows."""
    return os.path.normcase(os.path.abspath(os.path.expanduser(str(path))))

def is_hidden(path):
    """Dotfiles, Office lock files (~$x.docx) and Windows hidden/system files
    such as desktop.ini and Thumbs.db."""
    p = Path(path)
    if p.name.startswith((".", "~$")):
        return True
    try:
        return bool(getattr(p.stat(), "st_file_attributes", 0) & _HIDDEN_ATTRS)
    except OSError:
        return False
