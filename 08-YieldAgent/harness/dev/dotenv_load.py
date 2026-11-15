"""Load an existing checkout .env without overriding process environment."""
from pathlib import Path


def existing_dotenv_path(project, *, cwd=None, home=None):
    """Return the first existing .env for this project, or None.

    A worktree may not have its own .env. After walking the project and cwd
    ancestors, also look at a sibling ``yield-agent`` checkout.
    """
    project = Path(project)
    starts = [project]
    starts.append(Path(cwd) if cwd is not None else Path.cwd())
    try:
        resolved = project.resolve()
    except OSError:
        resolved = project
    if resolved.name == '08-YieldAgent':
        starts.append(resolved.parent.parent / 'yield-agent' / '08-YieldAgent')
        starts.append(resolved.parent.parent / 'yield-agent')
    if home is not None:
        starts.append(Path(home) / 'yield-agent' / '08-YieldAgent')
        starts.append(Path(home) / 'yield-agent')
    seen = set()
    for start in starts:
        try:
            here = Path(start).resolve()
        except OSError:
            continue
        for parent in (here, *here.parents):
            path = parent / '.env'
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            if path.is_file():
                return path
    return None


def load_existing_dotenv(project, *, cwd=None, home=None, override=False):
    """Load the discovered .env. Never logs path or values."""
    from dotenv import load_dotenv
    path = existing_dotenv_path(project, cwd=cwd, home=home)
    if path is not None:
        load_dotenv(path, override=override)
    return path
