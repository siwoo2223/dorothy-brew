import type { Followup } from './types';
import { getSentReplies, replyWatchGranted, callLogGranted, getCalls, type SentReply, type CallEntry } from './inbox';

/**
 * Auto-clears "회신 대기" items that the owner has already handled, from signals the phone can see:
 *  - messenger replies: the accessibility watcher records when the owner sends a KakaoTalk/messenger
 *    message (room-title hints only), so a follow-up whose person matches a reply sent AFTER it was
 *    registered is treated as answered.
 *  - phone call-backs: an outgoing call to the person after the follow-up was registered answers a
 *    "부재중 전화 → 전화 회신" item.
 * SMS replies are handled by the assistant (it can read sent SMS directly). Matching is conservative
 * (clear only on a real name/number match) so a still-open reply is never dropped by mistake.
 */

const norm = (s: string) => s.replace(/\s+/g, '').toLowerCase();
const digits = (s: string) => s.replace(/\D/g, '');

/** A follow-up that is about calling someone back, not messaging them. */
function isCallback(f: Followup): boolean {
  const s = `${f.source ?? ''} ${f.what}`;
  return /전화|부재중|콜백|call/i.test(s);
}

function nameMatches(who: string, hint: string): boolean {
  const a = norm(who);
  const b = norm(hint);
  if (a.length < 2 || b.length < 2) return false;
  return a.includes(b) || b.includes(a);
}

/** True when a messenger reply sent after `since` plausibly answers this person. */
export function replyAnswers(who: string, since: number, replies: SentReply[]): boolean {
  return replies.some((r) => r.time >= since && r.hints.some((h) => nameMatches(who, h)));
}

/** True when an outgoing call after `since` reaches this person (by name or number). */
export function callAnswers(who: string, since: number, calls: CallEntry[]): boolean {
  const whoDigits = digits(who);
  return calls.some((c) => {
    if (c.type !== 'outgoing' || c.time < since) return false;
    if (whoDigits.length >= 7 && digits(c.number).endsWith(whoDigits.slice(-9))) return true;
    return !!c.name && nameMatches(who, c.name);
  });
}

export type Cleared = { id: string; who: string; reason: 'kakao' | 'call' };

/**
 * Finds the open follow-ups that signals say are already handled. Pure: it does not change anything —
 * the caller completes the returned ids. `windowMs` bounds how far back signals are read.
 */
export async function findHandledFollowups(open: Followup[], windowMs = 14 * 24 * 60 * 60 * 1000): Promise<Cleared[]> {
  if (open.length === 0) return [];
  const since = Date.now() - windowMs;
  const [replies, calls] = await Promise.all([
    replyWatchGranted() ? getSentReplies(since, 300).catch(() => []) : Promise.resolve<SentReply[]>([]),
    (await callLogGranted().catch(() => false)) ? getCalls(since, 500).catch(() => []) : Promise.resolve<CallEntry[]>([]),
  ]);
  if (replies.length === 0 && calls.length === 0) return [];

  const out: Cleared[] = [];
  for (const f of open) {
    const start = f.createdAt ? Date.parse(f.createdAt) || since : since;
    if (isCallback(f)) {
      if (callAnswers(f.who, start, calls)) out.push({ id: f.id, who: f.who, reason: 'call' });
    } else if (replyAnswers(f.who, start, replies)) {
      out.push({ id: f.id, who: f.who, reason: 'kakao' });
    }
  }
  return out;
}
