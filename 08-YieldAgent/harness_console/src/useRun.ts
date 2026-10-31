import { useEffect, useRef, useState } from 'react';
import { active, api, mergeEvents, readEvents } from './core';
import type { Run, StoredEvent } from './types';
export function useRun(initial: Run) {
  const [run,setRun] = useState(initial);
  const [events,setEvents] = useState<StoredEvent[]>([]);
  const [error,setError] = useState('');
  const [revision,setRevision] = useState(0);
  const [syncedAt,setSyncedAt] = useState(Date.now());
  const after = useRef(0);
  const runRef = useRef(initial);
  const update = (next: Run) => { runRef.current = next; setRun(next); setSyncedAt(Date.now()); };
  useEffect(() => {
    const abort = new AbortController(); let timer: ReturnType<typeof setInterval>;
    let fetching = false;
    async function refresh() {
      if (fetching) return; fetching = true;
      try { const next = await api<Run>(`/runs/${encodeURIComponent(initial.run_id)}`, {signal:abort.signal}); if (!abort.signal.aborted) update(next); }
      finally { fetching = false; }
    }
    async function connect() {
      let failures = 0;
      while (!abort.signal.aborted) {
        try {
          const response = await fetch(`/runs/${encodeURIComponent(initial.run_id)}/events?after=${after.current}`, {signal:abort.signal});
          for await (const event of readEvents(response)) {
            if (abort.signal.aborted) return;
            after.current = Math.max(after.current,event.sequence);
            setEvents(current => mergeEvents(current,event));
            setError(''); failures = 0;
          }
          await refresh();
          if (!active(runRef.current.status)) return;
        } catch (cause) {
          if (abort.signal.aborted) return;
          failures++;
          setError(`${cause instanceof Error ? cause.message : String(cause)} · 재연결 ${failures}/5`);
          if (failures >= 5) return;
        }
        await new Promise<void>(resolve => { const id = setTimeout(resolve,Math.min(1000 * 2 ** failures,10000)); abort.signal.addEventListener('abort',() => {clearTimeout(id);resolve();},{once:true}); });
      }
    }
    setError(''); void connect();
    timer = setInterval(() => { if (active(runRef.current.status)) void refresh().catch(() => {}); },3000);
    return () => {abort.abort();clearInterval(timer);};
  },[initial.run_id,revision]);
  return {run,events,error,syncedAt,reconnect:() => setRevision(n => n+1),update};
}
