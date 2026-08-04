from __future__ import annotations

import os
import subprocess
from pathlib import Path


def test_wrapper_delegates_fixed_live_enrichment_command(tmp_path):
    project_dir = Path(__file__).resolve().parents[2]
    script = project_dir / "enrich_extra_index.sh"
    capture = tmp_path / "args.txt"
    fake_uv = tmp_path / "uv"
    fake_uv.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$@\" > \"$CAPTURE_PATH\"\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    env = os.environ.copy()
    env["CAPTURE_PATH"] = str(capture)
    env["PATH"] = f"{tmp_path}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        ["bash", str(script)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert capture.read_text(encoding="utf-8").splitlines() == [
        "run",
        "--frozen",
        "python",
        str(project_dir / "enrich_wiki.py"),
        "--apply",
        "--allow-external-llm",
        "--vault",
        "/Users/daehwankim/SYLDAIX/YieldWiki",
        "--source-index",
        "syld_gpt_2067627",
    ]
