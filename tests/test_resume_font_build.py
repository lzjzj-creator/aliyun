"""Font-build regression tests; PDF text is compared verbatim, never normalized."""

import hashlib
import importlib
import io
import json
from collections import Counter
from copy import deepcopy
from pathlib import Path

import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._c_m_a_p import CmapSubtable
from fontTools.varLib.instancer import instantiateVariableFont


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "backend" / "resume_assets"
SOURCE = ASSETS / "NotoSansSC.ttf"
SOURCE_SHA256 = "a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da"
OFL_SHA256 = "1c05c68c34f9708415aada51f17e1b0092d2cea709bf4a94cd38114f9e73d7d9"
SAMPLE = "林知行 后端开发工程师 行⾏工⼯–‒"


def test_frontend_distribution_includes_font_license():
    license_path = ROOT / "frontend" / "public" / "fonts" / "OFL.txt"
    assert license_path.read_bytes() == (ASSETS / "OFL.txt").read_bytes()


def builder():
    return importlib.import_module("tools.build_resume_fonts")


def unicode_map(font):
    merged = {}
    for table in font["cmap"].tables:
        if table.isUnicode():
            for codepoint, glyph in table.cmap.items():
                assert merged.setdefault(codepoint, glyph) == glyph
    return merged


def synthetic_font():
    """Include aliases of composite glyphs, vertical metrics and format 12/14."""
    glyphs = {}
    for name, width in ((".notdef", 0), ("stroke", 500), ("bar", 300)):
        pen = TTGlyphPen(None)
        if width:
            pen.moveTo((10, 20))
            pen.lineTo((width, 20))
            pen.lineTo((width, 600))
            pen.lineTo((10, 600))
            pen.closePath()
        glyphs[name] = pen.glyph()
    pen = TTGlyphPen(glyphs)
    pen.addComponent("stroke", (1, 0, 0, 1, 0, 0))
    pen.addComponent("bar", (1, 0, 0, 1, 500, 100))
    glyphs["composite"] = pen.glyph()
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder(list(glyphs))
    fb.setupCharacterMap({
        0x884C: "composite", 0x2F8F: "composite",
        0x5DE5: "stroke", 0x2F2F: "stroke", 0x20000: "stroke",
        0x2013: "bar", 0x2012: "bar",
    })
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics({name: (900, 10) for name in glyphs})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupVerticalMetrics({name: (1000, 50) for name in glyphs})
    fb.setupVerticalHeader(ascent=800, descent=-200)
    fb.setupOS2()
    fb.setupNameTable({"familyName": "Test", "styleName": "Regular"})
    fb.setupPost()
    fb.setupMaxp()
    uvs = CmapSubtable.newSubtable(14)
    uvs.platformID, uvs.platEncID, uvs.language = 0, 5, 0
    uvs.cmap = {}
    uvs.uvsDict = {0xFE00: [(0x884C, None), (0x5DE5, "stroke")]}
    fb.font["cmap"].tables.append(uvs)
    return fb.font


def test_sample_cmap_aliases_clone_composites_and_metrics():
    font = synthetic_font()
    before = deepcopy(font)
    original_order = before.getGlyphOrder()
    assert builder().deduplicate_unicode_cmap(font) == 4
    cmap = unicode_map(font)
    assert len(cmap) == len(set(cmap.values()))
    assert font.getGlyphOrder()[:len(original_order)] == original_order
    for codepoint, old_name in unicode_map(before).items():
        new_name = cmap[codepoint]
        assert font["glyf"][new_name].compile(font["glyf"]) == before["glyf"][old_name].compile(before["glyf"])
        for table in ("hmtx", "vmtx"):
            assert font[table].metrics[new_name] == before[table].metrics[old_name]
    assert font["glyf"][cmap[0x884C]].isComposite()
    assert font["glyf"][cmap[0x884C]] is not font["glyf"][cmap[0x2F8F]]
    assert font["glyf"][cmap[0x884C]].components[0] is not font["glyf"][cmap[0x2F8F]].components[0]
    assert font["cmap"].tables[-1].uvsDict[0xFE00] == [(0x884C, None), (0x5DE5, cmap[0x5DE5])]
    assert builder().deduplicate_unicode_cmap(font) == 0
    data = io.BytesIO()
    font.save(data)
    data.seek(0)
    with TTFont(data) as reopened:
        assert unicode_map(reopened) == cmap
        assert reopened["maxp"].numGlyphs == len(original_order) + 4


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    output = tmp_path_factory.mktemp("resume-font-build")
    metadata = builder().build_fonts(SOURCE, output)
    return output, metadata


