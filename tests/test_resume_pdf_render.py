import copy
import io
import json
import os
import time
import unittest
from pathlib import Path

from backend.resume_pdf import PdfInputError, render_pdf
from backend.resume_template import load


BASE = {
    "basics": {"name": "林知行", "phone": "13800000000", "email": "lin@example.com",
               "target_role": "后端开发工程师", "city": "杭州"},
    "education": [{"school": "示例大学", "major": "计算机科学与技术", "degree": "本科",
                   "period": "2022.09–2026.06", "courses": ["数据结构", "操作系统"]}],
    "internships": [{"company": "示例科技", "role": "研发实习生", "period": "2025.06–2025.09",
                     "bullets": ["维护课程检索接口，补充错误处理与自动化测试。", "与设计团队协作，完善无障碍交互。"]}],
    "projects": [{"name": "课程检索系统", "role": "后端开发", "period": "2025.03–2025.06",
                  "tech": ["Python", "FastAPI", "PostgreSQL"],
                  "bullets": ["设计结构化检索接口并验证分页结果。", "编写回归测试覆盖重复提交与断线重试。"]}],
    "skills": {"groups": [{"name": "开发", "items": ["Python", "SQL", "Git"]}]},
    "awards": {"items": [{"text": "校级优秀项目奖", "period": "2025.06"}]},
    "summary": {"text": "关注工程质量，能独立定位问题并完成交付验证。"},
}


def samples():
    cases = []
    for index in range(12):
        fields = copy.deepcopy(BASE)
        if index == 1:
            fields["internships"] = []
            fields["awards"] = {"items": []}
            fields["summary"] = {"text": ""}
        elif index == 2:
            fields["projects"][0]["name"] = "面向跨区域多团队协同的数据平台与高可用服务" * 6
        elif index == 3:
            fields["basics"]["blog"] = "https://portfolio.example/" + "very-long-path/" * 22
        elif index == 4:
            fields["projects"][0]["name"] = "OpenSearch Platform / UTF-8 中文与 English 2026"
            fields["skills"]["groups"][0]["items"] += ["C++", "C#", "Node.js", "CI/CD"]
        elif index == 5:
            fields["projects"][0]["bullets"] = [f"阶段{i}：验证中文数据处理、服务接口、分页与读取顺序。" * 4 for i in range(18)]
        elif index == 6:
            fields["projects"][0]["bullets"] = ["负责长段落的性能分析与回归测试；" * 200]
        elif index == 7:
            fields["summary"]["text"] = '<script>alert("xss")</script> & <img src="http://127.0.0.1/private">'
        elif index == 8:
            fields["basics"]["phone"] = ""
            fields["education"] = []
            fields["projects"] = []
        elif index == 9:
            fields["skills"]["groups"] += [{"name": "工程实践", "items": ["测试", "监控", "文档"]}, {"name": "", "items": []}]
            fields["awards"]["items"] += [{"text": "", "period": ""}]
        elif index == 10:
            fields["internships"][0]["company"] = "VeryLongCompanyNameWithoutAnySpaces" * 9
        elif index == 11:
            fields["projects"] = [copy.deepcopy(fields["projects"][0]) for _ in range(12)]
            for i, project in enumerate(fields["projects"]):
                project["name"] = f"项目{i + 1}：接口与质量验证"
        cases.append((f"case-{index + 1:02}", fields, "modern" if index % 2 else "classic"))
    return cases


