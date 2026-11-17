import type { AppState } from './store';

/**
 * 입력창에 보여줄 제안을 '지금 상황'에서 만들어낸다(고정 목록을 돌리는 게 아니라, 실제 할 일·회신·
 * 등록 대기 건수·시간대·회사 연결 여부에서 실제 숫자·이름을 넣어 생성). 가장 관련 있는 것부터 앞에 둔다.
 * 클로드가 대화 맥락에서 다음 할 말을 제안하듯, 도로시도 사장님 상황에 맞는 질문을 제안한다.
 */
export function suggestPrompts(state: AppState, now: Date = new Date()): string[] {
  const out: string[] = [];
  const hour = now.getHours();

  const open = (state.tasks ?? []).filter((t) => !t.done);
  const nowMs = now.getTime();
  const endToday = new Date(now);
  endToday.setHours(23, 59, 59, 999);
  const overdue = open.filter((t) => t.dueAt && new Date(t.dueAt).getTime() < nowMs);
  const today = open.filter((t) => t.dueAt && new Date(t.dueAt).getTime() >= nowMs && new Date(t.dueAt).getTime() <= endToday.getTime());
  const followups = (state.followups ?? []).filter((f) => !f.done);
  const candidates = state.todoCandidates ?? [];
  const companyOn = !!(state.companyUrl && state.companyUrl.trim());

  // ── 지금 바로 챙길 것(있을 때만, 실제 숫자/이름으로 생성) ──
  if (candidates.length) out.push(`등록 대기 ${candidates.length}건 지금 확인해줘`);
  if (overdue.length) out.push(`기한 지난 할 일 ${overdue.length}건 정리해줘`);

  // ── 시간대에 맞춘 한마디 ──
  if (hour >= 18 || hour < 5) out.push('오늘 놓친 거 있는지 점검해줘');
  else out.push('오늘 뭐부터 해야 해?');

  if (today.length) out.push(`오늘 할 일 ${today.length}건 급한 순으로 알려줘`);

  if (followups.length) {
    const who = followups[0].who?.trim();
    out.push(who ? `${who}님한테 회신할 거 챙겨줘` : '회신할 거 뭐 있어?');
    if (followups.length > 1) out.push(`회신 대기 ${followups.length}명 정리해줘`);
  }

  // ── 회사가 연결돼 있을 때만 회사 관련 제안 ──
  if (companyOn) {
    out.push('회사 현황 요약해줘');
    out.push('이번 주 출항·입항 뭐 있어?');
    out.push('페소 환율 얼마야?');
    out.push('이번 달 손익 어때?');
    out.push('오래 멈춘 화물 있어?');
  }

  // ── 아침엔 브리핑 ──
  if (hour >= 5 && hour < 11) out.push('오늘 브리핑 해줘');

  // ── 개인/마무리 ──
  out.push('내일 할 일 정리해줘');
  if (hour >= 17) out.push('내일 일정·약속 미리 챙겨줘');

  // 비어 있으면(아주 초기) 최소한의 안내
  if (out.length < 2) out.push('나에 대해 기억해둘 거 알려줄게');

  return [...new Set(out)];
}
