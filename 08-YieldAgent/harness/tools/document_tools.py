"""Read owned stored documents in bounded pages; never fetch caller URLs or paths."""
import json
from bs4 import BeautifulSoup
from pydantic import Field
from ..types import Contract
from ..runtime.domain_process import run_blocking
from .registry import ToolResult, ToolSpec


class ArtifactTextInput(Contract):
    artifact_id: str = Field(min_length=1, max_length=200)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=4000, ge=1, le=16000)


class HistoryDocumentInput(Contract):
    doc_id: str = Field(min_length=1, max_length=500)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=4000, ge=1, le=16000)


def _page(text, offset, limit):
    stop = min(len(text), offset + limit)
    return {'text': text[offset:stop], 'total_length': len(text), 'offset': offset,
            'next_offset': stop if stop < len(text) else None}


async def read_artifact_text(args, ctx):
    ref, content = await ctx.store.artifact(ctx.run['principal_id'], ctx.run['session_id'], args.artifact_id)
    mime = ref['mime'].split(';', 1)[0]
    scope = {'artifact_id': args.artifact_id, 'mime': mime, 'source': 'owned_stored_artifact'}
    if not (mime.startswith('text/') or mime in {'application/json', 'application/xml'}):
        return ToolResult(rows=[{'artifact_id': args.artifact_id, 'mime': mime,
            'readable': False, 'download_ref': ref}], scope=scope,
            source_result_ids=[ref['result_id']], summary='바이너리 문서: 저장 산출물 다운로드 참조를 사용하세요.')
    text = content.decode('utf-8', errors='replace')
    if mime == 'text/html':
        soup = BeautifulSoup(text, 'html.parser')
        for element in soup(['script', 'style', 'noscript']):
            element.decompose()
        text = soup.get_text(separator='\n', strip=True)
    page = _page(text, args.offset, args.limit)
    complete = args.offset == 0 and page['next_offset'] is None
    scope.update(complete=complete, missing_reason=None if complete else 'Only a page of the source document was read')
    return ToolResult(rows=[dict(artifact_id=args.artifact_id, **page)],
        scope=scope, source_result_ids=[ref['result_id']], status=None if complete else 'partial',
        summary='저장 원문 텍스트 페이지')


async def get_history_document(args, ctx):
    from fail_history_tools import _fetch_results_by_doc_ids
    documents = await run_blocking(_fetch_results_by_doc_ids, [args.doc_id], full_content=True, strict=True)
    if not documents:
        raise ValueError('History document ID not found')
    document = documents[0]
    text = document.get('content') or json.dumps(document, ensure_ascii=False)
    metadata = {k: v for k, v in document.items() if k != 'content'}
    page = _page(text, args.offset, args.limit)
    complete = args.offset == 0 and page['next_offset'] is None
    return ToolResult(rows=[dict(doc_id=args.doc_id, metadata=metadata, **page)],
        scope={'doc_id': args.doc_id, 'source': 'opensearch_original_document', 'complete': complete,
            'missing_reason': None if complete else 'Only a page of the source document was read'},
        status=None if complete else 'partial', summary='불량 이력 원문 페이지 (검색 snippet 아님)')


def register(registry):
    registry.add(ToolSpec('read_artifact_text', '현재 사용자·세션에 저장된 artifact_id의 HTML/텍스트 원문을 페이지로 읽는다. 임의 URL/파일 경로를 열지 않는다. next_offset으로 후속 페이지를 읽는다.', ArtifactTextInput, read_artifact_text))
    registry.add(ToolSpec('get_history_document', '검색 결과 doc_id의 불량 이력 전체 원문을 페이지로 읽는다. 검색의 200자 snippet과 별개이며 next_offset으로 이어 읽는다.', HistoryDocumentInput, get_history_document))
