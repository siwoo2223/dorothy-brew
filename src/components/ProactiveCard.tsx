import { useEffect, useState } from 'react';
import { Alert, Platform, Pressable, StyleSheet, Text } from 'react-native';

import { isProactiveEnabled, setProactive } from '../lib/proactive';
import { useStore } from '../lib/store';
import { useTheme } from '../lib/theme';
import { Body, Card, Title } from './ui';

/** Toggle for the background "먼저 알려주기" check (see src/lib/proactive.ts). */
export function ProactiveCard() {
  const t = useTheme();
  const { state } = useStore();
  const [on, setOn] = useState(false);

  useEffect(() => {
    isProactiveEnabled().then(setOn);
  }, []);

  if (Platform.OS === 'web') return null;

  const toggle = async () => {
    const next = !on;
    if (next && (!state.apiKey || state.model === 'local')) {
      Alert.alert('Claude 두뇌가 필요해요', '먼저 알려주기는 중요한 내용을 가려내야 해서 Claude(Haiku/Opus)가 필요해요. 설정에서 API 키를 넣고 두뇌를 Claude로 바꿔 주세요.');
      return;
    }
    setOn(next);
    await setProactive(next);
    if (next) Alert.alert('켰어요', '이제 중요한 메시지·부재중 전화·메일이 오면 도로시가 먼저 알려드려요. 휴대폰 사정에 따라 보통 15~30분 간격으로 확인해요. (밤 10시~아침 7시는 조용히 모아만 둬요.)');
  };

  return (
    <Card>
      <Title>먼저 알려주기</Title>
      <Body muted>
        물어보지 않아도, 중요한 게 생기면 도로시가 먼저 알려드려요. 새 메시지·부재중 전화·안 읽은 메일 중 놓치면 곤란한 것만 골라 알림을 보내요. 메시지·통화·메일 연결이 켜져 있어야 하고, 내용을 가려내기 위해 Claude 두뇌를 써요.
      </Body>
      <Pressable
        onPress={toggle}
        style={[styles.pill, { borderColor: on ? t.accent : t.border, backgroundColor: on ? t.accent : 'transparent' }]}
      >
        <Text style={{ color: on ? t.accentText : t.text }}>{on ? '✅ 먼저 알려주기 켜짐' : '먼저 알려주기 (꺼짐)'}</Text>
      </Pressable>
    </Card>
  );
}

const styles = StyleSheet.create({
  pill: { borderWidth: 1, borderRadius: 16, paddingHorizontal: 14, paddingVertical: 10, alignItems: 'center' },
});
