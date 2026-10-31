// Extracted verbatim class from H/apps/shared/src/json-rpc-gateway-legacy-replay.test.ts:8-44; export added for reuse.
export class FakeWebSocket extends EventTarget {
  static instances: FakeWebSocket[] = []
  readyState = 0
  sent: string[] = []
  url: string

  constructor(url: string) {
    super()
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  send(data: string): void {
    this.sent.push(data)
  }

  close(): void {
    this.readyState = 3
    this.dispatchEvent(new CloseEvent('close'))
  }

  open(): void {
    this.readyState = 1
    this.dispatchEvent(new Event('open'))
  }

  serverFrame(obj: unknown): void {
    this.dispatchEvent(new MessageEvent('message', { data: JSON.stringify(obj) }))
  }

  request(method: string): { id: string; method: string; params: Record<string, unknown> } | undefined {
    return this.sent.map(frame => JSON.parse(frame)).find(frame => frame.method === method)
  }
}
