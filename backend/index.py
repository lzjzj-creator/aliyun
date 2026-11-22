# FC 函数计算入口（custom.debian10 web 函数）：加载 vendor 依赖并监听 9000
import os
import sys

_VENDOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
sys.path.insert(0, _VENDOR)

# Vendored headless Chromium + Debian-10 shared libraries (PDF render on FC).
# LD_LIBRARY_PATH/FONTCONFIG_FILE are inherited by the render worker's
# subprocess, so setting them here is enough; the browser zip loses its exec
# bit when the code package is re-zipped, so restore it before first launch.
_DEPS = os.path.join(_VENDOR, "chromium-deps")
os.environ["LD_LIBRARY_PATH"] = os.path.join(_DEPS, "lib") + os.pathsep + \
    os.environ.get("LD_LIBRARY_PATH", "")
os.environ.setdefault("FONTCONFIG_FILE",
                      os.path.join(_DEPS, "etc", "fonts", "fonts.conf"))
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH",
                      os.path.join(_VENDOR, "playwright-browsers"))
for _root, _dirs, _files in os.walk(os.path.join(
        _VENDOR, "playwright-browsers")):
    for _name in _files:
        _path = os.path.join(_root, _name)
        if os.access(_path, os.X_OK):
            continue
        try:
            os.chmod(_path, 0o755)
        except OSError:
            pass

import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "9000")))
