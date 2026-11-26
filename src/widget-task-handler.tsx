import AsyncStorage from '@react-native-async-storage/async-storage';
import type { WidgetTaskHandlerProps } from 'react-native-android-widget';

import * as company from './lib/company';
import { getRate } from './lib/fx';
import { CompanyWidget } from './widgets/CompanyWidget';
import { FxWidget } from './widgets/FxWidget';
import { TodayWidget } from './widgets/TodayWidget';

/** 저장소(AsyncStorage)에서 오늘 할 일·회신 대기를 읽어 위젯에 보여줄 줄들을 만든다. 네트워크 없음. */
async function todayLines(): Promise<string[]> {
  try {
    const [tasksRaw, followRaw, candRaw] = await Promise.all([
      AsyncStorage.getItem('dorothy.tasks'),
      AsyncStorage.getItem('dorothy.followups'),
      AsyncStorage.getItem('dorothy.todoCandidates'),
    ]);
    const tasks: { done?: boolean; dueAt?: string; title: string }[] = tasksRaw ? JSON.parse(tasksRaw) : [];
    const follows: { done?: boolean }[] = followRaw ? JSON.parse(followRaw) : [];
    const cands: unknown[] = candRaw ? JSON.parse(candRaw) : [];
    const endToday = new Date();
    endToday.setHours(23, 59, 59, 999);
    const due = tasks.filter((t) => !t.done && t.dueAt && new Date(t.dueAt).getTime() <= endToday.getTime());
    const open = tasks.filter((t) => !t.done);
    const openFollow = follows.filter((f) => !f.done);

    const lines: string[] = [];
    lines.push(`✅ 오늘 ${due.length}개 · 진행 중 ${open.length}개`);
    if (due[0]) lines.push(`• ${due[0].title}`);
    if (openFollow.length) lines.push(`📮 회신 대기 ${openFollow.length}명`);
    if (cands.length) lines.push(`📥 등록 대기 ${cands.length}건`);
    return lines.length ? lines : ['오늘 챙길 거 없어요 👍'];
  } catch {
    return ['도로시를 한 번 열어주세요'];
  }
}

/** 회사 서버에서 '오늘 챙길 것' 요약을 받아 줄로. 연결 안 됐으면 안내. */
async function companyLines(): Promise<string[]> {
  try {
    const ok = await company.loadCompanyConfig();
    if (!ok) return ['회사 연결이 필요해요', '설정 → 회사 현황 연결'];
    const text = await company.fetchCompanyAttention();
    const lines = text
      .split('\n')
      .map((l) => l.replace(/\s+/g, ' ').trim())
      .filter(Boolean)
      .slice(0, 4);
    return lines.length ? lines : ['오늘 특별히 챙길 건 없어요 👍'];
  } catch {
    return ['회사 현황을 못 불러왔어요', '잠시 후 다시 갱신돼요'];
  }
}

/** 오늘 환율(KRW↔PHP) 한 줄씩. */
async function fxData(): Promise<{ pesoToWon: string; wonToPeso: string; date: string }> {
  try {
    const r = await getRate('PHP', 'KRW'); // 1페소 = r원
    if (!r || !r.rate) return { pesoToWon: '환율 못 불러옴', wonToPeso: '', date: '' };
    const perPeso = r.rate;
    const pesoPerThousandWon = 1000 / perPeso;
    return {
      pesoToWon: `1페소 = ₩${perPeso.toFixed(2)}`,
      wonToPeso: `₩1,000 = ₱${pesoPerThousandWon.toFixed(2)}`,
      date: r.date ? `${r.date} · ${r.source}` : r.source,
    };
  } catch {
    return { pesoToWon: '환율 못 불러옴', wonToPeso: '', date: '' };
  }
}

/** 홈 위젯이 추가·갱신·리사이즈·탭될 때 호출. 백그라운드(헤드리스)에서 돈다. */
export async function widgetTaskHandler(props: WidgetTaskHandlerProps) {
  const name = props.widgetInfo.widgetName;
  switch (props.widgetAction) {
    case 'WIDGET_ADDED':
    case 'WIDGET_UPDATE':
    case 'WIDGET_RESIZED':
    case 'WIDGET_CLICK':
      if (name === 'Today') props.renderWidget(<TodayWidget lines={await todayLines()} />);
      else if (name === 'Company') props.renderWidget(<CompanyWidget lines={await companyLines()} />);
      else if (name === 'Fx') props.renderWidget(<FxWidget {...(await fxData())} />);
      break;
    default:
      break;
  }
}
