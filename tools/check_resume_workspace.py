"""Local browser acceptance: run v3_dev_stub.py and Vite on 127.0.0.1:5175 first."""
import base64
import copy
import io
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.test_resume_pdf_render import BASE

URL = os.getenv("RESUME_WORKSPACE_URL", "http://127.0.0.1:5175")
TOKEN = "dev-owner-token-0000000000000000"
OUT = ROOT / "research" / "v3.1-pdf"


def pdf_text(path):
    reader = PdfReader(io.BytesIO(path.read_bytes()))
    return "".join("".join(page.extract_text().split()) for page in reader.pages)


def photo_fixture():
    from PIL import Image, ImageDraw, PngImagePlugin

    image = Image.new("RGB", (480, 640), "#e5ebee")
    draw = ImageDraw.Draw(image)
    draw.ellipse((140, 95, 340, 295), fill="#537782")
    draw.rounded_rectangle((65, 330, 415, 690), radius=140, fill="#537782")
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Comment", "synthetic-private-metadata")
    output = io.BytesIO()
    image.save(output, format="PNG", pnginfo=metadata)
    return {"name": "synthetic-portrait.png", "mimeType": "image/png", "buffer": output.getvalue()}


def photo_source(page):
    image = page.locator(".resume-paper img")
    expect(image).to_be_visible()
    expect(image).to_have_js_property("complete", True)
    assert 0 < image.evaluate("e => e.naturalWidth") <= 240
    assert 0 < image.evaluate("e => e.naturalHeight") <= 320
    source = image.get_attribute("src")
    assert source.startswith("data:image/jpeg;base64,")
    encoded = base64.b64decode(source.split(",", 1)[1], validate=True)
    assert len(encoded) <= 30000
    assert b"synthetic-private-metadata" not in encoded
    return source


def assert_pdf_photo(path, present=True):
    import fitz

    with fitz.open(path) as document:
        images = [image for page in document for image in page.get_images()]
        assert bool(images) == present
        if present:
            assert images[0][2:4] == (240, 320)


