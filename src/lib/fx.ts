import AsyncStorage from '@react-native-async-storage/async-storage';

import * as inbox from './inbox';

/**
 * 환율 두 가지를 따로 구한다:
 *  1) 네이버 실시간 PHP→KRW (시장 환율, 객관적)
 *  2) 단톡방에 올라온 평균 환율 (사장님이 실제로 거래하는 환율) — 메시지 알림에서만 추출, 추측 없음.
 * 어느 쪽도 못 구하면 null. 앱(폰)은 인터넷 제한이 없어 네이버 호출이 된다.
 */

export type NaverRate = { krwPerPhp: number; asOf: number; source: 'naver' | 'market' } | null;
export type RoomRate = { room: string; avg: number; count: number; latest: number; min: number; max: number };
export type ChatRate = {
  rooms: RoomRate[]; // 방별 평균(사장님이 고른 방만)
  combinedAvg: number; // 두 방 합친 평균
  combinedCount: number;
} | null;

// 사장님이 고른 환전방 기본값(부분 일치). 설정에서 바꿀 수 있다.
const DEFAULT_FX_ROOMS = ['손대손', '환전/호텔방'];

const NAVER_URLS = [
  'https://m.stock.naver.com/front-api/marketIndex/prices?category=exchange&reutersCode=FX_PHPKRW&page=1&pageSize=1',
  'https://api.stock.naver.com/marketindex/exchange/FX_PHPKRW',
];

function num(x: unknown): number {
  if (typeof x === 'number') return x;
  if (typeof x === 'string') {
    const n = Number(x.replace(/,/g, ''));
    return Number.isFinite(n) ? n : NaN;
  }
  return NaN;
}

/** 네이버 내부 API 응답 어디에 들어있든 closePrice/calcPrice 류의 환율 숫자를 찾아낸다(형식이 자주 바뀜). */
function digNaver(j: any): number {
  const keys = ['closePrice', 'calcPrice', 'price', 'nv', 'num'];
  const seen = new Set<any>();
  const walk = (o: any, depth: number): number => {
    if (!o || depth > 6 || seen.has(o)) return NaN;
    if (typeof o === 'object') seen.add(o);
    if (Array.isArray(o)) {
      for (const x of o) { const v = walk(x, depth + 1); if (Number.isFinite(v)) return v; }
      return NaN;
    }
    if (typeof o === 'object') {
      for (const k of keys) { if (k in o) { const v = num(o[k]); if (Number.isFinite(v) && v > 5 && v < 100) return v; } }
      for (const k of Object.keys(o)) { const v = walk(o[k], depth + 1); if (Number.isFinite(v)) return v; }
    }
    return NaN;
  };
  return walk(j, 0);
}

/**
 * 실시간 PHP→KRW(₩ per ₱1). 먼저 네이버 여러 경로를 시도하고(source='naver'),
 * 안 되면 공개 무료 환율 API로 백업(source='market'). 폰은 인터넷 제한이 없어 호출된다.
 * 전부 실패하면 null. 함수 이름은 호환 위해 유지.
 */
export async function naverPhpKrw(): Promise<NaverRate> {
  // 1) 네이버 내부 API (형식이 자주 바뀌므로 응답 전체를 뒤져 환율 숫자를 찾는다)
  for (const url of NAVER_URLS) {
    try {
      const r = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0', Accept: 'application/json', Referer: 'https://m.stock.naver.com/' } });
      if (!r.ok) continue;
      const j = await r.json();
      const v = digNaver(j);
      if (Number.isFinite(v)) return { krwPerPhp: v, asOf: Date.now(), source: 'naver' };
    } catch {
      // try next
    }
  }
  // 2) 공개 무료 환율 API 백업 (키 불필요, PHP 포함)
  const markets: { url: string; pick: (j: any) => number }[] = [
    { url: 'https://open.er-api.com/v6/latest/PHP', pick: (j) => num(j?.rates?.KRW) },
    { url: 'https://api.exchangerate.host/latest?base=PHP&symbols=KRW', pick: (j) => num(j?.rates?.KRW) },
    { url: 'https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/php.json', pick: (j) => num(j?.php?.krw) },
  ];
  for (const m of markets) {
    try {
      const r = await fetch(m.url, { headers: { Accept: 'application/json' } });
      if (!r.ok) continue;
      const v = m.pick(await r.json());
      if (Number.isFinite(v) && v > 5 && v < 100) return { krwPerPhp: v, asOf: Date.now(), source: 'market' };
    } catch {
      // try next
    }
  }
  return null;
}

// 환전방 메시지에서 환율을 뽑는다: 환율 신호어(환율·페소·테더·달러·매입/매도·삽/팝 등) 바로 옆
// (5자 이내)에 20~30 범위의 두 자리 수만 환율로 본다. 날짜(25일)·수량(500만)·호실은 제외된다.
const FX_KW = '환율|페소|peso|php|₱|테더|usdt|달러|매입|매도|삽니|팝니|팔|사|시오디|코드|환전';
const FX_AFTER = new RegExp(`(?:${FX_KW})[^\\d]{0,5}(\\d{2}(?:[.,]\\d{1,2})?)`, 'gi');
const FX_BEFORE = new RegExp(`(\\d{2}(?:[.,]\\d{1,2})?)[^\\d]{0,5}(?:${FX_KW})`, 'gi');

