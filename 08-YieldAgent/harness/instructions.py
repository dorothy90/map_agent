from pathlib import Path


def load_instructions():
    directory = Path(__file__).with_name("instructions")
    return (directory / "analysis.md").read_text()


def load_native_instructions() -> str:
    directory = Path(__file__).with_name("instructions")
    return "\n\n".join(
        (directory / name).read_text(encoding="utf-8").strip()
        for name in ("native.md", "domain-notes.md")
    )
