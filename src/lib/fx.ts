import * as inbox from './inbox';

/**
 * 환율 두 가지를 따로 구한다:
 *  1) 네이버 실시간 PHP→KRW (시장 환율, 객관적)
 *  2) 단톡방에 올라온 평균 환율 (사장님이 실제로 거래하는 환율) — 메시지 알림에서만 추출, 추측 없음.
 * 어느 쪽도 못 구하면 null. 앱(폰)은 인터넷 제한이 없어 네이버 호출이 된다.
 */

export type NaverRate = { krwPerPhp: number; asOf: number; source: 'naver' | 'market' } | null;
export type ChatRate = {
  avg: number;
  count: number;
  latest: number;
  min: number;
  max: number;
  samples: { rate: number; time: number; room: string }[];
} | null;

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

// 환율 언급으로 볼 수 있는 숫자: 환율/페소/원 등의 바로 옆(4자 이내)에 15~35 범위의 수.
const AFTER = /(?:환율|페소|peso|php|₱)[^\d]{0,4}(\d{1,2}(?:[.,]\d{1,2})?)/gi;
const BEFORE = /(\d{1,2}(?:[.,]\d{1,2})?)[^\d]{0,4}(?:원|환율|페소|peso|php|₱)/gi;

function ratesInText(t: string): number[] {
  const out = new Set<number>();
  for (const re of [AFTER, BEFORE]) {
    re.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = re.exec(t))) {
      const v = Number(m[1].replace(',', '.'));
      if (Number.isFinite(v) && v >= 15 && v <= 35) out.add(v);
    }
  }
  return [...out];
}

/**
 * 단톡방 평균 환율: 최근 days일 메시지 알림에서 '환율/페소/원' 문맥의 15~35 숫자만 모아 평균.
 * 메시지 권한이 없거나 언급이 하나도 없으면 null(지어내지 않음). 최근 건부터 최대 20개만 쓴다.
 */
export async function chatAverageRate(days = 14): Promise<ChatRate> {
  let msgs: inbox.InboxMessage[] = [];
  try {
    const st = await inbox.inboxStatus();
    if (!st.messages) return null;
    msgs = await inbox.getMessages(Date.now() - days * 86400000, 500);
  } catch {
    return null;
  }
  const found: { rate: number; time: number; room: string }[] = [];
  for (const m of msgs) {
    const text = m.text || '';
    if (!/(환율|페소|peso|php|₱|원)/i.test(text)) continue;
    for (const rate of ratesInText(text)) {
      found.push({ rate, time: m.time, room: m.conversation || m.sender || '' });
    }
  }
  if (!found.length) return null;
  found.sort((a, b) => b.time - a.time);
  const use = found.slice(0, 20);
  const avg = use.reduce((s, x) => s + x.rate, 0) / use.length;
  return {
    avg,
    count: use.length,
    latest: use[0].time,
    min: Math.min(...use.map((x) => x.rate)),
    max: Math.max(...use.map((x) => x.rate)),
    samples: use.slice(0, 5),
  };
}
