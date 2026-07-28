import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

vi.mock("./Chat", () => ({ Chat: () => <div>분석 화면</div> }));

const sessionResponse = {
  session_id: "session-1",
  rowcount: 1,
  columns: ["fail_value"],
  numeric_columns: ["fail_value"],
  query: {
    lotcd: "L12345",
    start: "2026-02-16",
    end: "2026-04-16",
    fail_name: "OPEN",
  },
};

const response = (status: number, body?: object) => ({
  ok: status >= 200 && status < 300,
  status,
  statusText: "status text",
  json: async () => body,
  text: async () => "close failed",
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function startSession(fetchMock: ReturnType<typeof vi.fn>) {
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "세션 시작" }));
  expect(await screen.findByLabelText("현재 데이터")).toHaveTextContent("L12345 · OPEN");
}

describe("App workspace", () => {
  it("keeps data selection inside the chat workspace before a session starts", () => {
    render(<App />);

    const workspace = screen.getByRole("main", { name: "Yield 분석 워크스페이스" });
    expect(workspace).toContainElement(screen.getByRole("heading", { name: "데이터 선택" }));
    expect(workspace).toContainElement(screen.getByRole("button", { name: "세션 시작" }));
    expect(screen.getByText("Yield Agent")).toBeInTheDocument();
  });

  it("keeps the same workspace and shows compact data context after a session starts", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(response(200, sessionResponse));
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(<App />);
    const workspace = screen.getByRole("main", { name: "Yield 분석 워크스페이스" });

    fireEvent.click(screen.getByRole("button", { name: "세션 시작" }));

    expect(await screen.findByLabelText("현재 데이터")).toHaveTextContent("L12345 · OPEN");
    expect(screen.getByText("분석 화면")).toBeInTheDocument();
    expect(screen.getByRole("main", { name: "Yield 분석 워크스페이스" })).toBe(workspace);
    expect(container.querySelector(".workspace-header")).not.toHaveTextContent("session-1");
    expect(container.querySelector(".workspace-header")).not.toHaveTextContent("fail_value");
  });

  it("closes the server session before returning to the session form", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(200, sessionResponse))
      .mockResolvedValueOnce(response(200));
    await startSession(fetchMock);

    fireEvent.click(screen.getByRole("button", { name: "데이터 변경" }));

    await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith(
      "/repl/session/session-1",
      { method: "DELETE" },
    ));
    expect(await screen.findByRole("heading", { name: "데이터 선택" })).toBeInTheDocument();
  });

  it("also clears local state when the server session is already gone", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(200, sessionResponse))
      .mockResolvedValueOnce(response(404));
    await startSession(fetchMock);

    fireEvent.click(screen.getByRole("button", { name: "데이터 변경" }));

    await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith(
      "/repl/session/session-1",
      { method: "DELETE" },
    ));
    expect(await screen.findByRole("heading", { name: "데이터 선택" })).toBeInTheDocument();
  });

  it("retains the session and explains a close failure", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(200, sessionResponse))
      .mockResolvedValueOnce(response(500));
    await startSession(fetchMock);

    fireEvent.click(screen.getByRole("button", { name: "데이터 변경" }));

    expect(await screen.findByText(/세션을 종료하지 못했습니다/)).toBeInTheDocument();
    expect(screen.getByText("분석 화면")).toBeInTheDocument();
  });

  it("makes a best-effort DELETE request when the page unloads", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(response(200, sessionResponse));
    await startSession(fetchMock);

    fireEvent(window, new Event("pagehide"));

    expect(fetchMock).toHaveBeenLastCalledWith(
      "/repl/session/session-1",
      { method: "DELETE", keepalive: true },
    );
  });
});
