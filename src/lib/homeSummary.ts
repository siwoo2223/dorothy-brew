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

// ── Exact readers for the dorothy_report.php payload (backend/api/dorothy_report.php) ──
// report  : { totals, by_status:[{status,cnt}], recent_status_change:{today,last7days},
//             invoices:{this_month_total,last_month_total}, accounting_this_month:{income,expense,net} }
// attention: { departing_this_week:[{voyage_code,route,departure_date}],
//              arriving_this_week:[{voyage_code,route,arrival_date}],
//              stuck_7days_plus:[{status,cnt,oldest}], delivered_unpaid_hint:<int> }
type Row = Record<string, unknown>;
const asArr = (v: unknown): Row[] => (Array.isArray(v) ? (v as Row[]) : []);
const int = (v: unknown): number | null =>
  typeof v === 'number' ? v : typeof v === 'string' && v.trim() !== '' && !isNaN(Number(v)) ? Number(v) : null;
const str = (v: unknown): string => (typeof v === 'string' ? v : typeof v === 'number' ? String(v) : '');
const fmt = (n: number) => n.toLocaleString('ko-KR');
/** "2026-11-15" → "11/15". */
const md = (v: unknown): string => { const s = str(v); return s.length >= 10 ? s.slice(5, 10).replace('-', '/') : s; };

/** Status breakdown straight from report.by_status, newest-first by count. */
function statusList(report: Row): { label: string; value: number }[] {
  return asArr(report.by_status)
    .map((r) => ({ label: str(r.status), value: int(r.cnt) ?? 0 }))
    .filter((s) => s.label !== '' && s.value > 0);
}

/** The first voyage leaving this week, as "DRY35 · 마닐라 · 11/18". */
function firstDeparture(attention: Row | null): string | null {
  const it = asArr(attention?.departing_this_week)[0];
  if (!it) return null;
  return [str(it.voyage_code), str(it.route), md(it.departure_date)].filter(Boolean).join(' · ') || null;
}

export async function companySummary(): Promise<CompanySummary | null> {
  if (!company.companyConfigured()) return null;
  try {
    const [reportText, attentionText] = await Promise.all([
      company.fetchCompanyReport('report'),
      company.fetchCompanyAttention().catch(() => ''),
    ]);
    const d = JSON.parse(reportText) as Row;
    const counts = statusList(d);
    const map = Object.fromEntries(counts.map((c) => [c.label, c.value]));
    const picked = ['통관', '배송중'].filter((k) => map[k] !== undefined);
    const line = picked.length
      ? picked.map((k) => `${k} ${fmt(map[k])}`).join(' · ')
      : counts.slice(0, 2).map((c) => `${c.label} ${fmt(c.value)}`).join(' · ') || '진행 중 화물 없음';
    const monthSales = int((d.invoices as Row)?.this_month_total);
    const badge = monthSales !== null ? `₱${fmt(monthSales)}` : null;
    let next: string | null = null;
    try {
      next = attentionText ? firstDeparture(JSON.parse(attentionText) as Row) : null;
    } catch {
      next = null;
    }
    return { line, badge, next };
  } catch {
    return null;
  }
}

/** Builds the "오늘 챙길 화물" list exactly from the attention payload's known shapes. */
function attentionItems(a: Row, max = 8): { title: string; sub: string }[] {
  const out: { title: string; sub: string }[] = [];
  for (const r of asArr(a.stuck_7days_plus)) {
    const cnt = int(r.cnt);
    if (out.length < max && str(r.status) && cnt) out.push({ title: `오래 멈춤 · ${str(r.status)}`, sub: `${fmt(cnt)}건 (7일+)` });
  }
  const unpaid = int(a.delivered_unpaid_hint);
  if (unpaid && out.length < max) out.push({ title: '미수 가능(배송완료·입금 0)', sub: `${fmt(unpaid)}건` });
  for (const r of asArr(a.departing_this_week)) {
    if (out.length < max) out.push({ title: `출항 예정 · ${str(r.voyage_code)}`, sub: [str(r.route), md(r.departure_date)].filter(Boolean).join(' · ') });
  }
  for (const r of asArr(a.arriving_this_week)) {
    if (out.length < max) out.push({ title: `입항 예정 · ${str(r.voyage_code)}`, sub: [str(r.route), md(r.arrival_date)].filter(Boolean).join(' · ') });
  }
  return out;
}

export type Dashboard = {
  statuses: { label: string; value: number }[];
  monthSales: string | null; // 이번 달 매출(인보이스 합계)
  monthNet: string | null; // 이번 달 수지(수입-지출)
  lastMonthSales: string | null;
  todayNew: number | null; // 오늘 상태 바뀐 화물 수
  attention: { title: string; sub: string }[];
  next: string | null;
};

