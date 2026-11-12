import AsyncStorage from '@react-native-async-storage/async-storage';

import * as contacts from './contacts';
import * as inbox from './inbox';

/**
 * A local, persistent archive of the phone's call log. The phone's own call log is read live
 * (inbox.getCalls), but it can be pruned and carries no names/notes; here each call is snapshotted
 * into the app — deduped, enriched with the contact name — so Dorothy keeps a lasting "통화 기록"
 * she can show and search, and so recording summaries can attach to it. Stays on the phone only.
 */
export type CallRecord = {
  id: string; // `${time}-${normalizedNumber}` — stable, so re-syncing never duplicates
  time: number;
  number: string;
  name: string; // contact / cached name, or '' when unknown
  type: inbox.CallEntry['type'];
  durationSec: number;
};

const KEY = 'dorothy.callHistory';
const MAX = 3000;

let mem: CallRecord[] | null = null;

async function load(): Promise<CallRecord[]> {
  if (mem) return mem;
  try {
    const raw = await AsyncStorage.getItem(KEY);
    mem = raw ? (JSON.parse(raw) as CallRecord[]) : [];
  } catch {
    mem = [];
  }
  return mem;
}

async function persist(list: CallRecord[]): Promise<void> {
  mem = list;
  try {
    await AsyncStorage.setItem(KEY, JSON.stringify(list));
  } catch {
    // best-effort; the live call log is still the source of truth
  }
}

const idOf = (c: { time: number; number: string }) => `${c.time}-${(c.number || '').replace(/\D/g, '')}`;

/**
 * Snapshots the phone's recent call log into the store, deduped and name-enriched. No-op (returns
 * whatever is stored) when the call-log permission isn't granted. Returns the whole store, newest first.
 */
export async function syncCalls(days = 14): Promise<CallRecord[]> {
  const store = await load();
  const have = new Set(store.map((c) => c.id));
  await contacts.loadContacts().catch(() => {});
  const since = Date.now() - days * 86_400_000;
  const calls = await inbox.getCalls(since, 500).catch(() => [] as inbox.CallEntry[]);
  let changed = false;
  for (const c of calls) {
    const id = idOf(c);
    if (have.has(id)) continue;
    store.push({
      id,
      time: c.time,
      number: c.number,
      name: c.name || contacts.nameFor(c.number) || '',
      type: c.type,
      durationSec: c.durationSec,
    });
    have.add(id);
    changed = true;
  }
  if (changed) {
    store.sort((a, b) => b.time - a.time);
    if (store.length > MAX) store.length = MAX;
    await persist(store);
  }
  return mem!;
}

export async function getStoredCalls(): Promise<CallRecord[]> {
  return (await load()).slice();
}

/** Calls since midnight (local), newest first. */
export async function todaysCalls(): Promise<CallRecord[]> {
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  return (await load()).filter((c) => c.time >= start.getTime());
}

export const isMissed = (c: CallRecord) => c.type === 'missed' || c.type === 'rejected';

export function missed(list: CallRecord[]): CallRecord[] {
  return list.filter(isMissed);
}

/** For Dorothy: search the stored history by name/number and an optional day window. */
export async function query(opts: { keyword?: string; days?: number; limit?: number }): Promise<CallRecord[]> {
  const list = await load();
  const since = opts.days ? Date.now() - opts.days * 86_400_000 : 0;
  const q = (opts.keyword || '').trim().toLowerCase();
  const qDigits = q.replace(/\D/g, '');
  return list
    .filter((c) => c.time >= since)
    .filter(
      (c) =>
        !q ||
        c.name.toLowerCase().includes(q) ||
        (qDigits && c.number.replace(/\D/g, '').includes(qDigits)),
    )
    .slice(0, opts.limit ?? 100);
}

const TYPE: Record<CallRecord['type'], string> = {
  incoming: '받은 전화',
  outgoing: '건 전화',
  missed: '부재중',
  rejected: '거절',
  other: '기타',
};

const when = (ms: number) =>
  new Date(ms).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', weekday: 'short', hour: '2-digit', minute: '2-digit' });

export const who = (c: CallRecord) => (c.name ? `${c.name} (${c.number})` : c.number || '알 수 없는 번호');

export function format(list: CallRecord[]): string {
  return list
    .map((c) => {
      const len = c.durationSec > 0 ? `, ${Math.round(c.durationSec / 60) || 1}분` : '';
      return `- ${when(c.time)} ${TYPE[c.type]} — ${who(c)}${len}`;
    })
    .join('\n');
}
