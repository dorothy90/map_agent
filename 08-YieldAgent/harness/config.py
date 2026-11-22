from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Literal
from urllib.parse import urlparse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class IsolationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ssh_host: str
    ssh_user: str
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_key: Path
    known_hosts: Path
    home: Path
    work_root: Path
    analysis_venv: Path
    launcher: Path
    sandbox: Literal["auto", "required"] = "auto"
    retain_python_files: bool = False

    @model_validator(mode="after")
    def validate_boundary(self):
        if self.ssh_host not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("isolation SSH host must be localhost")
        if self.ssh_user != "yieldrun":
            raise ValueError("isolation SSH user must be yieldrun")
        for name in ("ssh_key", "known_hosts"):
            path = getattr(self, name).expanduser()
            if not path.is_absolute() or not path.is_file():
                raise ValueError(f"isolation {name.replace('_', ' ')} must name an existing absolute file")
            setattr(self, name, path.resolve())
        for name in ("home", "work_root", "analysis_venv", "launcher"):
            path = getattr(self, name).expanduser()
            if not path.is_absolute():
                raise ValueError(f"isolation {name.replace('_', ' ')} must be absolute")
            if path.is_symlink():
                raise ValueError(f"isolation {name.replace('_', ' ')} must not be a symlink")
            try:
                resolved = path.resolve(strict=True)
            except OSError as exc:
                raise ValueError(f"isolation {name.replace('_', ' ')} must exist") from exc
            if resolved == REPOSITORY_ROOT or REPOSITORY_ROOT in resolved.parents:
                raise ValueError(f"isolation {name.replace('_', ' ')} must be outside the repository")
            setattr(self, name, resolved)
        if not self.home.is_dir() or not self.work_root.is_dir() or not self.analysis_venv.is_dir():
            raise ValueError("isolation home, work root, and analysis venv must be directories")
        if not self.launcher.is_file():
            raise ValueError("isolation launcher must be a file")
        if self.work_root.parent != self.home:
            raise ValueError("isolation work root must be directly inside the dedicated home")
        if self.analysis_venv == self.home or self.home in self.analysis_venv.parents:
            raise ValueError("analysis venv must be administrator-owned outside the dedicated home")
        return self


class _LocalBoundarySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    backend: Literal["local", "seatbelt"]
    home: Path
    work_root: Path
    analysis_venv: Path
    launcher: Path
    sandbox: Literal["required"] = "required"
    retain_python_files: bool = False

    @model_validator(mode="after")
    def validate_boundary(self):
        linux_local = self.backend == "local"
        if linux_local and sys.platform != "linux":
            raise ValueError("local bubblewrap isolation requires Linux")
        if not linux_local and sys.platform != "darwin":
            raise ValueError("Seatbelt isolation is supported only on macOS")
        for name in ("home", "work_root", "analysis_venv", "launcher"):
            path = getattr(self, name).expanduser()
            if not path.is_absolute():
                raise ValueError(f"isolation {name.replace('_', ' ')} must be absolute")
            if path.is_symlink():
                raise ValueError(f"isolation {name.replace('_', ' ')} must not be a symlink")
            try:
                resolved = path.resolve(strict=True)
            except OSError as exc:
                raise ValueError(f"isolation {name.replace('_', ' ')} must exist") from exc
            if (not linux_local or name in ("home", "work_root")) and resolved.is_relative_to(REPOSITORY_ROOT):
                raise ValueError(f"isolation {name.replace('_', ' ')} must be outside the repository")
            info = resolved.stat()
            owners = {os.geteuid(), 0} if linux_local and name in ("launcher", "analysis_venv") else {os.geteuid()}
            if info.st_uid not in owners or info.st_mode & 0o022:
                raise ValueError(f"isolation {name.replace('_', ' ')} is writable by another user")
            setattr(self, name, resolved)
        if not self.home.is_dir() or not self.work_root.is_dir() or not self.analysis_venv.is_dir():
            raise ValueError("isolation home, work root, and analysis venv must be directories")
        if not self.launcher.is_file():
            raise ValueError("isolation launcher must be a file")
        if self.work_root.parent != self.home:
            raise ValueError("isolation work root must be directly inside the dedicated home")
        if self.analysis_venv == self.home or self.home in self.analysis_venv.parents:
            raise ValueError("analysis venv must be outside the dedicated sandbox home")
        return self


class SeatbeltIsolationSettings(_LocalBoundarySettings):
    backend: Literal["seatbelt"] = "seatbelt"


