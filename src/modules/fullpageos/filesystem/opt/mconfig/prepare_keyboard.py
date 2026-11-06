import os
from pathlib import Path
import tempfile


KEYBOARD = Path("/home/pi/virtual_keyboard/script.js")
PREFIX = "// re3D: render the virtual keyboard in each local document.\n(function(top) {\n"
SUFFIX = "\n})(['localhost', '127.0.0.1', '[::1]'].includes(location.hostname) ? self : top);\n"


def prepare(path=KEYBOARD):
    source = path.read_text(encoding="utf-8")
    if source.startswith(PREFIX):
        if not source.endswith(SUFFIX):
            raise ValueError("Incomplete re3D virtual keyboard integration")
        return
    for required in ("function virtualKeyboardChromeExtension_open(",
                     "function virtualKeyboardChromeExtension_click(", "if (top == self)"):
        if required not in source:
            raise ValueError("Unsupported virtual keyboard script; check its frame handling before updating")
    # The upstream relay accesses frameElement/contentDocument across localhost ports.
    # Give local frames their own keyboard instead, without changing upstream functions.
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".re3d-keyboard-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(PREFIX + source + SUFFIX)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


if __name__ == "__main__":
    prepare()
