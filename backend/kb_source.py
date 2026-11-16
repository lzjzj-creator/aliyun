"""知识库来源 URL → 静态镜像 URL 的唯一映射规则。"""
import re
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urlsplit

ZERO_WIDTH = re.compile("[\u200b-\u200f\u202a-\u202e\u2060\ufeff]")


def to_mirror_url(url: str, base: str) -> str:
    base = base.rstrip("/")
    if not base or not url:
        return url
    # source_url 由 quote() 生成，零宽字符常以 %E2%80%8B 存在，必须先解码再清理
    path = unquote(urlsplit(url).path)
    if not path or path == "/":
        return url
    path = ZERO_WIDTH.sub("", path)
    # 爬取时以 /index 结尾的文章落盘为同级 index.html，避免拼成 /index/index.html
    if path.endswith("/index/index.html"):
        path = path[: -len("/index.html")]

    stripped = path.rstrip("/")
    if stripped.endswith("/index"):
        path = stripped + ".html"
    elif path.endswith("/"):
        path += "index.html"
    elif not PurePosixPath(path).suffix:
        path += "/index.html"

    return base + quote(path, safe="/")