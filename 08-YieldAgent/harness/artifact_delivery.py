"""Canonical model-facing links for owned stored artifacts."""
from urllib.parse import quote, urlencode


ARTIFACT_DELIVERY = 'answer_refs'


def artifact_url(session_id: str, artifact_id: str) -> str:
    return f"/harness/artifacts/{quote(artifact_id, safe='')}?{urlencode({'session_id': session_id})}"


def artifact_links(session_id: str, result_id: str, refs: list[dict]) -> list[dict]:
    links = []
    for ref in refs:
        href = artifact_url(session_id, ref['artifact_id'])
        mime = ref.get('mime', 'application/octet-stream')
        title = str(ref.get('title') or 'Artifact')
        label = ''.join('\\' + char if char in '\\`*_{}[]<>!&' else char
                        for char in title.replace('\r', ' ').replace('\n', ' '))
        image = mime.startswith('image/') and mime != 'image/svg+xml'
        links.append({**ref, 'result_id': ref.get('result_id') or result_id,
            'title': title, 'mime': mime, 'href': href,
            'markdown': f"{'!' if image else ''}[{label}]({href})"})
    return links
