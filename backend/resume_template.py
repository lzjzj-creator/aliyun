"""简历模板契约：backend/resume_template.json 是全链路唯一来源（§8）。

- load()：加载 + Pydantic 结构校验 + 一致性检查 + lru_cache
- slot 推导：derive_slots / required_slots / adaptive_min_entries（orchestrator 与 coverage 复用）
- 字段路径：iter_field_paths / validate_fields / empty_fields（autofill/optimize 共用）
"""
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ValidationError

TEMPLATE_PATH = Path(__file__).with_name("resume_template.json")


class TemplateError(Exception):
    pass


class FieldSpec(BaseModel):
    key: str
    label: str = ""
    input: str = "text"          # text|url|tags|bullets|textarea|skillgroups|lines
    required: bool = False
    rules: Optional[str] = None
    rubric: Optional[str] = None
    min_items: Optional[int] = None
    max_items: Optional[int] = None
    min_groups: Optional[int] = None
    max_groups: Optional[int] = None
    max_chars: Optional[int] = None
    group_name_examples: Optional[list[str]] = None
    line_fields: Optional[list[str]] = None


class SectionSpec(BaseModel):
    key: str
    title: str
    type: str                    # kv|entries|skillgroups|lines|text
    order: int
    required: bool = True
    min: Optional[int] = None
    max: Optional[int] = None
    priority: Optional[int] = None
    rules: Optional[str] = None
    fields: list[FieldSpec] = []        # kv 型用 fields
    entry_fields: list[FieldSpec] = []  # entries/skillgroups/lines/text 型用 entry_fields
    adaptive_min: Optional[dict] = None

    def spec_fields(self) -> list[FieldSpec]:
        return self.fields if self.type == "kv" else self.entry_fields


class SlotSpec(BaseModel):
    id: str
    label: str
    required: bool = True
    required_when: str = "always"      # always | exp:<experience_type>
    kind: str = "field"                # field | entry_group
    maps_to: Optional[str] = None
    min_entries: Optional[int] = None
    equivalents: Optional[list[str]] = None
    entry_slots: Optional[list[str]] = None
    ask: Optional[str] = None


class TemplateSpec(BaseModel):
    template_version: str
    name: str
    locale: str = "zh-CN"
    page_rule: str = ""
    render: dict = {}
    sections: list[SectionSpec]
    interview_slots: list[SlotSpec] = []
    optimize: dict = {}


def _consistency_check(tpl: TemplateSpec):
    keys = [s.key for s in tpl.sections]
    if len(keys) != len(set(keys)):
        raise TemplateError("section key 重复: " + ",".join(keys))
    valid = {s.key for s in tpl.sections}
    for slot in tpl.interview_slots:
        if slot.maps_to:
            root = slot.maps_to.split(".")[0]
            if root not in valid and not root.startswith(("profile", "identity", "basics", "education")):
                raise TemplateError(f"slot {slot.id} maps_to 指向未知 section: {slot.maps_to}")


@lru_cache(maxsize=1)
def load() -> TemplateSpec:
    """加载契约（进程内缓存）。文件损坏时抛 TemplateError。"""
    try:
        raw = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        raise TemplateError(f"模板契约读取失败: {e}") from e
    try:
        tpl = TemplateSpec.model_validate(raw)
    except ValidationError as e:
        raise TemplateError(f"模板契约结构校验失败: {e}") from e
    _consistency_check(tpl)
    return tpl


def get_section(tpl: TemplateSpec, key: str) -> Optional[SectionSpec]:
    return next((s for s in tpl.sections if s.key == key), None)


def check_version(tpl: TemplateSpec, version: str) -> bool:
    """请求携带版本与服务端不一致 → 调用方应返回 409。"""
    return bool(version) and version == tpl.template_version


# ---------- 访谈 slot 推导（#11/#12 复用） ----------

def derive_slots(tpl: TemplateSpec) -> list[SlotSpec]:
    """访谈 slot 集 = 契约 interview_slots，顺序即提纲顺序。"""
    return tpl.interview_slots


