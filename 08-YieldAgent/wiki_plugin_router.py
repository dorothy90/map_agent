import os

from fastapi import APIRouter, Depends

from wiki_plugin_auth import require_plugin_token

router = APIRouter(dependencies=[Depends(require_plugin_token)])


def plugin_dependency_status() -> dict[str, str]:
    try:
        from fail_history_tools import _get_opensearch_client

        opensearch = "ok" if _get_opensearch_client().ping() else "unavailable"
    except Exception:
        opensearch = "unavailable"
    llm = "configured" if os.getenv("OPENROUTER_API_KEY", "") else "unconfigured"
    return {"backend": "ok", "opensearch": opensearch, "llm": llm}


@router.get("/health")
def plugin_health() -> dict:
    return {"status": "ok", "dependencies": plugin_dependency_status()}
