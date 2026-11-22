"""Synthetic-only photo validation, persistence and real Chromium regressions."""
import asyncio
import base64
import copy
import importlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from PIL import Image, JpegImagePlugin
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

with patch.dict(os.environ, {
    "SUPABASE_URL": "https://supabase.invalid",
    "SUPABASE_SERVICE_KEY": "test-service-key",
    "DASHSCOPE_API_KEY": "test-key",
}):
    pdf = importlib.import_module("backend.resume_pdf")
    routes = importlib.import_module("backend.routers.resumes_v3")
    builder = importlib.import_module("backend.resume_builder_v3")
from backend.resume_template import load

PREFIX = "data:image/jpeg;base64,"
FIELDS = {"basics": {"name": "林知行", "target_role": "后端工程师", "email": "lin@example.com"},
          "summary": {"text": "精确原文：Python 接口测试与中文排版。"}}


def image_bytes(size=(120, 160), format="JPEG", **kwargs):
    buffer = io.BytesIO()
    Image.new("RGB", size, (45, 90, 150)).save(buffer, format, **kwargs)
    return buffer.getvalue()


def uri(data):
    return PREFIX + base64.b64encode(data).decode("ascii")


def with_photo(photo):
    return {**copy.deepcopy(FIELDS), "_meta": {"photo": photo, "internal": "never render"}}


def segment(marker, body):
    return b"\xff" + bytes([marker]) + (len(body) + 2).to_bytes(2, "big") + body