@pytest.mark.parametrize("weight,style", [(400, "Regular"), (700, "Bold")])
def test_static_fonts_preserve_outlines_metrics_and_layout(built, weight, style):
    output, _ = built
    with TTFont(SOURCE, recalcTimestamp=False) as upstream:
        baseline = instantiateVariableFont(upstream, {"wght": weight}, inplace=False)
    with TTFont(output / f"ZhimaiResume-{style}.ttf") as font:
        original_map = unicode_map(baseline)
        cmap = unicode_map(font)
        assert set(cmap) == set(original_map)
        assert len(cmap) == len(set(cmap.values()))
        assert len({font.getGlyphID(glyph) for glyph in cmap.values()}) == len(cmap)
        for first, second in (("行", "⾏"), ("工", "⼯"), ("–", "‒")):
            assert original_map[ord(first)] == original_map[ord(second)]
            assert cmap[ord(first)] != cmap[ord(second)]
        assert all(ord(character) in cmap for character in SAMPLE)
        assert not {"fvar", "gvar", "HVAR", "VVAR", "MVAR", "avar", "cvar"}.intersection(font.keys())
        assert font["OS/2"].usWeightClass == weight
        assert bool(font["OS/2"].fsSelection & (1 << 5)) == (weight == 700)
        assert bool(font["OS/2"].fsSelection & (1 << 6)) == (weight == 400)
        assert bool(font["head"].macStyle & 1) == (weight == 700)
        assert font.getGlyphOrder()[:len(baseline.getGlyphOrder())] == baseline.getGlyphOrder()
        aliases = sum(count - 1 for count in Counter(original_map.values()).values())
        assert font["maxp"].numGlyphs == baseline["maxp"].numGlyphs + aliases
        # Every encoded glyph, not only the Chinese regression samples.
        for codepoint, old_name in original_map.items():
            new_name = cmap[codepoint]
            assert font["glyf"][new_name].compile(font["glyf"]) == baseline["glyf"][old_name].compile(baseline["glyf"]), hex(codepoint)
            for table in ("hmtx", "vmtx"):
                assert font[table].metrics[new_name] == baseline[table].metrics[old_name], hex(codepoint)
        for table in ("GSUB", "GPOS", "GDEF", "BASE"):
            assert font.getTableData(table) == baseline.getTableData(table)
        for table in baseline["cmap"].tables:
            if table.format == 14:
                actual = next(t for t in font["cmap"].tables if t.format == 14)
                expected = {
                    selector: [(cp, cmap[cp] if glyph is not None and glyph == original_map.get(cp) else glyph)
                               for cp, glyph in entries]
                    for selector, entries in table.uvsDict.items()
                }
                assert actual.uvsDict == expected
        for name_id, expected in {
            1: "Zhimai Resume", 2: style, 4: f"Zhimai Resume {style}",
            6: f"ZhimaiResume-{style}", 16: "Zhimai Resume", 17: style,
        }.items():
            records = [record for record in font["name"].names if record.nameID == name_id]
            assert records and all(record.toUnicode() == expected for record in records)
        assert not any("Noto" in record.toUnicode() for record in font["name"].names if record.nameID in {1, 2, 3, 4, 6, 16, 17, 18, 21, 22, 25})
        for name_id in (0, 7, 8, 9, 10, 11, 13, 14):
            assert [record.toUnicode() for record in font["name"].names if record.nameID == name_id] == [record.toUnicode() for record in baseline["name"].names if record.nameID == name_id]


def test_build_is_byte_reproducible_and_matches_distributed_assets(built, tmp_path):
    output, metadata = built
    repeated = builder().build_fonts(SOURCE, tmp_path)
    assert repeated == metadata
    for name in ("ZhimaiResume-Regular.ttf", "ZhimaiResume-Bold.ttf", "fonts.css"):
        assert (output / name).read_bytes() == (tmp_path / name).read_bytes()
        assert (output / name).read_bytes() == (ASSETS / name).read_bytes()