function fxRatesInText(t: string): number[] {
  const out = new Set<number>();
  for (const re of [FX_AFTER, FX_BEFORE]) {
    re.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = re.exec(t))) {
      const v = Number(m[1].replace(',', '.'));
      if (Number.isFinite(v) && v >= 20 && v <= 30) out.add(v);
    }
  }
  return [...out];
}

/**
 * 단톡방 평균 환율: 사장님이 고른 환전방(기본 2곳)에서만, 최근 days일 메시지 알림의 환율 숫자를 모아
 * 방별로 평균을 낸다(두 개의 평균 + 합친 평균). 메시지 권한이 없거나 환율 언급이 없으면 null.
 * 방마다 최근 20건만 쓴다. 지어내지 않는다.
 */
export async function chatAverageRate(days = 14): Promise<ChatRate> {
  let fxRooms = DEFAULT_FX_ROOMS;
  let msgs: inbox.InboxMessage[] = [];
  try {
    const raw = await AsyncStorage.getItem('dorothy.fxRooms');
    if (raw) { const arr = JSON.parse(raw); if (Array.isArray(arr) && arr.length) fxRooms = arr; }
    const st = await inbox.inboxStatus();
    if (!st.messages) return null;
    msgs = await inbox.getMessages(Date.now() - days * 86400000, 1500);
  } catch {
    return null;
  }
  const low = fxRooms.map((r) => r.toLowerCase());
  // 매칭된 방 조각별로 환율을 모은다(방 이름은 실제 메시지의 방 이름을 쓴다).
  const groups = new Map<string, { name: string; items: { rate: number; time: number }[] }>();
  for (const m of msgs) {
    const conv = m.conversation || m.sender || '';
    const hay = conv.toLowerCase();
    const idx = low.findIndex((r) => hay.includes(r));
    if (idx < 0) continue;
    const rates = fxRatesInText(m.text || '');
    if (!rates.length) continue;
    const key = fxRooms[idx];
    const g = groups.get(key) ?? { name: conv, items: [] };
    for (const rate of rates) g.items.push({ rate, time: m.time });
    groups.set(key, g);
  }
  const rooms: RoomRate[] = [];
  const all: number[] = [];
  for (const g of groups.values()) {
    if (!g.items.length) continue;
    g.items.sort((a, b) => b.time - a.time);
    const use = g.items.slice(0, 20);
    const vals = use.map((x) => x.rate);
    rooms.push({
      room: g.name,
      avg: vals.reduce((s, x) => s + x, 0) / vals.length,
      count: use.length,
      latest: use[0].time,
      min: Math.min(...vals),
      max: Math.max(...vals),
    });
    all.push(...vals);
  }
  if (!rooms.length) return null;
  return { rooms, combinedAvg: all.reduce((s, x) => s + x, 0) / all.length, combinedCount: all.length };
}

/**
 * 범용 환율(아무 통화쌍, 예: KRW→PHP, USD→PHP). KRW↔PHP는 naverPhpKrw()가 더 정확하니
 * get_exchange_rate 도구에서 그쪽을 먼저 쓰고, 그 외 통화에 이 함수를 쓴다. 무료·키 없는 API 두 곳.
 */
export type FxRate = { from: string; to: string; rate: number; date: string; source: string };

const _rateCache: Record<string, { at: number; v: FxRate }> = {};

export async function getRate(from: string, to: string): Promise<FxRate | null> {
  const f = (from || '').toUpperCase();
  const t = (to || '').toUpperCase();
  if (!f || !t) return null;
  if (f === t) return { from: f, to: t, rate: 1, date: '', source: 'same' };
  const key = `${f}_${t}`;
  const c = _rateCache[key];
  if (c && Date.now() - c.at < 3 * 60 * 60 * 1000) return c.v;

  const sources: { url: string; pick: (j: any) => { rate: number; date: string; src: string } | null }[] = [
    { url: `https://open.er-api.com/v6/latest/${encodeURIComponent(f)}`, pick: (j) => (j?.result === 'success' && num(j?.rates?.[t]) > 0 ? { rate: num(j.rates[t]), date: String(j?.time_last_update_utc ?? '').slice(0, 16), src: 'open.er-api.com' } : null) },
    { url: `https://api.frankfurter.app/latest?base=${encodeURIComponent(f)}&symbols=${encodeURIComponent(t)}`, pick: (j) => (num(j?.rates?.[t]) > 0 ? { rate: num(j.rates[t]), date: String(j?.date ?? ''), src: 'frankfurter.app (ECB)' } : null) },
  ];
  for (const s of sources) {
    try {
      const r = await fetch(s.url, { headers: { Accept: 'application/json' } });
      if (!r.ok) continue;
      const hit = s.pick(await r.json());
      if (hit) {
        const v: FxRate = { from: f, to: t, rate: hit.rate, date: hit.date, source: hit.src };
        _rateCache[key] = { at: Date.now(), v };
        return v;
      }
    } catch {
      // try next
    }
  }
  return null;
}