class PdfRenderTest(unittest.TestCase):
    def test_twelve_synthetic_resumes_have_extractable_chinese_a4_text(self):
        import fitz
        from pypdf import PdfReader

        artifact_dir = os.getenv("RESUME_PDF_ARTIFACT_DIR")
        metrics = []
        for name, fields, layout in samples():
            with self.subTest(name=name, layout=layout):
                started = time.monotonic()
                data = render_pdf(load(), fields, layout)
                render_ms = round((time.monotonic() - started) * 1000)
                self.assertTrue(data.startswith(b"%PDF-"))
                reader = PdfReader(io.BytesIO(data))
                text = "\n".join(page.extract_text() for page in reader.pages)
                compact = "".join(text.split())
                self.assertIn("林知行", compact)
                self.assertIn("lin@example.com", compact)
                self.assertIn("后端开发工程师", compact)
                section_order = (["education", "internships", "projects", "skills", "awards", "summary"]
                                 if layout == "classic" else
                                 # 双栏：侧栏教育+奖项 → 主栏技能+实习+项目+自评（DOM 序=提取序）
                                 ["education", "awards", "skills", "internships", "projects", "summary"])
                titles = {section.key: section.title for section in load().sections}
                populated = {
                    "education": bool(fields.get("education")),
                    "internships": bool(fields.get("internships")),
                    "projects": bool(fields.get("projects")),
                    "skills": any(g.get("name") or g.get("items") for g in fields.get("skills", {}).get("groups", [])),
                    "awards": any(g.get("text") or g.get("period") for g in fields.get("awards", {}).get("items", [])),
                    "summary": bool(fields.get("summary", {}).get("text", "").strip()),
                }
                markers = ["林知行", "后端开发工程师", "lin@example.com"] + [titles[key] for key in section_order if populated[key]]
                positions = [compact.find(marker) for marker in markers]
                self.assertTrue(all(position >= 0 for position in positions), markers)
                self.assertEqual(positions, sorted(set(positions)), "PDF extraction changed the intended reading order")
                self.assertNotIn("待补充", text)
                self.assertNotIn("resume.invalid", text)
                self.assertNotIn("�", text)
                self.assertGreaterEqual(len(reader.pages), 1)
                self.assertLessEqual(len(reader.pages), 6)
                if name in ("case-06", "case-07", "case-12"):
                    self.assertGreaterEqual(len(reader.pages), 2)
                for page in reader.pages:
                    self.assertAlmostEqual(float(page.mediabox.width), 595.28, delta=1)
                    self.assertAlmostEqual(float(page.mediabox.height), 841.89, delta=1)
                    self.assertTrue(page.extract_text().strip(), "Unexpected blank page")
                document = fitz.open(stream=data, filetype="pdf")
                fonts = {font[3] for page in document for font in page.get_fonts()}
                self.assertTrue(any("ZhimaiResume" in font or "Zhimai-Resume" in font for font in fonts), fonts)
                for page in document:
                    for block in page.get_text("blocks"):
                        self.assertGreaterEqual(block[0], 30)
                        self.assertLessEqual(block[2], 570)
                    lines = ["".join(span["text"] for span in line["spans"]).strip()
                             for block in page.get_text("dict")["blocks"] if block["type"] == 0
                             for line in block["lines"]]
                    lines = [line for line in lines if line]
                    for index, line in enumerate(lines):
                        if line in titles.values():
                            self.assertLess(index + 1, len(lines), "Section heading stranded at page end")
                            self.assertNotIn(lines[index + 1], titles.values())
                purpose = "synthetic-display" if name in ("case-01", "case-02") else "edge-case-only-not-a-showcase"
                metrics.append({"case": name, "layout": layout, "pages": len(reader.pages),
                                "purpose": purpose, "reading_order_checked": True, "orphan_headings_checked": True,
                                "pdf_bytes": len(data), "render_ms": render_ms, "fonts": sorted(fonts)})
                if artifact_dir:
                    out = Path(artifact_dir)
                    (out / f"{name}-{layout}.pdf").write_bytes(data)
                    for page_number, page in enumerate(document):
                        page.get_pixmap(matrix=fitz.Matrix(1, 1)).save(str(out / f"{name}-page-{page_number + 1}.png"))
        if artifact_dir:
            (Path(artifact_dir) / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    def test_incomplete_sentence_is_rejected_not_silently_changed(self):
        fields = copy.deepcopy(BASE)
        fields["projects"][0]["bullets"][0] = "提升检索性能【待补充：百分比】"
        with self.assertRaises(PdfInputError) as raised:
            render_pdf(load(), fields)
        self.assertTrue(raised.exception.issues)


if __name__ == "__main__":
    unittest.main()