def invalid_photos():
    good = image_bytes()
    canonical = base64.b64encode(good).decode("ascii")
    # Nonzero pad bits decode to identical bytes, but are not canonical base64.
    padded = canonical
    assert padded.endswith("==")
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    noncanonical = padded[:-3] + alphabet[alphabet.index(padded[-3]) + 1] + "=="
    assert base64.b64decode(noncanonical) == good
    return {
        "remote": "https://example.invalid/photo.jpg",
        "local": "file:///private/photo.jpg",
        "svg": "data:image/svg+xml;base64,PHN2Zy8+",
        "wrong_mime": "data:image/png;base64," + canonical,
        "png_as_jpeg": uri(image_bytes(format="PNG")),
        "non_jpeg": uri(b"not a JPEG"),
        "bad_base64": PREFIX + "%%%",
        "whitespace": PREFIX + canonical + "\n",
        "extra_padding": PREFIX + canonical + "=",
        "noncanonical": PREFIX + noncanonical,
        "empty_string": "",
        "empty_payload": PREFIX,
        "truncated": uri(good[:-2]),
        "truncated_pixels": uri(good[:len(good) // 2] + b"\xff\xd9"),
        "too_many_bytes": uri(good + b"x" * 30000),
        "too_wide": uri(image_bytes((241, 160))),
        "too_tall": uri(image_bytes((120, 321))),
        "null": None,
        "number": 1,
        "boolean": False,
        "object": {"src": "https://example.invalid"},
        "array": [],
    }


class PhotoValidationTest(unittest.TestCase):
    def assert_photo_issue(self, exc):
        self.assertEqual(len(exc.issues), 1)
        self.assertEqual(exc.issues[0]["field_path"], "_meta.photo")
        self.assertEqual(exc.issues[0]["label"], "照片")
        self.assertTrue(exc.issues[0]["reason"])

    def test_valid_jpeg_preserves_identical_bytes_and_only_photo_metadata(self):
        for size in ((1, 1), (120, 160), (240, 320)):
            with self.subTest(size=size):
                photo = uri(image_bytes(size))
                fields = with_photo(photo)
                before = copy.deepcopy(fields)
                clean = pdf.validate_snapshot(load(), fields)
                self.assertEqual(clean, {**FIELDS, "_meta": {"photo": photo}})
                self.assertEqual(fields, before)

    def test_absent_photo_does_not_change_snapshot(self):
        for extra in ({}, {"_meta": None}, {"_meta": {}},
                      {"_meta": {"manual_override": ["basics.name"]}}):
            with self.subTest(extra=extra):
                self.assertEqual(pdf.validate_snapshot(load(), {**FIELDS, **extra}), FIELDS)

    def test_invalid_photos_have_actionable_422_and_never_start_worker(self):
        for name, photo in invalid_photos().items():
            with self.subTest(case=name), patch.object(pdf, "_run_worker") as worker:
                with self.assertRaises(HTTPException) as raised:
                    pdf.pdf_response(load(), with_photo(photo), "classic")
                self.assertEqual(raised.exception.status_code, 422)
                self.assert_photo_issue(pdf.PdfInputError(raised.exception.detail["issues"]))
                worker.assert_not_called()

    def test_byte_budget_checked_before_base64_decode_or_pillow(self):
        photo = PREFIX + "A" * 160004
        with patch("base64.b64decode", side_effect=AssertionError("decoded oversized input")), \
                patch.object(Image, "open", side_effect=AssertionError("opened oversized image")):
            with self.assertRaises(pdf.PdfInputError) as raised:
                pdf.validate_snapshot(load(), with_photo(photo))
        self.assert_photo_issue(raised.exception)

    def test_dimensions_checked_before_verify_or_pixel_decode(self):
        photo = uri(image_bytes((241, 320)))
        with patch.object(JpegImagePlugin.JpegImageFile, "load", side_effect=AssertionError("decoded oversized pixels")), \
                patch.object(JpegImagePlugin.JpegImageFile, "verify", side_effect=AssertionError("verified before bounds")):
            with self.assertRaises(pdf.PdfInputError) as raised:
                pdf.validate_snapshot(load(), with_photo(photo))
        self.assert_photo_issue(raised.exception)

    def test_valid_image_is_jpeg_only_verified_and_fully_loaded_without_transcode(self):
        photo = uri(image_bytes())
        real_open = Image.open
        real_verify = JpegImagePlugin.JpegImageFile.verify
        real_load = JpegImagePlugin.JpegImageFile.load
        calls = []

        def verify(image):
            calls.append("verify")
            return real_verify(image)

        def full_load(image):
            calls.append("load")
            return real_load(image)

        with patch.object(Image, "open", wraps=real_open) as opened, \
                patch.object(JpegImagePlugin.JpegImageFile, "verify", verify), \
                patch.object(JpegImagePlugin.JpegImageFile, "load", full_load), \
                patch.object(Image.Image, "save", side_effect=AssertionError("lossy transcode")):
            clean = pdf.validate_snapshot(load(), with_photo(photo))
        self.assertEqual(clean["_meta"]["photo"], photo)
        self.assertIn("verify", calls)
        self.assertIn("load", calls)
        self.assertLess(calls.index("verify"), calls.index("load"))
        self.assertTrue(opened.call_count)
        self.assertTrue(all(c.kwargs.get("formats") == ["JPEG"] for c in opened.call_args_list))

    def test_metadata_and_trailing_payload_rejected_without_silent_sanitizing(self):
        good = image_bytes()
        exif = Image.Exif()
        exif[0x0112] = 6  # Removing orientation silently would change the preview.
        exif[0x010E] = "synthetic private metadata"
        bad = [image_bytes(exif=exif), good + b"private trailing content"]
        for marker, body in ((0xE1, b"Exif\0\0private"), (0xE1, b"http://ns.adobe.com/xap/1.0/\0private"),
                             (0xE2, b"ICC_PROFILE\0private"), (0xFE, b"private comment")):
            bad += [good[:2] + segment(marker, body) + good[2:],
                    good[:-2] + segment(marker, body) + good[-2:]]
        for index, raw in enumerate(bad):
            with self.subTest(index=index):
                with self.assertRaises(pdf.PdfInputError) as raised:
                    pdf.validate_snapshot(load(), with_photo(uri(raw)))
                self.assert_photo_issue(raised.exception)

    def test_exact_byte_limit_and_progressive_jpeg_are_accepted(self):
        good = image_bytes()
        # JPEG permits FF fill bytes before a marker; this changes no pixels/metadata.
        boundary = good[:-2] + b"\xff" * (30000 - len(good)) + good[-2:]
        self.assertEqual(len(boundary), 30000)
        for raw in (boundary, image_bytes(progressive=True)):
            with self.subTest(byte_count=len(raw)):
                photo = uri(raw)
                self.assertEqual(pdf.validate_snapshot(load(), with_photo(photo))["_meta"], {"photo": photo})
        with patch("base64.b64decode", side_effect=AssertionError("decoded oversized input")):
            with self.assertRaises(pdf.PdfInputError):
                pdf.validate_snapshot(load(), with_photo(uri(boundary[:-2] + b"\xff" + boundary[-2:])))

    def test_full_load_failure_is_an_input_error(self):
        photo = uri(image_bytes())
        with patch.object(JpegImagePlugin.JpegImageFile, "load", side_effect=OSError("synthetic private decoder detail")):
            with self.assertRaises(pdf.PdfInputError) as raised:
                pdf.validate_snapshot(load(), with_photo(photo))
        self.assert_photo_issue(raised.exception)
        self.assertNotIn("private", str(raised.exception.issues))

    def test_total_pdf_budget_is_not_widened_for_photo(self):
        self.assertEqual(pdf.MAX_CONTENT_BYTES, 120000)
        fields = with_photo(uri(image_bytes()))
        fields["_meta"]["internal"] = "x" * 120000
        with self.assertRaises(pdf.PdfInputError):
            pdf.validate_snapshot(load(), fields)


class PhotoStorageTest(unittest.TestCase):
    def test_patch_rejects_invalid_photo_before_any_storage(self):
        app = FastAPI()
        app.include_router(routes.router)
        with TestClient(app, raise_server_exceptions=False) as client, \
                patch.object(routes, "_doc", side_effect=AssertionError("storage reached")) as read, \
                patch.object(routes.memory_v3, "patch_document") as write:
            for name, photo in invalid_photos().items():
                with self.subTest(case=name):
                    response = client.patch("/api/v3/resumes/synthetic", headers={"If-Match": "1"},
                                            json={"fields": with_photo(photo)})
                    self.assertEqual(response.status_code, 422)
                    self.assertIn("_meta.photo", response.text)
                    self.assertIn("照片", response.text)
            read.assert_not_called()
            write.assert_not_called()

    def test_patch_valid_photo_retains_all_other_metadata(self):
        photo = uri(image_bytes())
        fields = with_photo(photo)
        fields["_meta"]["manual_override"] = ["basics.name"]
        self.assertEqual(routes.PatchDocReq(fields=fields).fields, fields)
        self.assertEqual(routes.PatchDocReq(fields=FIELDS).fields, FIELDS)

    def test_regenerate_retains_photo_independent_of_keep_manual_without_model_image(self):
        photo = uri(image_bytes())
        doc = {"owner_id": "synthetic-owner", "target_role": "后端", "fields": with_photo(photo)}
        doc["fields"]["_meta"]["manual_override"] = ["basics.name"]
        before = copy.deepcopy(doc)
        for keep in (True, False):
            with self.subTest(keep_manual=keep), \
                    patch.object(builder.memory_v3, "facts_for_generation", return_value=[]), \
                    patch.object(builder.llm, "chat_json", return_value={"fields": {}}) as model:
                built = builder.regenerate_fields(doc, load(), keep_manual=keep)
                self.assertEqual(built["fields"]["_meta"]["photo"], photo)
                self.assertEqual(built["fields"]["basics"]["name"], FIELDS["basics"]["name"] if keep else "")
                model.assert_called_once()
                args = repr(model.call_args)
                self.assertNotIn(photo, args)
                self.assertNotIn("data:image", args)
                self.assertNotIn("never render", args)
        self.assertEqual(doc, before)

    def test_regenerate_without_photo_does_not_invent_one(self):
        for keep in (True, False):
            with self.subTest(keep_manual=keep), \
                    patch.object(builder.memory_v3, "facts_for_generation", return_value=[]), \
                    patch.object(builder.llm, "chat_json", return_value={"fields": {"_meta": {"photo": "model image"}}}):
                built = builder.regenerate_fields({"owner_id": "synthetic-owner", "fields": FIELDS},
                                                  load(), keep_manual=keep)
                self.assertNotIn("photo", built["fields"].get("_meta", {}))


class PhotoChromiumTest(unittest.TestCase):
    def test_both_layouts_embed_photo_and_preserve_exact_text(self):
        import fitz
        photo = uri(image_bytes())
        for layout in ("classic", "modern"):
            with self.subTest(layout=layout):
                data = pdf.render_pdf(load(), with_photo(photo), layout)
                with fitz.open(stream=data, filetype="pdf") as document:
                    images = [item for page in document for item in page.get_images()]
                    self.assertEqual(len(images), 1)
                    self.assertEqual(images[0][2:4], (120, 160))
                    text = "".join("".join(page.get_text().split()) for page in document)
                    for value in [*FIELDS["basics"].values(), FIELDS["summary"]["text"]]:
                        self.assertIn("".join(value.split()), text)
                    self.assertNotIn("never render", text)

    def test_broken_rendered_image_fails_before_print(self):
        from playwright.async_api import Page
        original = Page.evaluate

        async def inject(page, expression, arg=None):
            result = await original(page, expression, arg)
            if "window.resumeRender.renderResume" in expression:
                await original(page, """() => {
                    const image = document.createElement('img');
                    image.src = 'data:image/jpeg;base64,broken';
                    document.body.append(image);
                }""")
            return result

        with patch.object(Page, "evaluate", inject), patch.object(Page, "pdf", new_callable=AsyncMock) as print_pdf:
            with self.assertRaises(pdf.PdfInputError) as raised:
                asyncio.run(pdf._render_pdf(load(), FIELDS, "classic"))
        print_pdf.assert_not_called()
        self.assertEqual(raised.exception.issues[0]["field_path"], "_meta.photo")
        self.assertEqual(raised.exception.issues[0]["label"], "照片")

    def test_all_images_decode_before_print_and_csp_blocks_external_images(self):
        from playwright.async_api import Page
        original_evaluate, original_pdf = Page.evaluate, Page.pdf
        canvas_photos = []

        async def inject(page, expression, arg=None):
            result = await original_evaluate(page, expression, arg)
            if "window.resumeRender.renderResume" in expression:
                canvas_photos.append(await original_evaluate(page, """() => {
                    const canvas = document.createElement('canvas');
                    canvas.width = 120; canvas.height = 160;
                    const context = canvas.getContext('2d');
                    context.fillStyle = '#305a96'; context.fillRect(0, 0, 120, 160);
                    const src = canvas.toDataURL('image/jpeg', 0.8);
                    window.photoDecoded = [];
                    for (let index = 0; index < 2; index++) {
                        const image = document.createElement('img');
                        image.src = src;
                        const decode = image.decode.bind(image);
                        image.decode = async () => {
                            await decode();
                            await new Promise(resolve => setTimeout(resolve, 25 * (index + 1)));
                            window.photoDecoded.push(index);
                        };
                        document.body.append(image);
                    }
                    return src;
                }"""))
            return result

        async def print_after_decoding(page, **kwargs):
            self.assertEqual(await original_evaluate(page, "window.photoDecoded"), [0, 1])
            policy = await original_evaluate(page, "document.querySelector('meta[http-equiv]').content")
            self.assertIn("img-src data:", [part.strip() for part in policy.split(";")])
            self.assertIn("default-src 'none'", policy)
            # Playwright emits a request event even when CSP prevents transport.
            requests = []
            page.on("requestfailed", lambda request: requests.append(request))
            await original_evaluate(page, """async () => {
                const image = new Image();
                image.src = 'https://external.invalid/not-allowed.jpg';
                await image.decode().catch(() => {});
            }""")
            self.assertEqual([request.failure for request in requests if "external.invalid" in request.url],
                             ["csp"])
            return await original_pdf(page, **kwargs)

        with patch.object(Page, "evaluate", inject), patch.object(Page, "pdf", print_after_decoding):
            data = asyncio.run(pdf._render_pdf(load(), FIELDS, "modern"))
        self.assertTrue(data.startswith(b"%PDF-"))
        # The actual Chromium canvas encoder used by the client must pass the
        # backend's no-metadata policy, not just Pillow's synthetic JPEG encoder.
        self.assertEqual(pdf.validate_snapshot(load(), with_photo(canvas_photos[0]))["_meta"],
                         {"photo": canvas_photos[0]})

    def test_direct_script_import_rejects_bad_photo_without_disclosure(self):
        request = {"template": load().model_dump(), "fields": with_photo("https://private.invalid/photo"),
                   "layout": "classic"}
        result = subprocess.run([sys.executable, str(Path(pdf.__file__).resolve())],
                                cwd=tempfile.gettempdir(),
                                input=json.dumps(request).encode(), capture_output=True, timeout=10)
        self.assertEqual(result.returncode, pdf.WORKER_INPUT_ERROR)
        self.assertEqual(result.stderr, b"")
        issues = json.loads(result.stdout)
        self.assertEqual(issues[0]["field_path"], "_meta.photo")
        self.assertNotIn(b"private.invalid", result.stdout)


if __name__ == "__main__":
    unittest.main()
