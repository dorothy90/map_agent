
import pytest


def test_catalog_loads_only_metadata_and_pins_content(tmp_path):
    from harness.skills import SkillCatalog
    directory = tmp_path / 'sample'
    directory.mkdir()
    source = directory / 'SKILL.md'
    source.write_text('---\nname: sample\ndescription: Sample task\n---\nPrivate body')
    catalog = SkillCatalog(tmp_path)
    assert 'Private body' not in str(catalog.list())
    original = catalog.read('sample')
    source.write_text('changed')
    assert catalog.read('sample') == original
    assert original['content'].endswith('Private body')


def test_skill_resource_cannot_escape_its_directory(tmp_path):
    from harness.skills import SkillCatalog
    directory = tmp_path / 'sample'
    directory.mkdir()
    (directory / 'SKILL.md').write_text('---\nname: sample\ndescription: Sample\n---\nbody')
    (tmp_path / 'secret.md').write_text('secret')
    catalog = SkillCatalog(tmp_path)
    with pytest.raises(ValueError):
        catalog.read('sample', '../secret.md')


def test_loaded_skill_body_occurs_once_in_context():
    from langchain_core.messages import ToolMessage
    from harness.context import build_context
    state = {'goal': {}, 'loaded_skills': {'sample': {'name': 'sample', 'hash': 'h', 'content': 'UNIQUE BODY'}},
             'messages': [ToolMessage(content='{"name":"sample","hash":"h","content":"UNIQUE BODY"}', name='read_skill', tool_call_id='call')]}
    messages = build_context(state, 'base')
    assert str([m.content for m in messages]).count('UNIQUE BODY') == 1
    assert any('UNIQUE BODY' in m.content for m in messages if m.type == 'system')
    assert all('UNIQUE BODY' not in m.content for m in messages if m.type == 'tool')


def test_declared_auto_load_is_pinned_and_other_skills_remain_lazy(tmp_path):
    from harness.skills import SkillCatalog
    for name, auto in [('base', 'true'), ('specialist', 'false')]:
        directory = tmp_path / name
        directory.mkdir()
        (directory / 'SKILL.md').write_text(
            f'---\nname: {name}\ndescription: Test\nauto_load: {auto}\n---\n{name} body')
    catalog = SkillCatalog(tmp_path)
    pinned = SkillCatalog(snapshot=catalog.snapshot)
    assert list(pinned.auto_loaded()) == ['base/SKILL.md']
    assert pinned.auto_loaded()['base/SKILL.md'] == pinned.read('base')
    assert 'specialist body' not in str(pinned.auto_loaded())
