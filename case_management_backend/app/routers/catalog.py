"""Reference data shared by the rules module and case management."""

from fastapi import APIRouter, HTTPException

from app import catalog

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


@router.get("/meta")
def get_meta() -> dict:
    """Assurances, their modules, rule categories and the case vocabularies."""
    return catalog.meta()


@router.get("/assurances")
def list_assurances() -> list[dict]:
    return [
        {"code": a.code, "name": a.name, "group": a.group, "modules": a.modules}
        for a in catalog.ASSURANCES
    ]


@router.get("/assurances/{code}")
def get_assurance(code: str) -> dict:
    """Resolve one assurance by code or name — used when authoring a rule."""
    assurance = catalog.resolve_assurance(code)
    if assurance is None:
        raise HTTPException(404, f"Unknown assurance '{code}'")
    return {
        "code": assurance.code,
        "name": assurance.name,
        "group": assurance.group,
        "modules": assurance.modules,
        "ruleCategories": catalog.RULE_CATEGORIES,
    }
