from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from repl_agent.runtime.base import PlotArtifact


class BaseReplEvent(BaseModel):
    type: str
    run_id: str
    thread_id: str
    sequence: int = Field(ge=1)


class RunStarted(BaseReplEvent):
    type: Literal["RUN_STARTED"] = "RUN_STARTED"


class TextMessageStart(BaseReplEvent):
    type: Literal["TEXT_MESSAGE_START"] = "TEXT_MESSAGE_START"
    message_id: str


class TextMessageContent(BaseReplEvent):
    type: Literal["TEXT_MESSAGE_CONTENT"] = "TEXT_MESSAGE_CONTENT"
    message_id: str
    content: str


class TextMessageEnd(BaseReplEvent):
    type: Literal["TEXT_MESSAGE_END"] = "TEXT_MESSAGE_END"
    message_id: str


class ToolCallStart(BaseReplEvent):
    type: Literal["TOOL_CALL_START"] = "TOOL_CALL_START"
    tool_call_id: str
    name: str


class ToolCallArgs(BaseReplEvent):
    type: Literal["TOOL_CALL_ARGS"] = "TOOL_CALL_ARGS"
    tool_call_id: str
    args: dict[str, Any]


class ToolCallEnd(BaseReplEvent):
    type: Literal["TOOL_CALL_END"] = "TOOL_CALL_END"
    tool_call_id: str


class ToolResult(BaseReplEvent):
    type: Literal["TOOL_RESULT"] = "TOOL_RESULT"
    tool_call_id: str
    result: dict[str, Any]


class ArtifactEvent(BaseReplEvent):
    type: Literal["ARTIFACT"] = "ARTIFACT"
    tool_call_id: str
    artifact: PlotArtifact


class RunFinished(BaseReplEvent):
    type: Literal["RUN_FINISHED"] = "RUN_FINISHED"


class RunError(BaseReplEvent):
    type: Literal["RUN_ERROR"] = "RUN_ERROR"
    code: str
    message: str


class RunCancelled(BaseReplEvent):
    type: Literal["RUN_CANCELLED"] = "RUN_CANCELLED"
    code: Literal["execution_cancelled"] = "execution_cancelled"
    message: str = "실행이 취소되었습니다. 새 세션을 시작해주세요."


ReplEvent = Annotated[
    RunStarted
    | TextMessageStart
    | TextMessageContent
    | TextMessageEnd
    | ToolCallStart
    | ToolCallArgs
    | ToolCallEnd
    | ToolResult
    | ArtifactEvent
    | RunFinished
    | RunError
    | RunCancelled,
    Field(discriminator="type"),
]


class EventEmitter:
    def __init__(self, run_id: str, thread_id: str):
        self.run_id = run_id
        self.thread_id = thread_id
        self._sequence = 0

    def build(self, event_cls, **payload):
        self._sequence += 1
        return event_cls(
            run_id=self.run_id,
            thread_id=self.thread_id,
            sequence=self._sequence,
            **payload,
        )
