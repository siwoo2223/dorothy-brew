import { useFocusEffect } from 'expo-router';
import { useCallback, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import * as Notifications from 'expo-notifications';

import { Body, Card, Title } from './ui';
import { companyConfigured, loadCompanyConfig } from '../lib/company';
import { callLogGranted, inboxSupported, notificationAccessGranted, openNotificationAccess } from '../lib/inbox';
import { isProactiveEnabled } from '../lib/proactive';
import { useStore } from '../lib/store';
import { useTheme } from '../lib/theme';
import { voiceInputAvailable } from '../lib/voice';

type Item = { label: string; ok: boolean; hint: string; onPress?: () => void };

/**
 * "비서 준비 상태": one glance at what Dorothy can and can't do yet, so the owner knows which
 * switches are still off. Each unfinished item has a short hint (and, where easy, a one-tap action).
 */
export function SetupCard() {
  const t = useTheme();
  const { state } = useStore();
  const [items, setItems] = useState<Item[]>([]);

  const refresh = useCallback(async () => {
    await loadCompanyConfig().catch(() => {});
    const [calls, proactive, notif] = await Promise.all([
      callLogGranted().catch(() => false),
      isProactiveEnabled().catch(() => false),
      Notifications.getPermissionsAsync().then((p) => p.granted).catch(() => false),
    ]);
    const next: Item[] = [
      { label: 'Claude 두뇌(API 키)', ok: !!state.apiKey, hint: '아래 "도로시 두뇌"에서 키를 넣으면 똑똑하게 답해요.' },
      { label: '알림 받기', ok: notif, hint: '휴대폰 설정에서 도로시 알림을 켜면 챙김·브리핑을 받아요.' },
      { label: '회사 현황 연결', ok: companyConfigured(), hint: '아래 "회사·구글"에서 주소·토큰을 넣으면 회사 숫자를 봐요.' },
    ];
    if (inboxSupported()) {
      next.push({
        label: '메시지 알림 읽기',
        ok: notificationAccessGranted(),
        hint: '카톡·문자 알림을 읽어 회신할 것을 챙기고 키워드 알림을 보내요.',
        onPress: notificationAccessGranted() ? undefined : () => openNotificationAccess(),
      });
      next.push({ label: '통화 기록 읽기', ok: calls, hint: '부재중 전화를 회신할 사람으로 챙겨요. 아래 "내 폰 연결"에서 허용.' });
    }
    next.push({ label: '먼저 알려주기(능동 알림)', ok: proactive, hint: '아래 "알림·자동 챙김"에서 켜면 중요한 걸 먼저 알려줘요.' });
    next.push({ label: '음성으로 말하기', ok: voiceInputAvailable(), hint: '마이크·음성 인식이 되면 "도로시야"로 부를 수 있어요.' });
    setItems(next);
  }, [state.apiKey]);

  useFocusEffect(
    useCallback(() => {
      refresh();
    }, [refresh]),
  );

  const done = items.filter((i) => i.ok).length;

  return (
    <Card>
      <Title>✅ 비서 준비 상태 ({done}/{items.length})</Title>
      <Body muted>초록 체크가 많을수록 도로시가 더 많이 챙겨드려요. 회색은 아직 꺼져 있어요.</Body>
      {items.map((it) => (
        <Pressable key={it.label} onPress={it.onPress} disabled={!it.onPress} style={styles.row}>
          <Text style={{ fontSize: 16, width: 22 }}>{it.ok ? '✅' : '⬜'}</Text>
          <View style={{ flex: 1 }}>
            <Text style={{ color: t.text, fontSize: 15, fontWeight: '600' }}>{it.label}</Text>
            {!it.ok && <Text style={{ color: t.subtext, fontSize: 12 }}>{it.hint}{it.onPress ? ' 👉 눌러서 설정' : ''}</Text>}
          </View>
        </Pressable>
      ))}
    </Card>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 8 },
});
