import * as company from './company';
import type { StoreActions } from './store';
import type { Sailing } from './types';

/**
 * 선사 사이트에서 매일 수집돼 서버에 적재된 선편(kf_carrier_schedule)을 읽어, 도로시의 출항 스케줄
 * (Sailing)로 자동 공급한다. 폰이 사이트를 직접 긁지 않고 서버만 읽는다. 중복·알림 중복은
 * store.upsertAutoSailings가 carrier+voyageNo 키로 막는다.
 */

const str = (v: unknown): string => (v == null ? '' : String(v)).trim();
const firstStr = (row: Record<string, unknown>, keys: string[]): string => {
  for (const k of keys) {
    const v = row[k];
    if (v != null && String(v).trim() !== '') return String(v).trim();
  }
  return '';
};

/**
 * "M/D"(또는 "MM/DD")를 오늘 기준 YYYY-MM-DD로. 수집 스케줄은 가까운 미래라서, 해당 월/일이
 * 오늘보다 2주 이상 과거면 내년으로 넘긴다(12월→1월 롤오버 처리: 12/28과 1/03이 같은 주에 섞여도
 * 1/03을 내년으로 본다).
 */
export function mdToIso(md: string, today = new Date()): string {
  const m = md.trim().match(/^(\d{1,2})\s*[/.-]\s*(\d{1,2})/);
  if (!m) return '';
  const month = Number(m[1]);
  const day = Number(m[2]);
  if (month < 1 || month > 12 || day < 1 || day > 31) return '';
  const y0 = today.getFullYear();
  const pad = (n: number) => String(n).padStart(2, '0');
  const make = (y: number) => `${y}-${pad(month)}-${pad(day)}`;
  // 오늘(자정)보다 14일 이상 과거면 내년 것으로 본다.
  const todayMid = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
  const thisYear = new Date(y0, month - 1, day).getTime();
  if (thisYear < todayMid - 14 * 86400000) return make(y0 + 1);
  return make(y0);
}

/** "11/23 17:00" → { date: "2026-11-23", time: "17:00", raw } (날짜/시간 분리). */
function parseCut(cut: string, today: Date): { date: string; time: string } {
  const c = cut.trim();
  if (!c) return { date: '', time: '' };
  const date = mdToIso(c, today);
  const tm = c.match(/(\d{1,2}:\d{2})/);
  return { date, time: tm ? tm[1] : '' };
}

/** 한 서버 행 → Sailing 후보(없는 값은 비움). 컬럼명은 여러 표기를 관대하게 받아들인다. */
export function rowToSailing(row: Record<string, unknown>, today = new Date()): Omit<Sailing, 'id' | 'createdAt'> | null {
  const carrier = firstStr(row, ['carrier', 'line', 'scac']);
  const etdRaw = firstStr(row, ['etd', 'departure', 'etd_md']);
  const etd = etdRaw ? mdToIso(etdRaw, today) : '';
  if (!carrier && !etd) return null; // 최소한 선사나 출항일은 있어야
  const etaRaw = firstStr(row, ['eta', 'arrival', 'eta_md']);
  const cut = parseCut(firstStr(row, ['cut', 'cutoff', 'closing', 'cy_cutoff']), today);
  const typeRaw = firstStr(row, ['type', 'mode', 'routing']).toUpperCase();
  const transship = /T\/?S|TRANSSHIP|환적/.test(typeRaw) ? true : /DIRECT|직항/.test(typeRaw) ? false : undefined;
  const transitRaw = firstStr(row, ['transit', 'transit_days', 'days']);
  const transitNum = transitRaw ? Number(transitRaw.replace(/[^\d.]/g, '')) : NaN;
  const vessel = firstStr(row, ['vessel', 'vessel_name', 'ship']);
  const service = firstStr(row, ['svc', 'service', 'lane']);
  const voyageNo = firstStr(row, ['voy', 'voyage', 'voyage_no', 'voyageno']);
  const terminal = firstStr(row, ['terminal']);
  const extraNote = firstStr(row, ['note', 'remark', 'remarks']);

  const noteParts: string[] = [];
  if (cut.time) noteParts.push(`마감 ${cut.time}`);
  if (terminal) noteParts.push(`터미널 ${terminal}`);
  if (extraNote) noteParts.push(extraNote);

  return {
    carrier: carrier || '선사미상',
    voyageNo: voyageNo || undefined,
    from: firstStr(row, ['from', 'pol', 'origin']) || '부산',
    to: firstStr(row, ['to', 'pod', 'dest', 'destination']) || '마닐라 북항',
    docCutoff: undefined, // 수집값 cut은 서류·반입 통합 1개라 cyCutoff 한 칸만 써서 알림 중복을 막는다
    cyCutoff: cut.date || undefined,
    etd: etd || undefined,
    eta: etaRaw ? mdToIso(etaRaw, today) || undefined : undefined,
    vessel: vessel || undefined,
    service: service || undefined,
    transitDays: Number.isFinite(transitNum) ? transitNum : undefined,
    transship,
    note: noteParts.join(' · ') || undefined,
    source: 'auto',
  };
}

export type SailingSyncResult = { added: number; updated: number; unchanged: number; total: number };

/** 서버에서 수집된 선편을 읽어 Sailing으로 공급한다. 데이터가 없으면 total 0. */
export async function syncCarrierSchedule(actions: StoreActions): Promise<SailingSyncResult> {
  const rows = await company.fetchCarrierScheduleRows();
  const today = new Date();
  const candidates = rows
    .map((r) => rowToSailing(r, today))
    .filter((s): s is Omit<Sailing, 'id' | 'createdAt'> => !!s);
  if (candidates.length === 0) return { added: 0, updated: 0, unchanged: 0, total: 0 };
  const r = await actions.upsertAutoSailings(candidates);
  return { ...r, total: candidates.length };
}
