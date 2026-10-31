"""Static RPC contracts from Hermes 5894e750, without profile-home discovery.

Data: original agent/i18n.py:33-70, i18n_languages.py:23-41 and locales/*.tui.yaml.
User/plugin overlays are deferred by the existing manifest, not read as empty
host inventories. Config/model/tool/profile/paste dependencies remain explicit.
"""
from functools import lru_cache
import json
from pathlib import Path

from .execution import RpcError
from .session_rpc import NotConnected


STATIC_METHODS = frozenset({'config.get', 'model.options', 'profiles.list',
    'tools.list', 'tools.show', 'toolsets.list', 'i18n.catalog', 'i18n.languages', 'paste.collapse'})


@lru_cache(maxsize=1)
def _data():
    # Fixed packaged resource; never a client/host profile path or environment override.
    return json.loads(Path(__file__).with_name('static_i18n.json').read_text(encoding='utf-8'))


def static_rpc(method, params):
    # H/tui_gateway/methods_i18n.py:22-44. No persistence/engine imports.
    if method not in {'i18n.languages', 'i18n.catalog'}:
        # Required path (profiles/paste), real registry (tools), actual allowed
        # provider/config values are absent from policy/capabilities. PB:631,635.
        raise NotConnected()
    surface = str(params.get('surface') or 'core')
    if method == 'i18n.catalog' and surface not in ('core', 'tui', 'desktop'):
        raise RpcError('surface must be one of core, tui, desktop', code=4002)
    data = _data()
    info = data['BUNDLED_LANGUAGE_INFO']
    if method == 'i18n.languages':
        return {'languages': [{'id': lang, 'endonym': info[lang][0],
            'rtl': info[lang][1], 'source': 'bundled'}
            for lang in ['en', *sorted(set(info) - {'en'})]]}
    # H/agent/i18n.py:109-122; i18n_layers.py:43-45, exact locale normalization.
    raw = params.get('lang', '')
    key = raw.strip().lower().replace('_', '-') if isinstance(raw, str) else ''
    alias = data['_LANGUAGE_ALIASES'].get(key)
    base = key.split('-', 1)[0]
    lang = key if key in info else alias if alias in info else base if base in info else 'en'
    # H/agent/i18n.py:164-175: core excludes bundled strings; no desktop
    # YAML exists in 5894e750. TUI translations do travel over this RPC.
    return {'lang': lang, 'surface': surface,
            'messages': dict(data['tui'].get(lang, {})) if surface == 'tui' else {}}
