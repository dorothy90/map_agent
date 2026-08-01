import { createServer, type IncomingMessage, type ServerResponse } from "node:http";

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  YieldWikiApi,
  nodeSseStream,
  type RestTransport,
} from "../src/api";
import type { ChatRequest, PluginSettings, SseEvent } from "../src/types";

const DUMMY_TOKEN = "test-token-not-a-secret";
const chatBody: ChatRequest = {
  query: "원인은?",
  session_id: "session-1",
};

const openServers: Array<ReturnType<typeof createServer>> = [];

afterEach(async () => {
  await Promise.all(
    openServers.splice(0).map(
      (server) =>
        new Promise<void>((resolve) => server.close(() => resolve())),
    ),
  );
});

async function makeLocalServer(
  handler: (request: IncomingMessage, response: ServerResponse) => void,
): Promise<PluginSettings> {
  const server = createServer(handler);
  openServers.push(server);
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") {
    throw new Error("Local test server did not expose a TCP address");
  }
  return {
    serverUrl: `http://127.0.0.1:${address.port}`,
    apiToken: DUMMY_TOKEN,
  };
}

const unusedStream = vi.fn();

describe("YieldWikiApi REST", () => {
  it("adds bearer auth without exposing the token in the URL", async () => {
    const request = vi.fn().mockResolvedValue({
      status: 200,
      json: { status: "ok" },
    });
    const api = new YieldWikiApi(
      { serverUrl: "http://localhost:8001/", apiToken: DUMMY_TOKEN },
      request,
      unusedStream,
    );

    await api.health();

    expect(request).toHaveBeenCalledWith(
      expect.objectContaining({
        url: "http://localhost:8001/api/wiki/plugin/health",
        headers: { Authorization: `Bearer ${DUMMY_TOKEN}` },
        throw: false,
      }),
    );
    expect(request.mock.calls[0][0].url).not.toContain(DUMMY_TOKEN);
  });

  it("serializes JSON request bodies", async () => {
    const request = vi.fn().mockResolvedValue({ status: 200, json: { id: "review-1" } });
    const api = new YieldWikiApi(
      { serverUrl: "http://localhost:8001", apiToken: DUMMY_TOKEN },
      request,
      unusedStream,
    );

    await api.rest("/reviews", { method: "POST", body: { comment: "확인" } });

    expect(request).toHaveBeenCalledWith(
      expect.objectContaining({
        body: JSON.stringify({ comment: "확인" }),
        headers: {
          Authorization: `Bearer ${DUMMY_TOKEN}`,
          "Content-Type": "application/json",
        },
      }),
    );
  });

  it.each([
    [401, "unauthorized"],
    [404, "not_found"],
    [409, "conflict"],
    [502, "bad_gateway"],
  ] as const)("maps HTTP %i to a typed API error", async (status, code) => {
    const request = vi.fn().mockResolvedValue({
      status,
      json: { detail: "request failed" },
    });
    const api = new YieldWikiApi(
      { serverUrl: "http://localhost:8001", apiToken: DUMMY_TOKEN },
      request,
      unusedStream,
    );

    const error = await api.rest("/health").catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status, code, message: "request failed" });
  });
});

describe("Node SSE transport", () => {
  it("delivers each SSE event before stream completion and forwards bearer auth", async () => {
    let authorization: string | undefined;
    let requestPath: string | undefined;
    const settings = await makeLocalServer((request, response) => {
      authorization = request.headers.authorization;
      requestPath = request.url;
      response.writeHead(200, { "Content-Type": "text/event-stream" });
      response.write('data: {"type":"token","content":"첫"}\n\n');
      setTimeout(() => {
        response.end('data: {"type":"token","content":"번째"}\n\n');
      }, 25);
    });
    const api = new YieldWikiApi(settings, vi.fn(), nodeSseStream);
    const received: string[] = [];
    let completed = false;
    let notifyFirstEvent: (() => void) | undefined;
    const firstEvent = new Promise<void>((resolve) => {
      notifyFirstEvent = resolve;
    });

    const streaming = api
      .streamChat(chatBody, (event) => {
        received.push(event.type);
        if (received.length === 1) notifyFirstEvent?.();
      })
      .then(() => {
        completed = true;
      });
    await firstEvent;

    expect(completed).toBe(false);
    await streaming;
    expect(received).toEqual(["token", "token"]);
    expect(authorization).toBe(`Bearer ${DUMMY_TOKEN}`);
    expect(requestPath).toBe("/api/wiki/plugin/chat");
    expect(requestPath).not.toContain(DUMMY_TOKEN);
  });

  it("parses arbitrary chunks, CRLF, multi-line data, and a final unterminated frame", async () => {
    const settings = await makeLocalServer((_request, response) => {
      response.writeHead(200, { "Content-Type": "text/event-stream" });
      const bytes = Buffer.from(
        'data: {"type":"token",\r\ndata: "content":"한글"}\r\n\r\n' +
          'data: {"type":"stream_end"}',
      );
      const cuts = [1, 7, 19, 38, 42, 44, bytes.length - 2, bytes.length];
      let start = 0;
      cuts.forEach((end, index) => {
        setTimeout(() => {
          response.write(bytes.subarray(start, end));
          start = end;
          if (end === bytes.length) response.end();
        }, index * 3);
      });
    });
    const events: SseEvent[] = [];

    await nodeSseStream(settings, chatBody, (event) => events.push(event));

    expect(events).toEqual([
      { type: "token", content: "한글" },
      { type: "stream_end" },
    ]);
  });

  it("rejects non-success responses with ApiError", async () => {
    let requests = 0;
    const settings = await makeLocalServer((_request, response) => {
      requests += 1;
      response.writeHead(409, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ detail: "conflict" }));
    });

    const error = await nodeSseStream(settings, chatBody, vi.fn()).catch(
      (caught: unknown) => caught,
    );

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 409, code: "conflict" });
    expect(requests).toBe(1);
  });

  it("rejects malformed JSON instead of dropping the frame", async () => {
    const settings = await makeLocalServer((_request, response) => {
      response.writeHead(200, { "Content-Type": "text/event-stream" });
      response.end("data: not-json\n\n");
    });

    await expect(nodeSseStream(settings, chatBody, vi.fn())).rejects.toBeInstanceOf(
      SyntaxError,
    );
  });

  it("destroys an in-flight request when aborted", async () => {
    let requestClosed = false;
    let requestStarted: (() => void) | undefined;
    const started = new Promise<void>((resolve) => {
      requestStarted = resolve;
    });
    const settings = await makeLocalServer((request, response) => {
      request.on("close", () => {
        requestClosed = true;
      });
      response.writeHead(200, { "Content-Type": "text/event-stream" });
      response.write('data: {"type":"token","content":"첫"}\n\n');
      requestStarted?.();
    });
    const controller = new AbortController();
    const streaming = nodeSseStream(settings, chatBody, vi.fn(), controller.signal);

    await started;
    controller.abort();

    await expect(streaming).rejects.toMatchObject({ name: "AbortError" });
    await vi.waitFor(() => expect(requestClosed).toBe(true));
  });
});
