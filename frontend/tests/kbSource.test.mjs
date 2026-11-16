import assert from "node:assert/strict";
import test from "node:test";

import { toMirrorHref } from "../src/kbSource.mjs";

const BASE = "https://mirror.example.com";

test("encoded zero-width character is removed before re-encoding", () => {
  const source = `${BASE}/a/%E6%89%8B%E5%86%8C%E2%80%8B`;
  assert.equal(
    toMirrorHref(source, BASE),
    `${BASE}/a/${encodeURIComponent("手册")}/index.html`,
  );
});

test("index suffix maps to sibling page", () => {
  assert.equal(toMirrorHref(`${BASE}/a/index/`, BASE), `${BASE}/a/index.html`);
});

test("index slug does not duplicate index", () => {
  assert.equal(
    toMirrorHref(`${BASE}/a/index/index.html`, BASE),
    `${BASE}/a/index.html`,
  );
});

test("missing mirror base never leaks the original URL", () => {
  assert.equal(toMirrorHref("https://original.example.com/a/b/", ""), "#");
});