/** Full read-only snapshot for the 회사 dashboard tab — all fields read exactly from the server. */
export async function companyDashboard(): Promise<Dashboard | null> {
  if (!company.companyConfigured()) return null;
  try {
    const [reportText, attentionText] = await Promise.all([
      company.fetchCompanyReport('report'),
      company.fetchCompanyAttention().catch(() => ''),
    ]);
    const d = JSON.parse(reportText) as Row;
    const statuses = statusList(d).slice(0, 8);
    const inv = (d.invoices as Row) ?? {};
    const acct = (d.accounting_this_month as Row) ?? {};
    const monthSales = int(inv.this_month_total);
    const lastMonthSales = int(inv.last_month_total);
    const net = int(acct.net);
    const todayNew = int((d.recent_status_change as Row)?.today);
    let attention: { title: string; sub: string }[] = [];
    let next: string | null = null;
    if (attentionText) {
      try {
        const a = JSON.parse(attentionText) as Row;
        attention = attentionItems(a);
        next = firstDeparture(a);
      } catch {
        /* ignore malformed attention payload */
      }
    }
    return {
      statuses,
      monthSales: monthSales !== null ? `₱${fmt(monthSales)}` : null,
      monthNet: net !== null ? `₱${fmt(net)}` : null,
      lastMonthSales: lastMonthSales !== null ? `₱${fmt(lastMonthSales)}` : null,
      todayNew,
      attention,
      next,
    };
  } catch {
    return null;
  }
}

/** Best-effort list of "needs attention" shipments from the attention payload, for the 회사 탭. */
function attentionItems(attention: unknown, max = 8): { title: string; sub: string }[] {
  if (!attention || typeof attention !== 'object') return [];
  const out: { title: string; sub: string }[] = [];
  const seen = new Set<string>();
  for (const [key, list] of Object.entries(attention as Record<string, unknown>)) {
    if (!Array.isArray(list)) continue;
    for (const it of list as Record<string, unknown>[]) {
      if (!it || typeof it !== 'object' || out.length >= max) continue;
      const title = [it.customer, it.title, it.name, it.tracking_no, it.code].find((x) => typeof x === 'string' && x) as string | undefined;
      if (!title || seen.has(title)) continue;
      seen.add(title);
      const code = [it.code, it.voyage, it.voyage_code].find((x) => typeof x === 'string' && x) as string | undefined;
      const status = [it.status, it.reason, it.stage].find((x) => typeof x === 'string' && x) as string | undefined;
      const days = num(it.days ?? it.stalled_days ?? it.days_stuck);
      const bits = [code && code !== title ? code : null, status, days !== null ? `${days}일` : null, groupLabel(key)].filter(Boolean);
      out.push({ title, sub: bits.join(' · ') });
    }
  }
  return out;
}

/** Turns an attention list key into a short Korean tag (best effort). */
function groupLabel(key: string): string | null {
  if (/stall|stuck|멈춘|오래/i.test(key)) return '오래 멈춤';
  if (/depart|출항/i.test(key)) return '출항 예정';
  if (/unpaid|미수|outstanding/i.test(key)) return '미수금';
  if (/arriv|입항/i.test(key)) return '입항';
  return null;
}

export type Dashboard = {
  statuses: { label: string; value: number }[];
  month: string | null;
  attention: { title: string; sub: string }[];
  next: string | null;
};

/** Full read-only snapshot for the 회사 dashboard tab: status breakdown, month figure, attention list. */
export async function companyDashboard(): Promise<Dashboard | null> {
  if (!company.companyConfigured()) return null;
  try {
    const [reportText, attentionText] = await Promise.all([
      company.fetchCompanyReport('report'),
      company.fetchCompanyAttention().catch(() => ''),
    ]);
    const d = JSON.parse(reportText);
    const counts = statusCounts(d?.shipments);
    const statuses = Object.entries(counts)
      .map(([label, value]) => ({ label, value }))
      .filter((s) => s.value > 0)
      .sort((a, b) => b.value - a.value)
      .slice(0, 8);
    const month = findMonthTotal(d?.shipments);
    let attention: { title: string; sub: string }[] = [];
    let next: string | null = null;
    if (attentionText) {
      try {
        const a = JSON.parse(attentionText);
        attention = attentionItems(a);
        next = nextDeparture(a);
      } catch {
        /* ignore malformed attention payload */
      }
    }
    return { statuses, month: month !== null ? fmt(month) : null, attention, next };
  } catch {
    return null;
  }
}
