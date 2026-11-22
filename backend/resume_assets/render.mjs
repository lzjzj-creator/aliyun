// Browser and server share this dependency-free renderer. Template definitions
// are the field whitelist; editor state is deliberately not part of the API.
export const RENDER_VERSION = "3.1.0";

const SECTION_ORDER = {
  classic: ["basics", "education", "internships", "projects", "skills", "awards", "summary"],
  modern: ["basics", "internships", "projects", "education", "skills", "awards", "summary"],
};
const SECTION_TYPES = new Set(["kv", "entries", "skillgroups", "lines", "text"]);
const PLACEHOLDER = /[【\[［]\s*待补充|待补充\s*[:：]|^\s*待补充\s*$/u;
const own = (object, key) => object && typeof object === "object" && Object.hasOwn(object, key) ? object[key] : undefined;
const array = (value) => Array.isArray(value) ? value : [];
const text = (value) => typeof value === "string" ? value
  : typeof value === "number" && Number.isFinite(value) ? String(value)
  : typeof value === "boolean" ? String(value) : "";
const escape = (value) => text(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);

// Parse only marker headers, never raster data. A bounded caller may deliberately
// refuse JPEGs whose SOF occurs beyond its inspection budget. Full JPEG decoding
// remains the server's responsibility (Pillow), not this dependency-free module.
export function jpegDimensions(bytes) {
  if (!(bytes instanceof Uint8Array) || bytes[0] !== 0xff || bytes[1] !== 0xd8) return null;
  let offset = 2;
  while (offset < bytes.length) {
    if (bytes[offset++] !== 0xff) return null;
    while (bytes[offset] === 0xff) offset++;
    const marker = bytes[offset++];
    if (marker == null || marker === 0x00 || marker === 0xd8 || marker === 0xd9 || marker === 0xda) return null;
    if (marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) continue;
    if (offset + 2 > bytes.length) return null;
    const length = bytes[offset] * 256 + bytes[offset + 1];
    if (length < 2 || offset + length > bytes.length) return null;
    if (marker >= 0xc0 && marker <= 0xcf && ![0xc4, 0xc8, 0xcc].includes(marker)) {
      const components = bytes[offset + 7];
      if (length < 11 || !components || components > 4 || length !== 8 + components * 3) return null;
      const height = bytes[offset + 3] * 256 + bytes[offset + 4];
      const width = bytes[offset + 5] * 256 + bytes[offset + 6];
      return width && height ? { width, height } : null;
    }
    offset += length;
  }
  return null;
}

export function isValidResumePhoto(value) {
  const prefix = "data:image/jpeg;base64,";
  // Check the bound before regex/decoding; no whitespace, URL variants or aliases.
  if (typeof value !== "string" || value.length > prefix.length + 40000 || !value.startsWith(prefix)) return false;
  const encoded = value.slice(prefix.length);
  if (!encoded.length || encoded.length % 4 || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(encoded)) return false;
  try {
    const binary = atob(encoded);
    if (binary.length > 30000 || btoa(binary) !== encoded) return false; // canonical pad bits
    const size = jpegDimensions(Uint8Array.from(binary, (char) => char.charCodeAt(0)));
    return !!size && size.width <= 240 && size.height <= 320;
  } catch {
    return false;
  }
}

function leaf(value, path, label) {
  const raw = text(value);
  // Whitespace determines emptiness only; never rewrite the user's actual text.
  return raw.trim() ? [{ text: raw, path, label }] : [];
}

function listLeaves(value, path, label) {
  // Preserve legacy scalar text rather than dropping it or splitting characters.
  return Array.isArray(value)
    ? value.flatMap((item, index) => leaf(item, `${path}.${index}`, label))
    : leaf(value, path, label);
}

function readField(definition, data, prefix) {
  const { key, input, required } = definition;
  const path = `${prefix}.${key}`;
  const label = text(definition.label) || key;
  const value = own(data, key);
  const field = { key, input, required, path, label, leaves: [], rows: [] };
  if (input === "skillgroups") {
    // The skillgroups input contract permits only name and items in each group.
    field.rows = array(value).map((group, index) => ({
      name: leaf(own(group, "name"), `${path}.${index}.name`, `${label}名称`),
      items: listLeaves(own(group, "items"), `${path}.${index}.items`, label),
    }));
    field.leaves = field.rows.flatMap((row) => [...row.name, ...row.items]);
  } else if (input === "lines") {
    field.rows = array(value).map((line, index) => readFields(
      array(definition.line_fields).filter((key) => typeof key === "string").map((key) => ({ key, label, input: "text" })),
      line, `${path}.${index}`,
    ));
    field.leaves = field.rows.flatMap((row) => row.flatMap((part) => part.leaves));
  } else {
    field.leaves = ["tags", "bullets"].includes(input)
      ? listLeaves(value, path, label) : leaf(value, path, label);
  }
  return field;
}

function readFields(definitions, data, prefix) {
  return array(definitions)
    .filter((definition) => definition && typeof definition.key === "string")
    .map((definition) => readField(definition, data, prefix));
}

function model(template, fields, layout = "classic") {
  const order = SECTION_ORDER[layout];
  return array(own(template, "sections"))
    .filter((section) => section && typeof section.key === "string" && section.key !== "_meta" && SECTION_TYPES.has(section.type))
    .slice()
    .sort((a, b) => {
      const rank = (section) => order.includes(section.key) ? order.indexOf(section.key) : order.length;
      return rank(a) - rank(b) || (Number(a.order) || 0) - (Number(b.order) || 0);
    })
    .map((section) => {
      const data = own(fields, section.key);
      const definitions = section.type === "kv" ? section.fields : section.entry_fields;
      return {
        key: section.key, title: text(section.title), type: section.type,
        entries: section.type === "entries"
          ? array(data).map((entry, index) => readFields(definitions, entry, `${section.key}.${index}`)) : [],
        fields: section.type === "entries" ? [] : readFields(definitions, data, section.key),
      };
    });
}

const hasContent = (fields) => fields.some((field) => field.leaves.length);
const inline = (leaves, separator = "、") => leaves.map((item) => escape(item.text)).join(separator);
const joined = (fields, separator = " · ") => fields.filter((field) => field.leaves.length).map((field) => inline(field.leaves)).join(separator);
const paragraph = (className, content) => content ? `<p class="${className}">${content}</p>` : "";

function renderHeader(fields, photo) {
  if (!hasContent(fields) && !photo) return "";
  const name = joined(fields.filter((field) => field.key === "name"));
  const role = joined(fields.filter((field) => field.key === "target_role"));
  const contacts = fields.filter((field) => !["name", "target_role"].includes(field.key) && field.leaves.length);
  const content = (name ? `<h1 class="resume-name">${name}</h1>` : "")
    + paragraph("resume-role", role)
    + (contacts.length ? `<p class="resume-contact">${contacts.map((field) => `<span>${inline(field.leaves)}</span>`).join('<span class="resume-separator" aria-hidden="true"> · </span>')}</p>` : "");
  // Keep no-photo HTML identical to the existing renderer.
  return photo
    ? `<header class="resume-header resume-header-with-photo"><div class="resume-header-text">${content}</div><img class="resume-photo" src="${escape(photo)}" alt="简历照片" /></header>`
    : `<header class="resume-header">${content}</header>`;
}

function renderEntry(fields) {
  if (!hasContent(fields)) return "";
  const plain = fields.filter((field) => !["tags", "bullets"].includes(field.input) && field.key !== "period");
  const title = joined(plain.filter((field) => field.required));
  const period = joined(fields.filter((field) => field.key === "period"));
  const heading = title || period
    ? `<div class="resume-entry-head">${title ? `<b class="resume-entry-title">${title}</b>` : ""}${period ? `<span class="resume-period">${period}</span>` : ""}</div>` : "";
  return `<div class="resume-entry">${heading}`
    + paragraph("resume-detail", joined(plain.filter((field) => !field.required)))
    + fields.filter((field) => ["tags", "bullets"].includes(field.input)).map(renderField).join("")
    + "</div>";
}

function renderField(field) {
  if (!field.leaves.length) return "";
  if (field.input === "bullets") {
    return `<ul class="resume-bullets">${field.leaves.map((item) => `<li>${escape(item.text)}</li>`).join("")}</ul>`;
  }
  if (field.input === "skillgroups") {
    return field.rows.map((row) => {
      const name = inline(row.name), items = inline(row.items);
      return paragraph("resume-skill-line", `${name ? `<b>${name}${items ? "：" : ""}</b>` : ""}${items}`);
    }).join("");
  }
  if (field.input === "lines") {
    return `<ul class="resume-awards">${field.rows.filter(hasContent).map((row) => {
      const content = joined(row.filter((part) => part.key !== "period"));
      const period = joined(row.filter((part) => part.key === "period"));
      return `<li><div class="resume-entry-head">${content ? `<span class="resume-entry-title">${content}</span>` : ""}${period ? `<span class="resume-period">${period}</span>` : ""}</div></li>`;
    }).join("")}</ul>`;
  }
  return paragraph(field.input === "tags" ? "resume-detail" : "resume-text", inline(field.leaves));
}

/** Safe body-only HTML. Incomplete user text is preserved; export must be gated separately. */
export function renderResume(template, fields, layout = "classic") {
  const selectedLayout = layout === "modern" ? "modern" : "classic";
  const candidate = own(own(fields, "_meta"), "photo");
  const photo = isValidResumePhoto(candidate) ? candidate : "";
  const content = model(template, fields, selectedLayout).map((section) => {
    if (section.key === "basics") return renderHeader(section.fields, photo);
    const body = section.type === "entries"
      ? section.entries.map(renderEntry).join("") : section.fields.map(renderField).join("");
    if (!body) return "";
    return `<section class="resume-section" data-key="${escape(section.key)}"><h2 class="resume-section-title">${escape(section.title)}</h2>${body}</section>`;
  }).join("");
  return `<article class="resume-paper" data-layout="${selectedLayout}" data-render-version="${RENDER_VERSION}">${content}</article>`;
}

/** Export eligibility is about actual facts, not interview quotas or manual gaps. */
export function exportIssues(template, fields) {
  const sections = model(template, fields);
  const issues = [];
  const add = (path, label, reason) => issues.push({ field_path: path, label, reason });
  const meta = own(fields, "_meta");
  if (meta && Object.hasOwn(meta, "photo") && !isValidResumePhoto(meta.photo)) {
    add("_meta.photo", "照片", "照片无效，请重新上传或移除照片后导出（JPEG，最大 240×320 像素、30 KB）。");
  }
  const basics = sections.find((section) => section.key === "basics")?.fields || [];
  for (const key of ["name", "target_role"]) {
    const field = basics.find((field) => field.key === key);
    if (field && !field.leaves.length) add(field.path, field.label, `请填写${field.label}`);
  }
  const contacts = basics.filter((field) => ["phone", "email"].includes(field.key));
  if (contacts.length && !hasContent(contacts)) {
    add(contacts.find((field) => field.key === "phone")?.path || contacts[0].path, "电话或邮箱", "请至少填写电话或邮箱");
  }
  for (const section of sections) {
    for (const entry of section.entries.filter(hasContent)) {
      for (const field of entry) {
        if (field.required && !field.leaves.length) add(field.path, field.label, `请补全已填写经历的${field.label}`);
      }
    }
    for (const field of [...section.fields, ...section.entries.flat()]) {
      for (const item of field.leaves) {
        if (PLACEHOLDER.test(item.text)) add(item.path, item.label, "包含待补充标记，请补齐真实信息、删除该条或恢复有事实依据的原文；不能仅删除标记");
      }
    }
  }
  return issues;
}