def test_source_license_and_manifest_are_preserved(built):
    output, metadata = built
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == SOURCE_SHA256
    assert hashlib.sha256((ASSETS / "OFL.txt").read_bytes()).hexdigest() == OFL_SHA256
    manifest = json.loads((ASSETS / "font-source.json").read_text(encoding="utf-8"))
    assert manifest["repository"] == "https://github.com/google/fonts"
    assert manifest["source_commit"] == "06bfe0d62a98375cb09c942fa18e68bd32cff057"
    assert manifest["license"]["file"] == "OFL.txt"
    upstream = {asset["file"]: asset for asset in manifest["assets"]}
    assert upstream["NotoSansSC.ttf"]["sha256"] == SOURCE_SHA256
    assert upstream["OFL.txt"]["sha256"] == OFL_SHA256
    assert manifest["generated"] == metadata
    assert metadata["dependency"] == "fonttools==4.63.0"
    assert metadata["source_sha256"] == SOURCE_SHA256
    assert metadata["license"] == "OFL-1.1"
    for asset in metadata["assets"]:
        data = (output / asset["file"]).read_bytes()
        assert asset["size_bytes"] == len(data)
        assert asset["sha256"] == hashlib.sha256(data).hexdigest()


def test_css_uses_shared_local_static_faces(built):
    output, _ = built
    css = (output / "fonts.css").read_text(encoding="utf-8")
    assert css.count("@font-face") == 2
    for style, weight in (("Regular", 400), ("Bold", 700)):
        block = next(block for block in css.split("}") if f"ZhimaiResume-{style}.ttf" in block)
        assert 'font-family: "ZhimaiResume";' in block
        assert f'url("./ZhimaiResume-{style}.ttf")' in block
        assert f"font-weight: {weight};" in block
        assert "font-display: block;" in block
    assert "http" not in css
    assert "local(" not in css


def test_chromium_pdf_preserves_exact_unicode_at_both_weights(built):
    from playwright.sync_api import sync_playwright
    from pypdf import PdfReader
    import fitz

    output, _ = built
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            print(f"Chromium {browser.version}")
            page = browser.new_page()
            # A local-only route exercises the actual shared CSS and relative font URLs.
            def serve(route):
                name = route.request.url.rsplit("/", 1)[-1]
                path = output / name
                if name in {"fonts.css", "ZhimaiResume-Regular.ttf", "ZhimaiResume-Bold.ttf"}:
                    route.fulfill(body=path.read_bytes(), content_type="text/css" if name.endswith(".css") else "font/ttf")
                else:
                    route.fulfill(body="<!doctype html><html><head></head><body></body></html>", content_type="text/html")

            page.route("**/*", serve)
            page.goto("http://resume-font.test/")
            page.add_style_tag(url="http://resume-font.test/fonts.css")
            for weight, style in ((400, "Regular"), (700, "Bold")):
                page.evaluate("""({text, weight}) => {
                    const line = document.createElement('div');
                    line.textContent = text;
                    line.style.cssText = `font-family: ZhimaiResume; font-weight: ${weight}; font-size: 20px; white-space: pre;`;
                    document.body.replaceChildren(line);
                }""", {"text": SAMPLE, "weight": weight})
                assert page.evaluate("""async ({text, weight}) => {
                    await document.fonts.load(`${weight} 20px ZhimaiResume`, text);
                    await document.fonts.ready;
                    return document.fonts.check(`${weight} 20px ZhimaiResume`, text);
                }""", {"text": SAMPLE, "weight": weight})
                assert page.locator("body").text_content() == SAMPLE
                data = page.pdf(format="A4")
                reader = PdfReader(io.BytesIO(data))
                assert len(reader.pages) == 1
                assert reader.pages[0].extract_text().rstrip("\n") == SAMPLE
                with fitz.open(stream=data, filetype="pdf") as document:
                    assert document[0].get_text().rstrip("\n") == SAMPLE
                    fonts = {font[3] for font in document[0].get_fonts()}
                    assert any(f"ZhimaiResume-{style}" in name for name in fonts), fonts
        finally:
            browser.close()
