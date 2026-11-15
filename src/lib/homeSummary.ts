/**
 * Small, read-only summaries for the home screen: what arrived overnight (KakaoTalk, SMS, missed
 * calls) and a one-glance company status. Everything here is best effort — a missing permission,
 * an unconfigured company server or an unexpected JSON shape just yields zeros / null, never throws.
 */
import { getStoredCalls, isMissed } from './calls';
import * as company from './company';
import { collectMessages, getSms } from './inbox';

export type Overnight = { kakao: number; sms: number; missed: number };

/** "밤사이" = since 9 PM yesterday (or the last 12 hours, whichever is longer). */
export function overnightSince(now = new Date()): number {
  const ninePmYesterday = new Date(now);
  ninePmYesterday.setDate(now.getDate() - 1);
  ninePmYesterday.setHours(21, 0, 0, 0);
  return Math.min(ninePmYesterday.getTime(), now.getTime() - 12 * 3600_000);
}

export async function overnightCounts(): Promise<Overnight> {
  const since = overnightSince();
  const [msgs, sms, calls] = await Promise.all([
    collectMessages({ sinceMs: since, limit: 1000 }).catch(() => []),
    getSms(since, 500).catch(() => []),
    getStoredCalls().catch(() => []),
  ]);
  return {
    kakao: msgs.filter((m) => m.app === '카카오톡' || m.app === 'com.kakao.talk').length,
    sms: sms.filter((s) => s.type === 'received').length,
    missed: calls.filter((c) => c.time >= since && isMissed(c)).length,
  };
}

export type CompanySummary = { line: string; badge: string | null; next: string | null };

const num = (v: unknown) => (typeof v === 'number' ? v : typeof v === 'string' && v.trim() !== '' && !isNaN(Number(v)) ? Number(v) : null);

/** Collects { 상태: 건수 } from whatever status breakdown the report carries (object or array form). */
function statusCounts(node: unknown, out: Record<string, number> = {}, depth = 0): Record<string, number> {
  if (!node || typeof node !== 'object' || depth > 3) return out;
  if (Array.isArray(node)) {
    for (const it of node) {
      if (it && typeof it === 'object') {
        const o = it as Record<string, unknown>;
        const label = o.status ?? o.label ?? o.name;
        const n = num(o.count ?? o.cnt ?? o.total ?? o.value);
        if (typeof label === 'string' && n !== null) out[label] = n;
      }
    }
    return out;
  }
  for (const [k, v] of Object.entries(node as Record<string, unknown>)) {
    const n = num(v);
    if (n !== null && /[가-힣]/.test(k)) out[k] = n;
    else if (v && typeof v === 'object') statusCounts(v, out, depth + 1);
  }
  return out;
}

function findMonthTotal(node: unknown, depth = 0): number | null {
  if (!node || typeof node !== 'object' || Array.isArray(node) || depth > 2) return null;
  for (const [k, v] of Object.entries(node as Record<string, unknown>)) {
    if (/month/i.test(k) && num(v) !== null) return num(v);
  }
  for (const v of Object.values(node as Record<string, unknown>)) {
    const n = findMonthTotal(v, depth + 1);
    if (n !== null) return n;
  }
  return null;
}

/** First upcoming departure in the "attention" payload, as "코드 · MM/DD". */
function nextDeparture(attention: unknown): string | null {
  if (!attention || typeof attention !== 'object') return null;
  const lists = Object.entries(attention as Record<string, unknown>).filter(([k, v]) => Array.isArray(v) && /depart|voyage|출항/i.test(k));
  for (const [, list] of lists) {
    for (const it of list as Record<string, unknown>[]) {
      if (!it || typeof it !== 'object') continue;
      const name = [it.customer, it.title, it.name].find((x) => typeof x === 'string') as string | undefined;
      const code = [it.code, it.voyage_code, it.voyage].find((x) => typeof x === 'string') as string | undefined;
      const date = [it.departure_date, it.etd, it.depart_date, it.date].find((x) => typeof x === 'string') as string | undefined;
      if (!code && !name) continue;
      const md = date ? date.slice(5, 10).replace('-', '/') : '';
      return [name, code ? `(${code})` : '', md].filter(Boolean).join(' ');
    }
  }
  return null;
}

const fmt = (n: number) => n.toLocaleString('ko-KR');

export async function companySummary(): Promise<CompanySummary | null> {
  if (!company.companyConfigured()) return null;
  try {
    const [reportText, attentionText] = await Promise.all([
      company.fetchCompanyReport('report'),
      company.fetchCompanyAttention().catch(() => ''),
    ]);
    const d = JSON.parse(reportText);
    const counts = statusCounts(d?.shipments);
    const picked = ['통관', '배송중'].filter((k) => counts[k] !== undefined);
    const line = picked.length
      ? picked.map((k) => `${k} ${fmt(counts[k])}`).join(' · ')
      : `진행중 ${fmt(num(d?.shipments?.in_progress) ?? 0)} · 오늘 신규 ${fmt(num(d?.shipments?.today_new) ?? 0)}`;
    const month = findMonthTotal(d?.shipments);
    let next: string | null = null;
    try {
      next = attentionText ? nextDeparture(JSON.parse(attentionText)) : null;
    } catch {
      next = null;
    }
    return { line, badge: month !== null ? fmt(month) : null, next };
  } catch {
    return null;
  }
}