def slot_required(slot: SlotSpec, profile: dict) -> bool:
    """required_when 求值：always 恒真；exp:<type> = 画像中已存在该类经历。"""
    if not slot.required:
        return False
    when = slot.required_when or "always"
    if when == "always":
        return True
    if when.startswith("exp:"):
        etype = when[4:]
        return any(e.get("type") == etype for e in (profile or {}).get("experiences", []))
    return True


def required_slots(tpl: TemplateSpec, profile: dict) -> list[SlotSpec]:
    """自适应后的必填 slot：无实习不强制实习（§6 coverage 口径）。"""
    return [s for s in tpl.interview_slots if slot_required(s, profile)]


def adaptive_min_entries(tpl: TemplateSpec, sec_key: str, profile: dict) -> int:
    """section.min 经 adaptive_min 调整：无实习时项目要求数上浮。"""
    sec = get_section(tpl, sec_key)
    if not sec:
        return 0
    base = sec.min or 0
    rule = sec.adaptive_min or {}
    etype = rule.get("when_no_experience_type")
    if etype and not any(e.get("type") == etype for e in (profile or {}).get("experiences", [])):
        return max(base, int(rule.get("then", base)))
    return base


# ---------- 字段路径（evidence_map / gaps / change_log 共用约定） ----------
# 形状：basics.name | education.0.school | projects.0.bullets.1 | skills.groups.0.items.2 | awards.items.0.text | summary.text
_PATH_RE = re.compile(r"^[a-z_]+(\.\d+)*(\.[a-z_]+(\.\d+)*)*$")


def valid_path_syntax(path: str) -> bool:
    return bool(_PATH_RE.match(path or ""))


def iter_field_paths(tpl: TemplateSpec, fields: dict):
    """枚举 fields 数据里存在的全部字段路径（用于证据/gap/改动定位）。"""
    for sec in tpl.sections:
        data = (fields or {}).get(sec.key)
        if data is None:
            continue
        if sec.type == "kv":
            for f in sec.fields:
                yield f"{sec.key}.{f.key}"
        elif sec.type == "entries":
            for i, entry in enumerate(data or []):
                for f in sec.entry_fields:
                    v = (entry or {}).get(f.key)
                    if f.input == "bullets":
                        for j in range(len(v or [])):
                            yield f"{sec.key}.{i}.{f.key}.{j}"
                    else:
                        yield f"{sec.key}.{i}.{f.key}"
        elif sec.type == "skillgroups":
            for gi, g in enumerate((data or {}).get("groups", [])):
                for ii in range(len((g or {}).get("items", []))):
                    yield f"{sec.key}.groups.{gi}.items.{ii}"
        elif sec.type == "lines":
            for i in range(len((data or {}).get("items", []))):
                yield f"{sec.key}.items.{i}.text"
        elif sec.type == "text":
            yield f"{sec.key}.text"


def empty_fields(tpl: TemplateSpec) -> dict:
    """按契约生成空 fields 骨架（autofill/前端表单共用形状）。"""
    out = {}
    for sec in tpl.sections:
        if sec.type == "kv":
            out[sec.key] = {f.key: "" for f in sec.fields}
        elif sec.type == "entries":
            out[sec.key] = []
        elif sec.type == "skillgroups":
            out[sec.key] = {"groups": []}
        elif sec.type == "lines":
            out[sec.key] = {"items": []}
        elif sec.type == "text":
            out[sec.key] = {"text": ""}
    return out


def validate_fields(tpl: TemplateSpec, fields: dict) -> list[str]:
    """轻量形状校验：未知 section / 类型不符返回错误列表（不写死字段全集，允许空）。"""
    errs = []
    known = {s.key for s in tpl.sections}
    for key, val in (fields or {}).items():
        sec = get_section(tpl, key)
        if not sec:
            errs.append(f"未知 section: {key}")
            continue
        if sec.type == "entries" and not isinstance(val, list):
            errs.append(f"{key} 应为数组")
        elif sec.type == "kv" and not isinstance(val, dict):
            errs.append(f"{key} 应为对象")
        elif sec.type in ("skillgroups", "lines", "text") and not isinstance(val, dict):
            errs.append(f"{key} 应为对象")
    return errs
