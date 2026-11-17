"""B 域：简历接口 v2（template / autofill / optimize）。

文件分片：本域路由单独文件，main.py 只 include（§12.1）。
契约唯一来源 = resume_template.json；版本不匹配一律 409 让前端重取。
"""
from fastapi import APIRouter, HTTPException

from resume_template import load, check_version, TemplateError

router = APIRouter(prefix="/api/v2/resume", tags=["resume-v2"])


def _tpl():
    try:
        return load()
    except TemplateError as e:
        raise HTTPException(status_code=500, detail=str(e))


def _require_version(tpl, version: str):
    if not check_version(tpl, version):
        raise HTTPException(
            status_code=409,
            detail={"code": "TEMPLATE_VERSION_MISMATCH",
                    "server_version": tpl.template_version,
                    "message": "模板版本不匹配，请重新拉取 /api/v2/resume/template"})


@router.get("/template")
def get_template():
    """契约唯一出口：前后端读同一份，禁止双份硬编码。"""
    return _tpl().model_dump()
