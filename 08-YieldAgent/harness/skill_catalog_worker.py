"""Read one profile with the bundled Hermes parser in its own import namespace."""
import json
import os
from pathlib import Path
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().with_name('engine')))
from agent import skill_utils
from hermes_cli.config import atomic_config_write
from hermes_cli.skills_config import get_disabled_skills
from hermes_yaml import safe_load


def read_cards(root):
    entries = []
    for directory, dirs, files, dir_fd in os.fwalk(root, follow_symlinks=False):
        dirs[:] = [d for d in dirs if d not in skill_utils.EXCLUDED_SKILL_DIRS
                   and not ('SKILL.md' in files and d in skill_utils.SKILL_SUPPORT_DIRS)]
        if 'SKILL.md' not in files:
            continue
        path = Path(directory) / 'SKILL.md'
        try:
            fd = os.open('SKILL.md', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
            with os.fdopen(fd, encoding='utf-8-sig') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    continue
                front, body = skill_utils.parse_frontmatter(stream.read())
        except (OSError, UnicodeError):
            continue
        name = front.get('name') or path.parent.name
        if not isinstance(name, str):
            continue
        metadata = front.get('metadata')
        ui = metadata.get('yield_ui') if isinstance(metadata, dict) else None
        ui = ui if isinstance(ui, dict) else {}
        heading = next((line[2:].strip() for line in body.splitlines() if line.startswith('# ')), name)
        parts = path.parent.relative_to(root).parts
        entries.append(dict(name=name, tier=skill_utils.TIER_LOCAL, root=root, path=path,
                            visible=(skill_utils.skill_matches_platform(front)
                                     and skill_utils.skill_matches_environment(front)
                                     and skill_utils.skill_matches_apps(front)),
                            title=ui.get('title') or heading, artwork=ui.get('artwork') or 'generic',
                            description=ui.get('description') or front.get('description') or '',
                            category=ui.get('category') or (parts[0] if len(parts) > 1 else '일반'),
                            content=body.strip()))
    return [row for row in skill_utils.resolve_skill_catalog(entries) if row['load_name'] and row['visible']]


def run():
    profile = Path.cwd()
    config_path = profile / 'config.yaml'
    if config_path.is_symlink() or (config_path.exists() and (
            not config_path.is_file() or config_path.stat().st_nlink != 1)):
        raise ValueError('Unsupported profile config path')
    config = (safe_load(config_path.read_text()) or {}) if config_path.exists() else {}
    disabled = get_disabled_skills(config)
    cards = read_cards(profile / 'skills')
    if len(sys.argv) > 1:
        name, enabled = sys.argv[1], json.loads(sys.argv[2])
        card = next((row for row in cards if row['load_name'] == name), None)
        if not card:
            return {'error': 404}
        if not enabled and card['name'] in skill_utils.ESSENTIAL_SKILLS:
            return {'error': 409}
        if enabled and name != card['name'] and card['name'] in disabled:
            return {'error': 409}
        if enabled:
            disabled.discard(name)
        else:
            disabled.add(name)
        if config.get('skills') is None:
            config['skills'] = {}
        config['skills']['disabled'] = sorted(disabled)
        atomic_config_write(config_path, config)
        return {'name': name, 'enabled': enabled}
    fields = ('title', 'description', 'category', 'artwork', 'content')
    skills = [dict(name=card['load_name'], enabled=not skill_utils.is_disabled_entry(card, disabled),
                   **{key: str(card[key]) for key in fields}) for card in cards]
    return {'skills': sorted(skills, key=lambda card: (card['category'], card['name']))}


if __name__ == '__main__':
    print(json.dumps(run(), ensure_ascii=False))
