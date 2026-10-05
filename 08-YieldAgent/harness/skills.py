"""Versioned, on-demand domain references; never semantic routing rules."""
import hashlib
from pathlib import Path

import frontmatter
from pydantic import Field

from .types import Contract


class SkillRead(Contract):
    name: str = Field(min_length=1)
    resource: str | None = None


class SkillList(Contract):
    pass


class SkillCatalog:
    def __init__(self, root=None, *, snapshot=None):
        self.snapshot = snapshot if snapshot is not None else {}
        if snapshot is not None:
            return
        root = Path(root or Path(__file__).with_name('skills')).resolve()
        for source in sorted(root.glob('*/SKILL.md')):
            if not source.resolve().is_relative_to(root):
                continue
            parsed = frontmatter.loads(source.read_text())
            name = str(parsed.get('name', source.parent.name))
            if name != source.parent.name or name in self.snapshot:
                raise ValueError('Skill name must match its unique directory')
            resources = {}
            for path in sorted(source.parent.rglob('*.md')):
                if path.resolve().is_relative_to(source.parent.resolve()):
                    content = path.read_text()
                    resources[str(path.relative_to(source.parent))] = {'content': content,
                        'hash': hashlib.sha256(content.encode()).hexdigest()}
            self.snapshot[name] = {'name': name, 'description': str(parsed.get('description', '')),
                'version': resources['SKILL.md']['hash'], 'resources': resources}

    def list(self):
        return [{k: item[k] for k in ('name', 'description', 'version')} for item in self.snapshot.values()]

    def read(self, name, resource=None):
        path = Path(resource or 'SKILL.md')
        if path.is_absolute() or '..' in path.parts:
            raise ValueError('Resource must belong to this skill')
        item = self.snapshot.get(name)
        if not item:
            raise ValueError('Unknown skill. Available: ' + ', '.join(self.snapshot))
        if str(path) not in item['resources']:
            raise ValueError('Unknown reference resource. Available: ' + ', '.join(item['resources']))
        return {'name': name, 'description': item['description'], 'version': item['version'],
            'resource': str(path), **item['resources'][str(path)]}
