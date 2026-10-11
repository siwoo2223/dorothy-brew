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

const BRIEFING_ID = 'daily-briefing';

/** Repeats every day at the given hour to nudge the user to open their morning briefing. */
export async function scheduleDailyBriefing(hour: number): Promise<boolean> {
  if (Platform.OS === 'web') return false;
  if (!(await ensureNotificationPermission())) return false;
  await Notifications.cancelScheduledNotificationAsync(BRIEFING_ID).catch(() => {});
  await Notifications.scheduleNotificationAsync({
    identifier: BRIEFING_ID,
    content: { title: '좋은 아침이에요 ☀️', body: '도로시가 오늘의 브리핑을 준비했어요.' },
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
