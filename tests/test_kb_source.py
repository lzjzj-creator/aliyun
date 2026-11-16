import unittest

from backend.kb_source import to_mirror_url

BASE = "https://mirror.example.com"


class MirrorUrlTest(unittest.TestCase):
    def test_encoded_zero_width_is_removed(self):
        source = (
            f"{BASE}/0002%E5%A4%A7%E4%BA%8C/5.%E3%80%90%E8%80%83%E7%A0%94%E3%80%91/"
            "%E9%99%84%EF%BC%9A%E4%BB%A52027%E8%80%83%E7%A0%94%E4%B8%BA%E4%BE%8B%EF%BC%89%E2%80%8B"
        )
        self.assertEqual(
            to_mirror_url(source, BASE),
            f"{BASE}/0002%E5%A4%A7%E4%BA%8C/5.%E3%80%90%E8%80%83%E7%A0%94%E3%80%91/"
            "%E9%99%84%EF%BC%9A%E4%BB%A52027%E8%80%83%E7%A0%94%E4%B8%BA%E4%BE%8B%EF%BC%89/index.html",
        )

    def test_directory_url_gets_index(self):
        self.assertEqual(
            to_mirror_url(f"{BASE}/a/b/", BASE),
            f"{BASE}/a/b/index.html",
        )

    def test_index_suffix_maps_to_sibling_page(self):
        self.assertEqual(
            to_mirror_url(f"{BASE}/a/b/index/", BASE),
            f"{BASE}/a/b/index.html",
        )

    def test_index_slug_does_not_duplicate_index(self):
        self.assertEqual(
            to_mirror_url(f"{BASE}/a/b/index/index.html", BASE),
            f"{BASE}/a/b/index.html",
        )

    def test_file_url_is_unchanged(self):
        self.assertEqual(
            to_mirror_url(f"{BASE}/a/b/index.html", BASE),
            f"{BASE}/a/b/index.html",
        )


if __name__ == "__main__":
    unittest.main()