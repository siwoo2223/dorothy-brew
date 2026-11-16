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

/** An "오늘 챙길 화물" row, with where its 조회 button should drill into (status/filter/code). */
export type AttentionItem = { title: string; sub: string; status?: string; filter?: 'stuck' | 'unpaid'; code?: string };

/** Builds the "오늘 챙길 화물" list exactly from the attention payload's known shapes. */
function attentionItems(a: Row, max = 8): AttentionItem[] {
  const out: AttentionItem[] = [];
  for (const r of asArr(a.stuck_7days_plus)) {
    const cnt = int(r.cnt);
    const status = str(r.status);
    if (out.length < max && status && cnt) out.push({ title: `오래 멈춤 · ${status}`, sub: `${fmt(cnt)}건 (7일+)`, status, filter: 'stuck' });
  }
  const unpaid = int(a.delivered_unpaid_hint);
  if (unpaid && out.length < max) out.push({ title: '미수 가능(배송완료·입금 0)', sub: `${fmt(unpaid)}건`, status: '배송완료', filter: 'unpaid' });
  for (const r of asArr(a.departing_this_week)) {
    if (out.length < max) out.push({ title: `출항 예정 · ${str(r.voyage_code)}`, sub: [str(r.route), md(r.departure_date)].filter(Boolean).join(' · '), code: str(r.voyage_code) });
  }
  for (const r of asArr(a.arriving_this_week)) {
    if (out.length < max) out.push({ title: `입항 예정 · ${str(r.voyage_code)}`, sub: [str(r.route), md(r.arrival_date)].filter(Boolean).join(' · '), code: str(r.voyage_code) });
  }
  return out;
}

// ── Shipment list (for the 조회 detail page), read exactly from company_shipments ──
export type Shipment = {
  trackingNo: string;
  customer: string;
  voyageCode: string;
  status: string;
  daysInStatus: number | null; // how long it has sat in its current status
  items: string;
  boxes: number;
  cbm: number;
  weightKg: number;
  unpaid: boolean; // 배송완료인데 입금액 0/빈 값
  departureDate: string;
};
export type ShipmentsPage = { total: number | null; hasMore: boolean; shipments: Shipment[] };

const daysSince = (v: unknown): number | null => {
  const s = str(v);
  if (!s) return null;
  const ms = Date.parse(s.replace(' ', 'T'));
  if (isNaN(ms)) return null;
  return Math.max(0, Math.floor((Date.now() - ms) / 86400_000));
};

/** One page of shipments for a status (optionally refined client-side to stuck-7day+ or unpaid). */
export async function shipmentsPage(opts: { status?: string; code?: string; filter?: 'stuck' | 'unpaid'; page?: number }): Promise<ShipmentsPage> {
  const text = await company.fetchCompanyShipments({ status: opts.status, code: opts.code, page: opts.page });
  const d = JSON.parse(text) as Row;
  let list: Shipment[] = asArr(d.shipments).map((r) => {
    const paid = int(r.paid_amount);
    return {
      trackingNo: str(r.tracking_no),
      customer: str(r.customer),
      voyageCode: str(r.voyage_code),
      status: str(r.status),
      daysInStatus: daysSince(r.status_changed_at),
      items: str(r.items),
      boxes: int(r.boxes) ?? 0,
      cbm: int(r.cbm) ?? 0,
      weightKg: int(r.weight_kg) ?? 0,
      unpaid: str(r.status) === '배송완료' && (paid === null || paid === 0),
      departureDate: str(r.departure_date),
    };
  });
  if (opts.filter === 'stuck') list = list.filter((s) => s.daysInStatus !== null && s.daysInStatus >= 7);
  else if (opts.filter === 'unpaid') list = list.filter((s) => s.unpaid);
  return { total: int(d.total), hasMore: d.has_more === true, shipments: list };
}

export type Dashboard = {
  statuses: { label: string; value: number }[];
  monthIncome: string | null; // 이번 달 수입 (회계 income)
  monthExpense: string | null; // 이번 달 지출 (회계 expense)
  monthNet: string | null; // 이번 달 수지 (수입-지출)
  invoiceThisMonth: string | null; // 이번 달 발행 인보이스 합계 (참고)
  invoiceLastMonth: string | null;
  todayNew: number | null; // 오늘 상태 바뀐 화물 수
  attention: AttentionItem[];
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
    const income = int(acct.income);
    const expense = int(acct.expense);
    const net = int(acct.net);
    const invThis = int(inv.this_month_total);
    const invLast = int(inv.last_month_total);
    const todayNew = int((d.recent_status_change as Row)?.today);
    const peso = (n: number | null) => (n !== null ? `₱${fmt(n)}` : null);
    let attention: AttentionItem[] = [];
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
      monthIncome: peso(income),
      monthExpense: peso(expense),
      monthNet: peso(net),
      invoiceThisMonth: peso(invThis),
      invoiceLastMonth: peso(invLast),
      todayNew,
      attention,
      next,
    };
  } catch {
    return null;
  }
}
