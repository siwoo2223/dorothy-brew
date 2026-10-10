import { Linking, Platform, Pressable, StyleSheet, Text, View } from 'react-native';

import { useStore, VoiceOnOpen } from '../lib/store';
import { useTheme } from '../lib/theme';
import { voiceInputAvailable } from '../lib/voice';
import { Body, Button, Card, Title } from './ui';

const OPTIONS: { value: VoiceOnOpen; label: string }[] = [
  { value: 'off', label: '끄기' },
  { value: 'listen', label: '바로 듣기' },
  { value: 'standby', label: '대기 모드' },
];

export function VoiceCard() {
  const t = useTheme();
  const { state, actions } = useStore();
  if (!voiceInputAvailable()) {
    return (
      <Card>
        <Title>음성 호출</Title>
        <Body muted>음성 인식은 설치용 앱(APK)에서 쓸 수 있어요. README의 “휴대폰에 설치하기”를 참고해 주세요.</Body>
      </Card>
    );
  }
  return (
    <Card>
      <Title>음성 호출</Title>
      <Body muted>앱을 열 때 어떻게 할지 골라 주세요. 대기 모드에서는 화면이 켜진 동안 &quot;도로시야&quot;라고 부르면 바로 대답해요.</Body>
      <View style={styles.row}>
        {OPTIONS.map((o) => {
          const active = state.voiceOnOpen === o.value;
          return (
            <Pressable
              key={o.value}
              onPress={() => actions.setVoiceOnOpen(o.value)}
              style={[styles.pill, { borderColor: active ? t.accent : t.border, backgroundColor: active ? t.accent : 'transparent' }]}
            >
              <Text style={{ color: active ? t.accentText : t.text }}>{o.label}</Text>
            </Pressable>
          );
        })}
      </View>
      {Platform.OS === 'android' && (
        <>
          <Body>빅스비처럼 바로 부르기 (갤럭시)</Body>
          <Body muted>
            • 측면 버튼 두 번 누르기: 설정 → 유용한 기능 → 측면 버튼 → 두 번 누르기 → 앱 열기 → 도로시{'\n'}
            • 기본 디지털 비서로 지정: 설정 → 애플리케이션 → 기본 앱 선택 → 디지털 어시스턴트 앱 → 도로시. 그러면 화면 아래 모서리에서 대각선으로 쓸어 올려도 도로시가 열려요.{'\n'}
            • 빅스비로: &quot;하이 빅스비, 도로시 열어 줘&quot;
          </Body>
          <Button label="휴대폰 기본 앱 설정 열기" variant="ghost" onPress={() => Linking.sendIntent('android.settings.MANAGE_DEFAULT_APPS_SETTINGS').catch(() => Linking.openSettings())} />
        </>
      )}
    </Card>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  pill: { borderWidth: 1, borderRadius: 16, paddingHorizontal: 14, paddingVertical: 8 },
});
