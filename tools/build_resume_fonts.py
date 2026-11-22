"""Build deterministic, OFL-licensed static resume fonts without cmap aliases.

Build dependency: fonttools==4.63.0
Run from any directory: python /path/to/tools/build_resume_fonts.py
The original NotoSansSC.ttf and OFL.txt are read-only inputs.
"""

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import fontTools
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "backend" / "resume_assets"
FONTTOOLS_VERSION = "4.63.0"
SOURCE_SHA256 = "a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da"
OFL_SHA256 = "1c05c68c34f9708415aada51f17e1b0092d2cea709bf4a94cd38114f9e73d7d9"
FACES = ((400, "Regular"), (700, "Bold"))
CSS = """@font-face {
  font-family: "ZhimaiResume";
  src: url("./ZhimaiResume-Regular.ttf") format("truetype");
  font-style: normal;
  font-weight: 400;
  font-display: block;
}

@font-face {
  font-family: "ZhimaiResume";
  src: url("./ZhimaiResume-Bold.ttf") format("truetype");
  font-style: normal;
  font-weight: 700;
  font-display: block;
}
"""


def deduplicate_unicode_cmap(font: TTFont) -> int:
    """Give every nominal Unicode codepoint its own glyph ID, in place.

    Existing glyph IDs and composite component references stay unchanged.
    A deep glyf copy (not a wrapper composite) preserves outlines, hinting and
    component flags. Layout tables keep their original, still-valid glyph IDs.
    Format 14 default UVSes follow the rewritten nominal cmap; non-default
    alternates are retained, including explicit references to nominal glyphs.
    """
    if any(tag in font for tag in ("fvar", "gvar", "HVAR", "VVAR", "MVAR", "cvar")):
        raise ValueError("Instantiate the variable font before cloning glyphs")
    original = {}
    tables = [table for table in font["cmap"].tables if table.isUnicode()]
    for table in tables:
        for codepoint, glyph_name in table.cmap.items():
            if original.setdefault(codepoint, glyph_name) != glyph_name:
                raise ValueError(f"Conflicting Unicode cmap entries for U+{codepoint:04X}")

    glyph_order = list(font.getGlyphOrder())
    alias_count = len(original) - len(set(original.values()))
    if len(glyph_order) + alias_count > 65535:
        raise ValueError("Cloning aliases would exceed the TrueType glyph limit")
    glyph_names = set(glyph_order)
    owners = set()
    remapped = dict(original)
    for codepoint, glyph_name in sorted(original.items()):
        if glyph_name not in owners:
            owners.add(glyph_name)
            continue
        stem = f"zhimai.u{codepoint:06X}"
        clone_name = stem
        suffix = 1
        while clone_name in glyph_names:
            clone_name = f"{stem}.{suffix}"
            suffix += 1
        font["glyf"][clone_name] = deepcopy(font["glyf"][glyph_name])
        for tag in ("hmtx", "vmtx"):
            if tag in font:
                font[tag].metrics[clone_name] = deepcopy(font[tag].metrics[glyph_name])
        glyph_order.append(clone_name)
        glyph_names.add(clone_name)
        remapped[codepoint] = clone_name

    font.setGlyphOrder(glyph_order)
    for table in tables:
        table.cmap = {codepoint: remapped[codepoint] for codepoint in table.cmap}
        if table.format == 14:
            table.uvsDict = {
                selector: [
                    (codepoint, remapped[codepoint]
                     if glyph_name is not None and glyph_name == original.get(codepoint)
                     else glyph_name)
                    for codepoint, glyph_name in entries
                ]
                for selector, entries in table.uvsDict.items()
            }
    return alias_count


def _rename_static_face(font: TTFont, weight: int, style: str) -> None:
    # Replace ALL localized identity records, not only the English family name.
    # Copyright, attribution, trademark and OFL name records remain verbatim.
    identity_ids = {1, 2, 3, 4, 5, 6, 16, 17, 18, 21, 22, 25}
    names = font["name"]
    names.names = [record for record in names.names if record.nameID not in identity_ids]
    for name_id, value in {
        1: "Zhimai Resume",
        2: style,
        3: f"2.004;ZhimaiResume-{style};build-1",
        4: f"Zhimai Resume {style}",
        5: "Version 2.004; Zhimai Resume build 1",
        6: f"ZhimaiResume-{style}",
        16: "Zhimai Resume",
        17: style,
    }.items():
        names.setName(value, name_id, 3, 1, 0x409)
    font["OS/2"].usWeightClass = weight
    font["OS/2"].fsSelection &= ~((1 << 0) | (1 << 5) | (1 << 6))
    font["OS/2"].fsSelection |= (1 << 5) if weight == 700 else (1 << 6)
    font["head"].macStyle &= ~3
    if weight == 700:
        font["head"].macStyle |= 1


