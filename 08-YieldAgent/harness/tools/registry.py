from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal
from pydantic import BaseModel
from langchain_core.utils.function_calling import convert_to_openai_tool
from ..types import ResultTable


@dataclass
class ToolResult:
    rows: list[dict] = field(default_factory=list)
    summary: str = ""
    artifacts: list[dict] = field(default_factory=list)
    scope: dict = field(default_factory=dict)
    source_result_ids: list[str] = field(default_factory=list)
    status: str | None = None
    data_origin: str = "live"
    tables: list[ResultTable] = field(default_factory=list)

    def __post_init__(self):
        if self.rows and self.tables:
            raise ValueError("rows and tables cannot both be supplied")
        self.tables = [ResultTable.model_validate(table) for table in self.tables]
        ids = [table.table_id for table in self.tables]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate table_id")


@dataclass
class ToolSpec:
    name: str
    description: str
    schema: type[BaseModel]
    handler: Callable
    read_only: bool = True
    timeout: int = 60
    effect: Literal['read', 'artifact', 'persistent_write'] | None = None
    availability: Callable | None = None
    eager: bool = False

    def __post_init__(self):
        if self.effect is None:
            self.effect = 'read' if self.read_only else 'persistent_write'

    def available(self):
        return self.availability() if self.availability else {'available': True, 'reason': ''}


class ToolRegistry:
    def __init__(self):
        self.tools: dict[str, ToolSpec] = {}

    def add(self, spec: ToolSpec):
        if spec.name in self.tools:
            raise ValueError("Duplicate tool")
        self.tools[spec.name] = spec

    def model_tools(self, principal_id=None):
        tools = []
        for spec in self.tools.values():
            tool = convert_to_openai_tool(spec.schema)
            tool["function"].update(name=spec.name, description=spec.description)
            tools.append(tool)
        return tools

    async def invoke(self, name, arguments, context):
        spec = self.tools[name]
        available = spec.available()
        if not available['available']:
            raise ValueError(available['reason'])
        args = spec.schema.model_validate(arguments)
        return await spec.handler(args, context)


def domain_registry():
    from .yield_tools import register as yields
    from .wads_tools import register as wads
    from .lot_tools import register as lots
    from .python_tools import register as python
    from .analysis_tools import register as analysis
    from .artifact_tools import register as artifacts
    from .map_tools import register as maps
    from .document_tools import register as documents
    from .defect_tools import register as defects
    registry = ToolRegistry()
    for register in (yields, wads, lots, python, analysis, artifacts, maps, documents, defects):
        register(registry)
    return registry