def main():
    assert OUT.is_dir()
    assert urlparse(URL).scheme == "http" and urlparse(URL).hostname == "127.0.0.1"
    results = []
    errors = []
    network = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
        context.route("**/*", lambda route: route.continue_() if urlparse(route.request.url).hostname == "127.0.0.1" else route.abort())
        context.add_init_script(f"localStorage.setItem('zhimai.owner_token', {json.dumps(TOKEN)});")
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: network.append((request.method, urlparse(request.url).path)))
        headers = {"X-Owner-Token": TOKEN}
        created = context.request.post(URL + "/api/v3/resumes", headers=headers, data={"title": "本机浏览器验收"})
        assert created.ok, created.status
        doc_id = created.json()["document"]["id"]
        doc_path = f"/api/v3/resumes/{doc_id}"
        seeded = context.request.patch(URL + doc_path, headers=headers, data={"fields": BASE})
        assert seeded.ok
        page.goto(URL + "/#/resume/library/" + doc_id)
        expect(page.locator(".resume-paper")).to_be_visible()
        page.evaluate("document.fonts.ready")
        expect(page.locator(".save-state")).to_have_text("已保存")
        assert page.locator(".resume-paper .gap, .resume-paper .compared, .resume-paper .hl").count() == 0
        assert page.locator(".resume-paper img").count() == 0
        upload = page.locator(".rff input[type=file]")
        page.evaluate("""() => {
            window.__photoDecoder = window.createImageBitmap;
            window.createImageBitmap = (...args) => new Promise((resolve, reject) => {
                window.__finishPhoto = () => window.__photoDecoder(...args).then(resolve, reject);
            });
        }""")
        upload.set_input_files(photo_fixture(), timeout=5000)
        expect(page.locator(".save-state")).to_have_text("照片处理中…")
        for name in ("导出 PDF", "保存修改", "智能优化", "复制为新稿"):
            expect(page.get_by_role("button", name=name, exact=True)).to_be_disabled()
        page.wait_for_function("typeof window.__finishPhoto === 'function'")
        page.evaluate("() => { window.createImageBitmap = window.__photoDecoder; window.__finishPhoto(); }")
        source = photo_source(page)
        upload.set_input_files({"name": "unsafe.svg", "mimeType": "image/svg+xml", "buffer": b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'})
        expect(page.locator(".rff [role=alert]")).to_be_visible()
        assert photo_source(page) == source
        page.get_by_label("姓名", exact=False).fill("林知行验收")
        expect(page.locator(".save-state")).to_have_text("有未保存修改")
        before = len(network)
        try:
            with page.expect_download(timeout=45000) as download:
                page.get_by_role("button", name="导出 PDF", exact=True).click()
        except Exception:
            page.screenshot(path=str(OUT / "browser-failure.png"), full_page=False)
            print(json.dumps({"feedback": page.locator(".lib-feedback").all_text_contents(), "page_errors": errors, "requests": network[before:]}, ensure_ascii=False))
            raise
        desktop = OUT / "browser-library-desktop.pdf"
        download.value.save_as(desktop)
        assert "林知行验收" in pdf_text(desktop)
        assert_pdf_photo(desktop)
        stored = context.request.get(URL + doc_path, headers=headers).json()
        assert stored["fields"]["_meta"]["photo"] == source
        calls = network[before:]
        assert calls.index(("PATCH", doc_path)) < calls.index(("POST", doc_path + "/pdf"))
        expect(page.locator(".save-state")).to_have_text("已保存")
        page.screenshot(path=str(OUT / "browser-library-desktop.png"), full_page=False)
        results.append("library: dirty save before PDF, returned snapshot, desktop preview")

        for width in (390, 320):
            page.set_viewport_size({"width": width, "height": 844})
            expect(page.locator(".lib-editor")).to_be_visible()
            expect(page.locator(".lib-preview")).to_be_hidden()
            page.get_by_role("button", name="预览", exact=True).click()
            expect(page.locator(".resume-paper")).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"overflow at {width}"
            assert abs(page.locator(".resume-paper").evaluate("e => e.offsetWidth") - 794) <= 1
            page.screenshot(path=str(OUT / f"browser-library-mobile-{width}.png"), full_page=False)
            if width == 390:
                with page.expect_download(timeout=45000) as download:
                    page.get_by_role("button", name="导出 PDF", exact=True).click()
                mobile = OUT / "browser-library-mobile.pdf"
                download.value.save_as(mobile)
                assert pdf_text(mobile) == pdf_text(desktop)
                assert_pdf_photo(mobile)
                assert photo_source(page) == source
                assert [p.mediabox for p in PdfReader(mobile).pages] == [p.mediabox for p in PdfReader(desktop).pages]
            page.get_by_role("button", name="编辑", exact=True).click()
        results.append("library: 390/320px no overflow, fixed A4, mobile PDF equals desktop")

        page.set_viewport_size({"width": 1440, "height": 1000})
        page.get_by_label("姓名", exact=False).fill("")
        before = len(network)
        page.get_by_role("button", name="导出 PDF", exact=True).click()
        expect(page.locator(".lib-feedback")).to_contain_text("请填写姓名")
        assert not any(path.endswith("/pdf") for _, path in network[before:])
        page.get_by_label("姓名", exact=False).fill("林知行重试")
        failed = lambda route: route.fulfill(status=409, json={"detail": {"message": "版本冲突"}}) if route.request.method == "PATCH" else route.continue_()
        page.route("**" + doc_path, failed)
        before = len(network)
        page.get_by_role("button", name="导出 PDF", exact=True).click()
        expect(page.locator(".lib-feedback")).to_contain_text("版本冲突")
        expect(page.locator(".save-state")).to_have_text("有未保存修改")
        assert not any(path.endswith("/pdf") for _, path in network[before:])
        page.unroute("**" + doc_path, failed)
        page.get_by_role("button", name="保存修改", exact=True).click()
        expect(page.locator(".save-state")).to_have_text("已保存")
        results.append("library: required data gate, injected conflict preserves edits and blocks PDF, retry")

        def optimize_plan(route):
            assert "_meta" not in route.request.post_data_json["fields"]
            route.fulfill(json={"sections": [{"key": "summary", "title": "自我评价", "optimizable": True, "status": "pending"}]})

        page.route("**/api/v2/resume/optimize/plan", optimize_plan)
        page.route("**/api/v2/resume/optimize/section", lambda route: route.fulfill(json={"status": "done", "optimized": {"text": "这是一段已明确采纳的合成优化稿"}, "change_log": [], "placeholders": []}))
        page.get_by_role("button", name="智能优化", exact=True).click()
        expect(page.locator(".resume-paper")).to_contain_text("合成优化稿")
        before = len(network)
        page.get_by_role("button", name="导出 PDF", exact=True).click()
        expect(page.locator(".lib-feedback")).to_contain_text("请先采纳优化稿")
        assert not any(path.endswith("/pdf") for _, path in network[before:])
        before = len(network)
        page.get_by_role("button", name="放弃优化稿", exact=True).click()
        expect(page.locator(".resume-paper")).not_to_contain_text("合成优化稿")
        expect(page.locator(".save-state")).to_have_text("已保存")
        assert not any(method == "PATCH" for method, _ in network[before:])
        page.get_by_role("button", name="智能优化", exact=True).click()
        expect(page.locator(".resume-paper")).to_contain_text("合成优化稿")
        page.get_by_role("button", name="采纳优化稿", exact=True).click()
        page.get_by_role("combobox", name=re.compile("版式")).select_option("modern")
        with page.expect_download(timeout=45000) as download:
            page.get_by_role("button", name="导出 PDF", exact=True).click()
        optimized = OUT / "browser-library-optimized.pdf"
        download.value.save_as(optimized)
        assert "合成优化稿" in pdf_text(optimized)
        assert_pdf_photo(optimized)
        assert photo_source(page) == source
        results.append("library: discard preserves original without PATCH; modern PDF exports adopted text")
        page.reload()
        expect(page.locator(".save-state")).to_have_text("已保存")
        assert photo_source(page) == source
        page.get_by_role("button", name=re.compile("移除照片|删除照片")).click()
        expect(page.locator(".resume-paper img")).to_have_count(0)
        with page.expect_download(timeout=45000) as download:
            page.get_by_role("button", name="导出 PDF", exact=True).click()
        without_photo = OUT / "browser-library-without-photo.pdf"
        download.value.save_as(without_photo)
        assert_pdf_photo(without_photo, False)
        stored = context.request.get(URL + doc_path, headers=headers).json()
        assert "photo" not in stored["fields"].get("_meta", {})
        results.append("photo: pending conversion blocks actions, metadata removal, invalid SVG rejected, saved/reloaded, two-layout PDF embedding, removal")
        page.locator(".rff input[type=file]").set_input_files(photo_fixture())
        source = photo_source(page)
        expect(page.locator(".save-state")).to_have_text("有未保存修改")
        before = len(network)
        page.get_by_role("button", name="复制为新稿", exact=True).click()
        page.wait_for_url(lambda url: "/resume/library/" in url and not url.endswith(doc_id))
        expect(page.locator(".save-state")).to_have_text("已保存")
        assert photo_source(page) == source
        calls = network[before:]
        assert calls.index(("PATCH", doc_path)) < calls.index(("POST", doc_path + "/duplicate"))
        copied_id = page.url.rsplit("/", 1)[1]
        copied = context.request.get(URL + "/api/v3/resumes/" + copied_id, headers=headers).json()
        assert copied["fields"]["_meta"]["photo"] == source
        results.append("library: copying unsaved photo persists current fields before duplicating")
        history_doc = context.request.post(URL + "/api/v3/resumes", headers=headers, data={"title": "十版历史保留验收"}).json()["document"]["id"]
        history_path = "/api/v3/resumes/" + history_doc
        historical_fields = copy.deepcopy(BASE)
        historical_fields["_meta"] = {"photo": source}
        assert context.request.patch(URL + history_path, headers=headers, data={"fields": historical_fields}).ok
        for index in range(10):
            current_fields = copy.deepcopy(BASE)
            current_fields["summary"]["text"] = f"合成历史更新 {index + 1}"
            assert context.request.patch(URL + history_path, headers=headers, data={"fields": current_fields}).ok
        page.goto(URL + "/#/resume/library/" + history_doc)
        expect(page.locator(".lv-restore")).to_have_count(10)
        page.get_by_label("姓名", exact=False).fill("尚未保存的姓名")
        before = len(network)
        page.locator(".lv-restore").last.click()
        expect(page.locator(".lib-feedback")).to_contain_text("有未保存修改")
        assert not any(method == "PATCH" or path.endswith("/restore") for method, path in network[before:])
        versions = context.request.get(URL + history_path + "/versions", headers=headers).json()["versions"]
        assert len(versions) == 10 and versions[-1]["version"] == 1
        page.reload()
        expect(page.locator(".save-state")).to_have_text("已保存")
        expect(page.locator(".lv-restore")).to_have_count(10)
        page.once("dialog", lambda dialog: dialog.accept())
        page.locator(".lv-restore").last.click()
        expect(page.locator(".resume-paper")).to_contain_text(BASE["summary"]["text"])
        assert photo_source(page) == source
        results.append("library: dirty restore preserves ten snapshots; clean restore recovers oldest text and photo")

        quote = '<img src="https://example.invalid/source" onerror="alert(1)">仅用于核对的合成出处'
        draft = {"schema_version": 1, "template_version": "1.0.0", "fields": copy.deepcopy(BASE), "evidence_map": {"basics.name": [{"quote": quote, "session_id": "synthetic-source", "msg_id": "synthetic-message"}]}, "gaps": []}
        draft["fields"]["_meta"] = {"manual_override": ["summary.text"]}
        page.evaluate("draft => { localStorage.removeItem('zhimai.owner_token'); localStorage.setItem('resume_draft_v1', JSON.stringify(draft)); }", draft)
        page.goto(URL + "/#/resume")
        page.reload()
        expect(page.get_by_role("button", name="② 修改预览", exact=True)).to_be_enabled()
        assert page.locator(".wiz-lib").evaluate("e => getComputedStyle(e).color !== getComputedStyle(e).backgroundColor"), "Library navigation text is invisible"
        page.get_by_role("button", name="② 修改预览", exact=True).click()
        expect(page.locator(".resume-paper")).to_be_visible()
        page.locator(".evidence-panel summary").click()
        expect(page.locator(".evidence-panel")).to_contain_text(quote)
        assert page.locator(".evidence-panel img, .resume-paper .evidence-panel").count() == 0
        page.locator(".rff input[type=file]").set_input_files(photo_fixture())
        wizard_source = photo_source(page)
        page.get_by_label("姓名", exact=False).fill("林知行草稿")
        expect(page.locator(".save-state")).to_have_text("已保存到本机")
        saved_photo = page.evaluate("JSON.parse(localStorage.getItem('resume_draft_v1')).fields._meta.photo")
        assert saved_photo == wizard_source
        with page.expect_download(timeout=45000) as download:
            page.get_by_role("button", name="导出 PDF", exact=True).click()
        wizard = OUT / "browser-wizard.pdf"
        download.value.save_as(wizard)
        assert "林知行草稿" in pdf_text(wizard)
        assert_pdf_photo(wizard)
        assert "仅用于核对" not in pdf_text(wizard)
        page.get_by_role("button", name="③ 一键优化", exact=True).click()
        page.get_by_role("button", name="一键优化", exact=True).click()
        expect(page.locator(".resume-paper")).to_contain_text("合成优化稿")
        page.get_by_role("button", name="放弃优化稿", exact=True).click()
        expect(page.locator(".resume-paper")).not_to_contain_text("合成优化稿")
        expect(page.locator(".save-state")).to_have_text("已保存到本机")
        page.get_by_role("button", name="③ 一键优化", exact=True).click()
        page.get_by_role("button", name="一键优化", exact=True).click()
        expect(page.locator(".resume-paper")).to_contain_text("合成优化稿")
        page.get_by_role("button", name="← 返回修改", exact=True).click()
        page.get_by_role("button", name="移除照片", exact=True).click()
        page.get_by_role("button", name="③ 一键优化", exact=True).click()
        expect(page.get_by_role("button", name="采纳优化稿", exact=True)).to_have_count(0)
        expect(page.locator(".resume-paper img")).to_have_count(0)
        expect(page.locator(".resume-paper")).not_to_contain_text("合成优化稿")
        with page.expect_download(timeout=45000) as download:
            page.get_by_role("button", name="导出 PDF", exact=True).click()
        edited = OUT / "browser-wizard-after-photo-removal.pdf"
        download.value.save_as(edited)
        assert_pdf_photo(edited, False)
        assert "合成优化稿" not in pdf_text(edited)
        page.get_by_role("button", name="② 修改预览", exact=True).click()
        page.locator(".rff input[type=file]").set_input_files(photo_fixture())
        assert photo_source(page) == wizard_source
        results.append("wizard: editing photos invalidates older optimization without restoring removed images")
        page.screenshot(path=str(OUT / "browser-wizard-desktop.png"), full_page=False)
        page.set_viewport_size({"width": 390, "height": 844})
        page.get_by_role("button", name="预览", exact=True).click()
        expect(page.locator(".wz-panel")).to_be_hidden()
        expect(page.locator(".resume-paper")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(OUT / "browser-wizard-mobile.png"), full_page=False)
        results.append("wizard: local snapshot, escaped evidence excluded from PDF, discard optimization, responsive preview")
        assert not errors, errors
        browser.close()
    (OUT / "browser-results.json").write_text(json.dumps({"mode": "local fixture API; real Chromium PDF; injected error and optimization responses", "passed": results, "page_errors": errors}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": results, "page_errors": errors}, ensure_ascii=False))


if __name__ == "__main__":
    main()