def _asset_metadata(path: Path) -> dict:
    data = path.read_bytes()
    return {"file": path.name, "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def build_fonts(source_path: Path, output_dir: Path) -> dict:
    """Build both faces and shared CSS, returning reproducible provenance data."""
    if fontTools.__version__ != FONTTOOLS_VERSION:
        raise RuntimeError(f"Reproducible builds require fonttools=={FONTTOOLS_VERSION}")
    source_path, output_dir = Path(source_path).resolve(), Path(output_dir).resolve()
    if hashlib.sha256(source_path.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise ValueError("NotoSansSC.ttf does not match the pinned official source hash")
    if hashlib.sha256((source_path.parent / "OFL.txt").read_bytes()).hexdigest() != OFL_SHA256:
        raise ValueError("OFL.txt does not match the pinned original license hash")
    output_dir.mkdir(parents=True, exist_ok=True)
    generated = []
    for weight, style in FACES:
        with TTFont(source_path, recalcTimestamp=False) as source:
            timestamps = source["head"].created, source["head"].modified
            font = instantiateVariableFont(source, {"wght": weight}, inplace=True)
            clone_count = deduplicate_unicode_cmap(font)
            _rename_static_face(font, weight, style)
            font.recalcTimestamp = False
            font["head"].created, font["head"].modified = timestamps
            destination = output_dir / f"ZhimaiResume-{style}.ttf"
            font.save(destination, reorderTables=True)
        generated.append({**_asset_metadata(destination), "weight": weight, "cloned_glyphs": clone_count})
    css_path = output_dir / "fonts.css"
    css_path.write_bytes(CSS.encode("utf-8"))
    generated.append(_asset_metadata(css_path))
    return {
        "family": "Zhimai Resume",
        "css_family": "ZhimaiResume",
        "build_script": "tools/build_resume_fonts.py",
        "build_command": "python tools/build_resume_fonts.py",
        "dependency": f"fonttools=={FONTTOOLS_VERSION}",
        "source_file": "NotoSansSC.ttf",
        "source_sha256": SOURCE_SHA256,
        "license": "OFL-1.1",
        "license_file": "OFL.txt",
        "license_sha256": OFL_SHA256,
        "license_note": "Derived fonts remain under OFL-1.1; original copyright, attribution and license name records and the complete OFL.txt are preserved. Upstream source bytes are unchanged.",
        "strategy": [
            "Fully instantiate the official variable TrueType font at wght=400 and wght=700 with fontTools.varLib.instancer; do not subset.",
            "Visit nominal Unicode codepoints in ascending order. Keep the first glyph ID and deep-copy glyf plus hmtx/vmtx for every alias, appending glyphOrder without changing existing IDs or composite references.",
            "Rewrite every Unicode cmap subtable consistently; preserve format-14 variation sequences and their alternate glyphs. No input text normalization or substitution.",
            "Preserve GSUB, GPOS, GDEF and BASE layout tables and rename derived font identities to Zhimai Resume Regular/Bold.",
        ],
        "reproducibility": "Pinned source SHA-256 and fonttools version; fixed codepoint/glyph order, upstream head timestamps, recalcTimestamp=False, sorted sfnt tables, UTF-8/LF CSS, and no build-time timestamps or absolute paths.",
        "assets": generated,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ASSETS)
    args = parser.parse_args()
    manifest = json.loads((ASSETS / "font-source.json").read_text(encoding="utf-8"))
    manifest["generated"] = build_fonts(ASSETS / "NotoSansSC.ttf", args.output_dir)
    destination = args.output_dir / "font-source.json"
    destination.write_bytes((json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    for asset in manifest["generated"]["assets"]:
        print(f"{asset['file']}: {asset['size_bytes']} bytes, sha256={asset['sha256']}")


if __name__ == "__main__":
    main()
