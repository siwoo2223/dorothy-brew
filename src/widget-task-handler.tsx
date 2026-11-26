import AsyncStorage from '@react-native-async-storage/async-storage';
import type { WidgetTaskHandlerProps } from 'react-native-android-widget';

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

/** 홈 위젯이 추가·갱신·리사이즈될 때 호출. 백그라운드(헤드리스)에서 돈다. */
export async function widgetTaskHandler(props: WidgetTaskHandlerProps) {
  const name = props.widgetInfo.widgetName;
  switch (props.widgetAction) {
    case 'WIDGET_ADDED':
    case 'WIDGET_UPDATE':
    case 'WIDGET_RESIZED':
    case 'WIDGET_CLICK':
      if (name === 'Today') props.renderWidget(<TodayWidget lines={await todayLines()} />);
      break;
    default:
      break;
  }
}
