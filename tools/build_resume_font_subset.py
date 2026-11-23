"""S5 字体子集化：ZhimaiResume-Regular/Bold TTF → 预览用 WOFF2 子集。

子集范围 = GB2312-80 全部汉字与符号 + ASCII + 常用标点，
覆盖简历正文几乎全部用字（生僻姓氏走 fallback 字体，PDF 导出不受影响——
服务端 PDF 仍用 backend/resume_assets 下的全量 TTF）。

产物：
  backend/resume_assets/ZhimaiResume-Regular.woff2
  backend/resume_assets/ZhimaiResume-Bold.woff2
  backend/resume_assets/fonts-preview.css   （前端 ResumePreview 用）

用法：python tools/build_resume_font_subset.py
依赖：fonttools>=4.63（与 build_resume_fonts.py 同版本线）
"""
from pathlib import Path

from fontTools.subset import Subsetter, Options

ASSETS = Path(__file__).resolve().parents[1] / "backend" / "resume_assets"


def gb2312_charset() -> str:
    """GB2312-80 编码空间（0xA1A1–0xFEFE）的全部字符。"""
    chars = []
    for hi in range(0xA1, 0xFF):
        for lo in range(0xA1, 0xFF):
            try:
                chars.append(bytes([hi, lo]).decode("gb2312"))
            except UnicodeDecodeError:
                pass
    return "".join(chars)


EXTRA = "".join(chr(c) for c in range(0x20, 0x7F)) + (
    "，。；：？！、「」『』（）《》〈〉【】—…·～"
    "％‰℃℉±×÷≈≠≤≥∞°′″←→↑↓↔"
    "①②③④⑤⑥⑦⑧⑨⑩"
    "0123456789"  # 全角数字已由 GB2312 覆盖，半角兜底
)


def subset_font(src: Path, dst: Path, charset: str) -> int:
    opt = Options()
    opt.flavor = "woff2"
    opt.desubroutinize = True
    opt.layout_features = ["*"]          # 保留全部 OpenType 特性（含 GPOS）
    opt.name_IDs = ["*"]                 # 保留 OFL 要求的字体名记录
    opt.notdef_outline = True
    opt.recalc_bounds = True
    opt.drop_tables += ["FFTM"]          # fontTools 私有表可去
    font = __import__("fontTools.ttLib", fromlist=["TTFont"]).TTFont(src)
    ss = Subsetter(options=opt)
    ss.populate(text=charset)
    ss.subset(font)
    font.save(dst)
    return dst.stat().st_size


def main() -> None:
    charset = gb2312_charset() + EXTRA
    print(f"charset: {len(charset)} chars")
    for weight in ("Regular", "Bold"):
        src = ASSETS / f"ZhimaiResume-{weight}.ttf"
        dst = ASSETS / f"ZhimaiResume-{weight}.woff2"
        size = subset_font(src, dst, charset)
        print(f"{dst.name}: {size / 1024:.0f} KB (from {src.stat().st_size / 1024 / 1024:.1f} MB)")

    css = ASSETS / "fonts-preview.css"
    css.write_text(
        '@font-face {\n'
        '  font-family: "ZhimaiResume";\n'
        '  src: url("./ZhimaiResume-Regular.woff2") format("woff2");\n'
        '  font-style: normal;\n'
        '  font-weight: 400;\n'
        '  font-display: swap;\n'
        '}\n\n'
        '@font-face {\n'
        '  font-family: "ZhimaiResume";\n'
        '  src: url("./ZhimaiResume-Bold.woff2") format("woff2");\n'
        '  font-style: normal;\n'
        '  font-weight: 700;\n'
        '  font-display: swap;\n'
        '}\n',
        encoding="utf-8")
    print("fonts-preview.css written")


if __name__ == "__main__":
    main()
