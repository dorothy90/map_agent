export interface BaseReplEvent {
  type: string;
  run_id: string;
  thread_id: string;
  sequence: number;
}

export type PlotArtifact = {
  artifact_id: string;
  kind: "plotly";
  mime_type: "application/vnd.plotly.v1+json";
  spec: Record<string, unknown>;
};

export type ReplEvent =
  | (BaseReplEvent & { type: "RUN_STARTED" })
  | (BaseReplEvent & { type: "TEXT_MESSAGE_START"; message_id: string })
  | (BaseReplEvent & {
      type: "TEXT_MESSAGE_CONTENT";
      message_id: string;
      content: string;
    })
  | (BaseReplEvent & { type: "TEXT_MESSAGE_END"; message_id: string })
  | (BaseReplEvent & {
      type: "TOOL_CALL_START";
      tool_call_id: string;
      name: string;
    })
  | (BaseReplEvent & {
      type: "TOOL_CALL_ARGS";
      tool_call_id: string;
      args: Record<string, unknown>;
    })
  | (BaseReplEvent & { type: "TOOL_CALL_END"; tool_call_id: string })
  | (BaseReplEvent & {
      type: "TOOL_RESULT";
      tool_call_id: string;
      result: Record<string, unknown>;
    })
  | (BaseReplEvent & {
      type: "ARTIFACT";
      tool_call_id: string;
      artifact: PlotArtifact;
    })
  | (BaseReplEvent & { type: "RUN_FINISHED" })
  | (BaseReplEvent & { type: "RUN_ERROR"; code: string; message: string })
  | (BaseReplEvent & {
      type: "RUN_CANCELLED";
      code: "execution_cancelled";
      message: string;
    });

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const requireString = (value: Record<string, unknown>, key: string): void => {
  if (typeof value[key] !== "string") throw new Error(`Invalid REPL event field: ${key}`);
};

const requireRecord = (
  value: Record<string, unknown>,
  key: string,
): Record<string, unknown> => {
  if (!isRecord(value[key])) throw new Error(`Invalid REPL event field: ${key}`);
  return value[key];
};

export function parseReplEvent(value: unknown): ReplEvent {
  if (!isRecord(value)) throw new Error("REPL event must be an object");
  requireString(value, "type");
  requireString(value, "run_id");
  requireString(value, "thread_id");
  if (!Number.isInteger(value.sequence) || (value.sequence as number) < 1) {
    throw new Error("Invalid REPL event field: sequence");
  }

  switch (value.type) {
    case "RUN_STARTED":
    case "RUN_FINISHED":
      break;
    case "TEXT_MESSAGE_START":
    case "TEXT_MESSAGE_END":
      requireString(value, "message_id");
      break;
    case "TEXT_MESSAGE_CONTENT":
      requireString(value, "message_id");
      requireString(value, "content");
      break;
    case "TOOL_CALL_START":
      requireString(value, "tool_call_id");
      requireString(value, "name");
      break;
    case "TOOL_CALL_ARGS":
      requireString(value, "tool_call_id");
      requireRecord(value, "args");
      break;
    case "TOOL_CALL_END":
      requireString(value, "tool_call_id");
      break;
    case "TOOL_RESULT":
      requireString(value, "tool_call_id");
      requireRecord(value, "result");
      break;
    case "ARTIFACT": {
      requireString(value, "tool_call_id");
      const artifact = requireRecord(value, "artifact");
      requireString(artifact, "artifact_id");
      if (artifact.kind !== "plotly") throw new Error("Invalid REPL artifact kind");
      if (artifact.mime_type !== "application/vnd.plotly.v1+json") {
        throw new Error("Invalid REPL artifact MIME type");
      }
      requireRecord(artifact, "spec");
      break;
    }
    case "RUN_ERROR":
      requireString(value, "code");
      requireString(value, "message");
      break;
    case "RUN_CANCELLED":
      if (value.code !== "execution_cancelled") {
        throw new Error("Invalid RUN_CANCELLED code");
      }
      requireString(value, "message");
      break;
    default:
      throw new Error(`Unsupported REPL event type: ${String(value.type)}`);
  }

  return value as unknown as ReplEvent;
}

function parseSseFrame(frame: string): ReplEvent | null {
  const data = frame
    .split(/\r?\n/)
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).replace(/^ /, ""))
    .join("\n");
  if (!data) return null;
  return parseReplEvent(JSON.parse(data) as unknown);
}

export async function* parseSseChunks(
  chunks: Iterable<string> | AsyncIterable<string>,
): AsyncGenerator<ReplEvent> {
  let buffer = "";
  for await (const chunk of chunks) {
    buffer += chunk;
    let boundary = /\r?\n\r?\n/.exec(buffer);
    while (boundary) {
      const frame = buffer.slice(0, boundary.index);
      buffer = buffer.slice(boundary.index + boundary[0].length);
      const event = parseSseFrame(frame);
      if (event) yield event;
      boundary = /\r?\n\r?\n/.exec(buffer);
    }
  }
  if (buffer) {
    const event = parseSseFrame(buffer);
    if (event) yield event;
  }
}

export async function* parseSseStream(
  body: ReadableStream<Uint8Array> | null,
): AsyncGenerator<ReplEvent> {
  if (!body) throw new Error("SSE response has no body");
  const reader = body.getReader();
  const decoder = new TextDecoder();

  async function* decodedChunks(): AsyncGenerator<string> {
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        const decoded = decoder.decode(value, { stream: true });
        if (decoded) yield decoded;
      }
      const final = decoder.decode();
      if (final) yield final;
    } finally {
      reader.releaseLock();
    }
  }

  yield* parseSseChunks(decodedChunks());
}
