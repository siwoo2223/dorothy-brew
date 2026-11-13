import * as Notifications from 'expo-notifications';
import { Platform } from 'react-native';

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
  }),
});

export async function ensureNotificationPermission(): Promise<boolean> {
  if (Platform.OS === 'android') {
    await Notifications.setNotificationChannelAsync('reminders', {
      name: '리마인더',
      importance: Notifications.AndroidImportance.HIGH,
    });
  }
  const current = await Notifications.getPermissionsAsync();
  if (current.granted) return true;
  const requested = await Notifications.requestPermissionsAsync();
  return requested.granted;
}

/** Schedules a one-off reminder. Returns undefined when the time is in the past or permission is denied. */
export async function scheduleReminder(title: string, body: string, at: Date): Promise<string | undefined> {
  if (Platform.OS === 'web' || at.getTime() <= Date.now()) return undefined;
  if (!(await ensureNotificationPermission())) return undefined;
  return Notifications.scheduleNotificationAsync({
    content: { title, body },
    trigger: { type: Notifications.SchedulableTriggerInputTypes.DATE, date: at, channelId: 'reminders' },
  });
}

export async function cancelReminder(id?: string): Promise<void> {
  if (!id || Platform.OS === 'web') return;
  await Notifications.cancelScheduledNotificationAsync(id);
}

/**
 * Schedules a repeating reminder. weekday (expo: 1=Sun … 7=Sat) makes it weekly; omit it for daily.
 * Returns the scheduled id (cancel it with cancelReminder), or undefined if denied / on web.
 */
export async function scheduleRepeatingReminder(
  title: string,
  body: string,
  hour: number,
  minute: number,
  weekday?: number,
): Promise<string | undefined> {
  if (Platform.OS === 'web') return undefined;
  if (!(await ensureNotificationPermission())) return undefined;
  const trigger = weekday
    ? { type: Notifications.SchedulableTriggerInputTypes.WEEKLY as const, weekday, hour, minute, channelId: 'reminders' }
    : { type: Notifications.SchedulableTriggerInputTypes.DAILY as const, hour, minute, channelId: 'reminders' };
  return Notifications.scheduleNotificationAsync({ content: { title, body }, trigger });
}

const BRIEFING_ID = 'daily-briefing';

/** Repeats every day at the given hour to nudge the user to open their morning briefing. */
export async function scheduleDailyBriefing(hour: number): Promise<boolean> {
  if (Platform.OS === 'web') return false;
  if (!(await ensureNotificationPermission())) return false;
  await Notifications.cancelScheduledNotificationAsync(BRIEFING_ID).catch(() => {});
  await Notifications.scheduleNotificationAsync({
    identifier: BRIEFING_ID,
    content: {
      title: '좋은 아침이에요 ☀️',
      body: '밤사이 온 메시지·문자·부재중 전화와 오늘 일정·회사 현황을 정리했어요. 눌러서 확인하세요.',
      data: { url: '/?brief=1' },
    },
    trigger: { type: Notifications.SchedulableTriggerInputTypes.DAILY, hour, minute: 0, channelId: 'reminders' },
  });
  return true;
}

export async function cancelDailyBriefing(): Promise<void> {
  if (Platform.OS === 'web') return;
  await Notifications.cancelScheduledNotificationAsync(BRIEFING_ID).catch(() => {});
}

const PLAN_ID = 'nightly-plan';

/** Every evening: "shall I sort out tomorrow?" Tapping it opens the chat and asks for the plan (see NotificationRouter). */
export async function scheduleNightlyPlan(hour: number): Promise<boolean> {
  if (Platform.OS === 'web') return false;
  if (!(await ensureNotificationPermission())) return false;
  await Notifications.cancelScheduledNotificationAsync(PLAN_ID).catch(() => {});
  await Notifications.scheduleNotificationAsync({
    identifier: PLAN_ID,
    content: { title: '🌙 내일 할 일 정리할까요?', body: '오늘 받은 메시지와 통화를 보고 내일 할 일을 정리해 드릴게요.', data: { url: '/chat?plan=1' } },
    trigger: { type: Notifications.SchedulableTriggerInputTypes.DAILY, hour, minute: 0, channelId: 'reminders' },
  });
  return true;
}

export async function cancelNightlyPlan(): Promise<void> {
  if (Platform.OS === 'web') return;
  await Notifications.cancelScheduledNotificationAsync(PLAN_ID).catch(() => {});
}

const WEEKLY_ID = 'weekly-report';

/**
 * Repeats every week on the given weekday (expo weekday: 1=Sunday … 7=Saturday) at 9am. Tapping it
 * opens the chat and generates the weekly business report (see NotificationRouter + chat ?weekly=1).
 */
export async function scheduleWeeklyReport(weekday: number, hour = 9): Promise<boolean> {
  if (Platform.OS === 'web') return false;
  if (!(await ensureNotificationPermission())) return false;
  await Notifications.cancelScheduledNotificationAsync(WEEKLY_ID).catch(() => {});
  await Notifications.scheduleNotificationAsync({
    identifier: WEEKLY_ID,
    content: { title: '📊 주간 사업 리포트', body: '지난주 어땠는지 도로시가 정리해 드릴게요.', data: { url: '/chat?weekly=1' } },
    trigger: { type: Notifications.SchedulableTriggerInputTypes.WEEKLY, weekday, hour, minute: 0, channelId: 'reminders' },
  });
  return true;
}

export async function cancelWeeklyReport(): Promise<void> {
  if (Platform.OS === 'web') return;
  await Notifications.cancelScheduledNotificationAsync(WEEKLY_ID).catch(() => {});
}

const STANDBY_SHORTCUT_ID = 'standby-shortcut';

/**
 * Shows a persistent ("sticky") lock-screen notification. One tap opens Dorothy straight into
 * standby ("대기 모드"). Android forbids a third-party app from keeping the mic open while the
 * screen is off, so this is the reliable one-tap way back in from a locked phone. It sits on a
 * silent, low-importance channel (a shortcut, not an alert) and does not auto-dismiss.
 * Re-called on every app start (so it survives reboots/updates); the fixed id replaces, never stacks.
 */
export async function showStandbyShortcut(): Promise<boolean> {
  if (Platform.OS !== 'android') return false;
  if (!(await ensureNotificationPermission())) return false;
  await Notifications.setNotificationChannelAsync('standby', {
    name: '대기 바로가기',
    importance: Notifications.AndroidImportance.LOW, // no sound, no heads-up pop
  });
  await Notifications.scheduleNotificationAsync({
    identifier: STANDBY_SHORTCUT_ID,
    content: {
      title: '👂 도로시 대기',
      body: '여기를 누르면 바로 "도로시야"를 들을 준비를 해요.',
      data: { url: '/chat?voice=standby' },
      sticky: true, // Android ongoing: stays put, can't be swiped away
      autoDismiss: false,
    },
    trigger: { type: Notifications.SchedulableTriggerInputTypes.TIME_INTERVAL, seconds: 1, channelId: 'standby' },
  });
  return true;
}

export async function hideStandbyShortcut(): Promise<void> {
  if (Platform.OS !== 'android') return;
  await Notifications.dismissNotificationAsync(STANDBY_SHORTCUT_ID).catch(() => {});
  await Notifications.cancelScheduledNotificationAsync(STANDBY_SHORTCUT_ID).catch(() => {});
}
