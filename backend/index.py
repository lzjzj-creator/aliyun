# FC 函数计算入口（custom.debian10 web 函数）：加载 vendor 依赖并监听 9000
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "9000")))
