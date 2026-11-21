/**
 * 선적 역산 플래너: 출항일(ETD) 또는 마감일에서 거꾸로, 주말·공휴일을 빼고 "언제까지 작업·반입·
 * 서류를 해야 하는지" 영업일로 계산해 안내한다. 냉동(리퍼)/드라이도 구분해 알려 준다.
 *
 * ⚠ 공휴일은 아래 KR_HOLIDAYS(최선의 추정)로 거르되, 결과에 '건너뛴 날'을 항상 보여줘서
 * 사장이 틀린 공휴일을 바로잡을 수 있게 한다(extraHolidays로 추가·보정 가능).
 */

// 한국 공휴일(2026 하반기~2027, 대체공휴일 포함 best-effort). 틀리면 사장 확인 후 보정.
const KR_HOLIDAYS = new Set<string>([
  '2026-12-25',
  '2027-01-01',
  '2027-02-06', '2027-02-07', '2027-02-08', '2027-02-09', // 설 연휴(+대체)
  '2027-03-01',
  '2027-05-01', '2027-05-05', '2027-05-13', // 노동절·어린이날·부처님오신날
  '2027-06-06', '2027-06-07', // 현충일(+대체)
  '2027-07-17',
  '2027-08-15', '2027-08-16', // 광복절(+대체)
  '2027-09-14', '2027-09-15', '2027-09-16', // 추석 연휴
  '2027-10-03', '2027-10-04', '2027-10-09', // 개천절(+대체)·한글날
  '2027-12-25',
]);

const pad = (n: number) => String(n).padStart(2, '0');
const toIso = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const WD = ['일', '월', '화', '수', '목', '금', '토'];
const label = (iso: string) => {
  const d = new Date(iso + 'T00:00:00');
  return `${iso.slice(5).replace('-', '/')}(${WD[d.getDay()]})`;
};

function holidaySet(extra?: string[]): Set<string> {
  if (!extra?.length) return KR_HOLIDAYS;
  const s = new Set(KR_HOLIDAYS);
  for (const e of extra) if (/^\d{4}-\d{2}-\d{2}$/.test(e)) s.add(e);
  return s;
}

const isOff = (d: Date, hol: Set<string>) => d.getDay() === 0 || d.getDay() === 6 || hol.has(toIso(d));

/** anchor(YYYY-MM-DD)에서 n 영업일 이동한 날(음수면 과거). 주말·공휴일은 세지 않는다. */
export function addWorkdays(anchorIso: string, n: number, extra?: string[]): string {
  const hol = holidaySet(extra);
  const d = new Date(anchorIso + 'T00:00:00');
  if (n === 0) return toIso(d);
  const step = n > 0 ? 1 : -1;
  let left = Math.abs(n);
  while (left > 0) {
    d.setDate(d.getDate() + step);
    if (!isOff(d, hol)) left--;
  }
  return toIso(d);
}

/** a..b 사이에 낀 '건너뛴 날'(주말·공휴일)을 사람이 읽기 좋게 나열(최대 10개). */
function skippedBetween(aIso: string, bIso: string, extra?: string[]): string[] {
  const hol = holidaySet(extra);
  const out: string[] = [];
  const [lo, hi] = aIso <= bIso ? [aIso, bIso] : [bIso, aIso];
  const d = new Date(lo + 'T00:00:00');
  const end = new Date(hi + 'T00:00:00');
  while (d <= end && out.length < 10) {
    if (isOff(d, hol)) {
      const wknd = d.getDay() === 0 || d.getDay() === 6;
      out.push(`${label(toIso(d))}${wknd ? '' : ' 공휴일'}`);
    }
    d.setDate(d.getDate() + 1);
  }
  return out;
}

export type PlanInput = {
  etd?: string;        // 출항일 YYYY-MM-DD
  cutoff?: string;     // 마감일 YYYY-MM-DD (반입/작업 마감)
  workDays?: number;   // 작업 소요 영업일(기본 1)
  container?: 'reefer' | 'dry' | null;
  extraHolidays?: string[];
};

/** 역산 플랜을 사람이 읽는 한국어 안내로 만든다. */
export function planShipment(input: PlanInput): string {
  const workDays = Math.max(1, Math.round(input.workDays ?? 1));
  const extra = input.extraHolidays;
  const lines: string[] = [];
  const contName = input.container === 'reefer' ? '냉동(리퍼)' : input.container === 'dry' ? '드라이' : null;

  if (input.etd && /^\d{4}-\d{2}-\d{2}$/.test(input.etd)) {
    const etd = input.etd;
    const doc = addWorkdays(etd, -3, extra);     // 서류마감: 출항 3영업일 전
    const cy = addWorkdays(etd, -2, extra);      // 반입마감: 출항 2영업일 전
    const workDone = addWorkdays(etd, -2, extra); // 작업 완료(최소): 출항 2영업일 전
    const workDoneSafe = addWorkdays(etd, -3, extra); // 여유: 3영업일 전
    const workStart = addWorkdays(workDone, -(workDays - 1), extra);
    lines.push(`🚢 출항(ETD): ${label(etd)}`);
    lines.push(`• 서류마감: ${label(doc)} (출항 3영업일 전)`);
    lines.push(`• 반입마감(CY): ${label(cy)} (출항 2영업일 전)`);
    lines.push(workDays > 1
      ? `• 작업: ${label(workStart)} 시작 → ${label(workDone)} 완료 (약 ${workDays}영업일, 최소 출항 2영업일 전 완료 / 여유 ${label(workDoneSafe)})`
      : `• 작업: ${label(workDone)}까지 완료 (최소 출항 2영업일 전 / 여유 ${label(workDoneSafe)})`);
    lines.push(`\n(주말·공휴일 제외 역산) 건너뛴 날: ${skippedBetween(workStart, etd, extra).join(', ') || '없음'}`);
  } else if (input.cutoff && /^\d{4}-\d{2}-\d{2}$/.test(input.cutoff)) {
    const cutoff = input.cutoff;
    const workStart = addWorkdays(cutoff, -(workDays - 1), extra);
    lines.push(`⏰ 마감: ${label(cutoff)}`);
    lines.push(workDays > 1
      ? `• 작업: ${label(workStart)} 시작 → ${label(cutoff)}까지 완료 (약 ${workDays}영업일)`
      : `• 작업: ${label(cutoff)}까지 완료`);
    lines.push(`• 서류·반입은 마감일(${label(cutoff)}) 전에 맞춰 준비하세요.`);
    lines.push(`\n(주말·공휴일 제외 역산) 건너뛴 날: ${skippedBetween(workStart, cutoff, extra).join(', ') || '없음'}`);
  } else {
    return '출항일(ETD)이나 마감일 중 하나는 알려주세요(YYYY-MM-DD 또는 "12월 7일"처럼).';
  }

  if (contName) {
    lines.push(
      input.container === 'reefer'
        ? `\n❄️ 컨테이너: 냉동(리퍼) — 전원(플러그인)·사전 예냉이 필요해, 반입·세팅을 드라이보다 하루쯤 더 일찍 잡는 게 안전해요.`
        : `\n📦 컨테이너: 드라이 — 일반 상온 화물.`,
    );
  }
  lines.push('\n※ 공휴일은 앱 기준이며, 틀리면 알려주시면 바로잡아 다시 계산할게요.');
  return lines.join('\n');
}
