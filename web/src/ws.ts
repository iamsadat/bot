// WebSocket auto-reconnect helper.

export type WsMessage = { kind: string; payload: any };

export function connectWs(onMessage: (m: WsMessage) => void): () => void {
  let ws: WebSocket | null = null;
  let closed = false;
  let timer: number | null = null;

  const open = () => {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${window.location.host}/ws`);
    ws.onmessage = (ev) => {
      try {
        onMessage(JSON.parse(ev.data));
      } catch {
        /* ignore malformed */
      }
    };
    ws.onclose = () => {
      if (closed) return;
      timer = window.setTimeout(open, 2000);
    };
    ws.onerror = () => ws?.close();
  };

  open();

  return () => {
    closed = true;
    if (timer) window.clearTimeout(timer);
    ws?.close();
  };
}
