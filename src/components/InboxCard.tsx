import { useFocusEffect } from 'expo-router';
import { useCallback, useEffect, useState } from 'react';
import { Alert, AppState, Pressable, StyleSheet, Text, View } from 'react-native';

import {
  clearMessages,
  inboxStatus,
  InboxStatus,
  messageCount,
  openAppDetails,
  openNotificationAccess,
  requestCallLog,
} from '../lib/inbox';
import { cancelNightlyPlan, scheduleNightlyPlan } from '../lib/notifications';
import { useStore } from '../lib/store';
import { useTheme } from '../lib/theme';
import { Body, Button, Card, Title } from './ui';

const PLAN_HOURS = [20, 21, 22];

/** Settings for reading messenger notifications and the call log, and the nightly "plan tomorrow" reminder. */
export function InboxCard() {
  const t = useTheme();
  const { state, actions } = useStore();
  const [status, setStatus] = useState<InboxStatus | null>(null);
  const [count, setCount] = useState(0);

  const refresh = useCallback(() => {
    inboxStatus().then(setStatus);
    setCount(messageCount());
  }, []);

  // Permissions are granted in the phone's settings app, so re-check when coming back from there.
  useFocusEffect(refresh);
  useEffect(() => {
    const sub = AppState.addEventListener('change', (next) => next === 'active' && refresh());
    return () => sub.remove();
  }, [refresh]);

  if (!status) return null;
  if (!status.supported) {
    return (
      <Card>
        <Title>메시지·통화</Title>
        <Body muted>안드로이드 설치용 앱(APK)에서만 쓸 수 있어요.</Body>
      </Card>
    );
  }

  const setPlanHour = async (hour: number | null) => {
    if (hour === null) {
      await cancelNightlyPlan();
      actions.setPlanHour(null);
    } else if (await scheduleNightlyPlan(hour)) {
      actions.setPlanHour(hour);
    } else {
      Alert.alert('알림 권한 필요', '휴대폰 설정에서 도로시의 알림을 허용해 주세요.');
    }
  };

  return (
    <Card>
      <Title>메시지·통화</Title>
      <Body muted>
        카카오톡, Messenger, Viber, WhatsApp, Telegram, 문자 알림과 통화 기록을 보고 내일 할 일을 챙겨 드려요. 메시지는 이 휴대폰에만 저장되고(최대 14일), 정리를 부탁할 때만 도로시의 두뇌로 보내요.
      </Body>

      <Body>📩 메신저 알림 읽기: {status.messages ? `✅ 켜짐 · 모은 메시지 ${count}개` : '꺼짐'}</Body>
      {!status.messages && (
        <>
          <Button label="알림 읽기 허용하기" onPress={openNotificationAccess} />
          <Body muted>
            목록에서 &quot;도로시 메시지 정리&quot;를 켜 주세요. 회색으로 눌리지 않으면: 아래 &quot;앱 정보 열기&quot; → 오른쪽 위 ⋮ → &quot;제한된 설정 허용&quot;을 누른 뒤 다시 해 주세요.
          </Body>
          <Button label="앱 정보 열기" variant="ghost" onPress={openAppDetails} />
        </>
      )}
      <Body muted>알림으로 온 메시지만 읽을 수 있어요. 알림을 꺼 둔 채팅방이나 내가 보낸 메시지는 볼 수 없어요.</Body>

      <Body>📞 통화 기록: {status.calls ? '✅ 허용됨' : '꺼짐'}</Body>
      {!status.calls && (
        <Button
          label="통화 기록 허용하기"
          variant="ghost"
          onPress={async () => {
            if (!(await requestCallLog())) Alert.alert('권한이 꺼져 있어요', '앱 정보 → 권한 → 통화 기록에서 허용할 수 있어요.', [{ text: '닫기' }, { text: '앱 정보 열기', onPress: openAppDetails }]);
            refresh();
          }}
        />
      )}

      <Body>🌙 매일 밤 &quot;내일 할 일&quot; 알림</Body>
      <View style={styles.row}>
        {[null, ...PLAN_HOURS].map((h) => {
          const active = state.planHour === h;
          return (
            <Pressable
              key={String(h)}
              onPress={() => setPlanHour(h)}
              style={[styles.pill, { borderColor: active ? t.accent : t.border, backgroundColor: active ? t.accent : 'transparent' }]}
            >
              <Text style={{ color: active ? t.accentText : t.text }}>{h === null ? '끄기' : `${h}시`}</Text>
            </Pressable>
          );
        })}
      </View>

      {count > 0 && (
        <Button
          label="모은 메시지 지우기"
          variant="danger"
          onPress={() =>
            Alert.alert('모은 메시지 지우기', '도로시가 저장한 메신저 알림을 모두 지워요.', [
              { text: '취소' },
              { text: '지우기', style: 'destructive', onPress: () => (clearMessages(), refresh()) },
            ])
          }
        />
      )}
    </Card>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  pill: { borderWidth: 1, borderRadius: 16, paddingHorizontal: 14, paddingVertical: 8 },
});
