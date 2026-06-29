'use client';

import { useEffect, useRef, useState } from 'react';
import { ActivityEvent, wsUrl } from './api';

// Poll any async fetcher on an interval (default 2.5s, matching the legacy SPA).
export function usePoll<T>(fetcher: () => Promise<T>, interval = 2500): T | null {
  const [data, setData] = useState<T | null>(null);
  const fnRef = useRef(fetcher);
  fnRef.current = fetcher;
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const d = await fnRef.current();
        if (alive) setData(d);
      } catch {
        /* transient — keep last good */
      }
    };
    tick();
    const id = setInterval(tick, interval);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [interval]);
  return data;
}

// Live reasoning stream over the /ws/stream WebSocket, newest first, auto-reconnect.
export function useReasoningStream(max = 120): ActivityEvent[] {
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    const connect = () => {
      try {
        ws = new WebSocket(wsUrl());
      } catch {
        return;
      }
      ws.onmessage = (ev) => {
        try {
          const e = JSON.parse(ev.data) as ActivityEvent;
          setEvents((prev) => [e, ...prev].slice(0, max));
        } catch {
          /* ignore malformed frame */
        }
      };
      ws.onclose = () => {
        if (!closed) setTimeout(connect, 2000);
      };
    };
    connect();
    return () => {
      closed = true;
      ws?.close();
    };
  }, [max]);
  return events;
}
