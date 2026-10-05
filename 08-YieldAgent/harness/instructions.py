from pathlib import Path


def load_instructions():
    directory = Path(__file__).with_name("instructions")
    return (directory / "analysis.md").read_text()