class LocalIsolationSettings(_LocalBoundarySettings):
    backend: Literal["local"] = "local"
    launcher: Path = Path(__file__).resolve().parent / "isolation/launcher.py"

    @model_validator(mode="after")
    def validate_linux(self):
        if self.launcher != Path(__file__).resolve().parent / "isolation/launcher.py":
            raise ValueError("local isolation must use the bundled launcher")
        if (not (self.analysis_venv / "pyvenv.cfg").is_file()
                or REPOSITORY_ROOT.is_relative_to(self.analysis_venv)
                or self.home.is_relative_to(self.analysis_venv)):
            raise ValueError("local analysis requires a Python virtual environment separate from app data")
        for name in ("home", "work_root"):
            if getattr(self, name).stat().st_mode & 0o077:
                raise ValueError(f"isolation {name.replace('_', ' ')} must be private (0700)")
        return self

    @classmethod
    def from_env(cls):
        if sys.platform != "linux":
            raise ValueError("local bubblewrap isolation requires Linux")
        home = Path(os.environ.get("HARNESS_ISOLATION_HOME", Path.home() / ".local/share/yield-harness"))
        work = Path(os.environ.get("HARNESS_ISOLATION_WORK_ROOT", home / "work"))
        for path in (home, work):
            if not path.is_absolute() or path != path.resolve() or path.is_relative_to(REPOSITORY_ROOT):
                raise ValueError("local isolation workspace must be an absolute canonical path outside the repository")
        if work.parent != home:
            raise ValueError("isolation work root must be directly inside the dedicated home")
        home.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = home.stat()
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("isolation home must be private (0700) and owned by the current user")
        work.mkdir(mode=0o700, exist_ok=True)
        sandbox = os.getenv("HARNESS_ISOLATION_SANDBOX", "required")
        return cls(
            home=home, work_root=work, analysis_venv=os.getenv("HARNESS_ISOLATION_ANALYSIS_VENV", sys.prefix),
            sandbox="required" if sandbox == "auto" else sandbox,
            retain_python_files=os.getenv("HARNESS_RETAIN_PYTHON_FILES") == "1",
        )


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    engine: Literal["hermes"] = "hermes"
    model: str = "z-ai/glm-5.3-flash"
    base_url: str = "https://openrouter.ai/api/v1"
    api_key: SecretStr = SecretStr("")
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "yield_agent"
    tool_limit: int = Field(default=24, ge=1)
    parallel_tool_limit: int = Field(default=8, ge=1, le=8)
    publish_commentary: bool = False
    native_prompts: bool = False
    model_limit: int = Field(default=40, ge=3)
    token_limit: int = Field(default=120_000, ge=4096)
    context_tokens: int = Field(default=24_000, ge=4096)
    active_seconds: int = Field(default=300, ge=5)
    call_timeout: int = Field(default=90, ge=1)
    max_output_tokens: int = Field(default=8192, ge=128)
    answer_output_tokens: int = Field(default=4096, ge=128)
    review_output_tokens: int = Field(default=2048, ge=128)
    evidence_tokens: int = Field(default=2000, ge=0)
    temperature: float = Field(default=0, ge=0, le=2)
    reasoning_effort: str | None = None
    timezone: str | None = None
    python_runtime: Literal["process", "docker"] = "process"
    python_executable: str | None = None
    python_image: str = "yield-harness-python:2"
    no_yield_tools: bool = False
    native_allowed_tools: list[str] | None = None
    isolation: LocalIsolationSettings | SeatbeltIsolationSettings | IsolationSettings | None = None
    principal: str = "local"
    enabled: bool = False

    @model_validator(mode="after")
    def require_isolation(self):
        if self.no_yield_tools and self.isolation is None:
            raise ValueError("native execution requires isolation configuration")
        return self

    @classmethod
    def from_env(cls):
        engine = os.getenv("HARNESS_ENGINE", "hermes")
        if engine != "hermes":
            raise ValueError("HARNESS_ENGINE must be hermes; other execution engines have been retired")
        base_url = os.getenv("HARNESS_BASE_URL", cls.model_fields["base_url"].default)
        host = urlparse(base_url).hostname
        key = os.getenv("HARNESS_API_KEY", "")
        if not key and host == "api.groq.com":
            key = os.getenv("GROQ_API_KEY") or os.getenv("groq_api_key", "")
        elif not key and host == "openrouter.ai":
            key = os.getenv("OPENROUTER_API_KEY", "")
        default_backend = "seatbelt" if sys.platform == "darwin" else "local"
        backend = os.getenv("HARNESS_ISOLATION_BACKEND", default_backend)
        if backend == "local":
            isolation = LocalIsolationSettings.from_env()
        elif backend == "seatbelt":
            names = {
                "home": "HARNESS_ISOLATION_HOME",
                "work_root": "HARNESS_ISOLATION_WORK_ROOT",
                "analysis_venv": "HARNESS_ISOLATION_ANALYSIS_VENV",
                "launcher": "HARNESS_ISOLATION_LAUNCHER",
                "sandbox": "HARNESS_ISOLATION_SANDBOX",
            }
            missing = [env_name for field, env_name in names.items()
                       if field != "sandbox" and not os.getenv(env_name)]
            if missing:
                raise ValueError("missing isolation configuration: " + ", ".join(missing))
            isolation = SeatbeltIsolationSettings(
                home=os.environ[names["home"]], work_root=os.environ[names["work_root"]],
                analysis_venv=os.environ[names["analysis_venv"]],
                launcher=os.environ[names["launcher"]],
                sandbox=os.getenv(names["sandbox"], "required"),
                retain_python_files=os.getenv("HARNESS_RETAIN_PYTHON_FILES") == "1",
            )
        elif backend == "ssh":
            names = {
                "ssh_host": "HARNESS_ISOLATION_SSH_HOST",
                "ssh_user": "HARNESS_ISOLATION_SSH_USER",
                "ssh_port": "HARNESS_ISOLATION_SSH_PORT",
                "ssh_key": "HARNESS_ISOLATION_SSH_KEY",
                "known_hosts": "HARNESS_ISOLATION_KNOWN_HOSTS",
                "home": "HARNESS_ISOLATION_HOME",
                "work_root": "HARNESS_ISOLATION_WORK_ROOT",
                "analysis_venv": "HARNESS_ISOLATION_ANALYSIS_VENV",
                "launcher": "HARNESS_ISOLATION_LAUNCHER",
                "sandbox": "HARNESS_ISOLATION_SANDBOX",
            }
            missing = [env_name for field, env_name in names.items()
                       if field not in {"ssh_port", "sandbox"} and not os.getenv(env_name)]
            if missing:
                raise ValueError("missing isolation configuration: " + ", ".join(missing))
            isolation = IsolationSettings(
                ssh_host=os.environ[names["ssh_host"]], ssh_user=os.environ[names["ssh_user"]],
                ssh_port=int(os.getenv(names["ssh_port"], "22")),
                ssh_key=os.environ[names["ssh_key"]], known_hosts=os.environ[names["known_hosts"]],
                home=os.environ[names["home"]], work_root=os.environ[names["work_root"]],
                analysis_venv=os.environ[names["analysis_venv"]], launcher=os.environ[names["launcher"]],
                sandbox=os.getenv(names["sandbox"], "auto"),
                retain_python_files=os.getenv("HARNESS_RETAIN_PYTHON_FILES") == "1")
        else:
            raise ValueError("HARNESS_ISOLATION_BACKEND must be local, seatbelt or ssh")
        return cls(
            engine=engine,
            model=os.getenv("HARNESS_MODEL", cls.model_fields["model"].default),
            base_url=base_url,
            api_key=key,
            mongo_uri=os.getenv("HARNESS_MONGO_URI", "mongodb://localhost:27017"),
            mongo_db=os.getenv("HARNESS_MONGO_DB", "yield_agent"),
            principal=os.getenv("HARNESS_PRINCIPAL", "local"),
            enabled=os.getenv("HARNESS_ENABLED", "false").lower() == "true",
            python_runtime=os.getenv("HARNESS_PYTHON_RUNTIME", "process"),
            python_executable=os.getenv("HARNESS_PYTHON_EXECUTABLE") or None,
            python_image=os.getenv("HARNESS_PYTHON_IMAGE", "yield-harness-python:2"),
            no_yield_tools=True,
            isolation=isolation,
            tool_limit=int(os.getenv("HARNESS_TOOL_LIMIT", "24")),
            parallel_tool_limit=int(os.getenv("HARNESS_PARALLEL_TOOLS", "8")),
            publish_commentary=os.getenv("HARNESS_PUBLISH_COMMENTARY", "false").lower() == "true",
            native_prompts=os.getenv("HARNESS_NATIVE_PROMPTS", "false").lower() == "true",
            model_limit=int(os.getenv("HARNESS_MODEL_LIMIT", "40")),
            token_limit=int(os.getenv("HARNESS_TOKEN_LIMIT", "120000")),
            context_tokens=int(os.getenv("HARNESS_CONTEXT_TOKENS", "24000")),
            active_seconds=int(os.getenv("HARNESS_ACTIVE_SECONDS", "300")),
            call_timeout=int(os.getenv("HARNESS_CALL_TIMEOUT", "90")),
            max_output_tokens=int(os.getenv("HARNESS_MAX_OUTPUT_TOKENS", "8192")),
            answer_output_tokens=int(os.getenv("HARNESS_ANSWER_OUTPUT_TOKENS", "4096")),
            review_output_tokens=int(os.getenv("HARNESS_REVIEW_OUTPUT_TOKENS", "2048")),
            evidence_tokens=int(os.getenv("HARNESS_EVIDENCE_TOKENS", "2000")),
            temperature=float(os.getenv("HARNESS_TEMPERATURE", "0")),
            reasoning_effort=os.getenv("HARNESS_REASONING_EFFORT") or None,
            timezone=os.getenv("HARNESS_TIMEZONE") or os.getenv("HERMES_TIMEZONE") or None,
        )
