import { useEffect, useState } from 'react';
import { Alert, Linking, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { Body, Button, Card, Input, Title } from '../components/ui';
import { cancelDailyBriefing, scheduleDailyBriefing } from '../lib/notifications';
import { useStore } from '../lib/store';
import { useTheme } from '../lib/theme';

const HOURS = [6, 7, 8, 9];

export default function Settings() {
  const t = useTheme();
  const { state, loaded, actions } = useStore();
  const [name, setName] = useState('');
  const [about, setAbout] = useState('');
  const [key, setKey] = useState('');

  useEffect(() => {
    if (!loaded) return;
    setName(state.profile.name);
    setAbout(state.profile.about);
  }, [loaded]);

  const saveProfile = () => {
    actions.setProfile({ name: name.trim(), about: about.trim() });
    Alert.alert('저장됨', '도로시가 이제 당신을 더 잘 알게 됐어요.');
  };

  const saveKey = () => {
    actions.setApiKey(key);
    setKey('');
    Alert.alert('저장됨', 'API 키는 이 기기의 보안 저장소에만 보관돼요.');
  };

  const setBriefing = async (hour: number | null) => {
    if (hour === null) {
      await cancelDailyBriefing();
      actions.setBriefingHour(null);
      return;
    }
    if (await scheduleDailyBriefing(hour)) actions.setBriefingHour(hour);
    else Alert.alert('알림 권한 필요', '휴대폰 설정에서 도로시의 알림을 허용해 주세요.');
  };

  return (
    <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
      <Card>
        <Title>나에 대해</Title>
        <Input value={name} onChangeText={setName} placeholder="이름 또는 호칭" />
        <Input
          value={about}
          onChangeText={setAbout}
          placeholder="자유롭게 소개해 주세요. 하는 일, 사는 곳, 가족, 관심사, 요즘 목표 등"
          multiline
          style={{ minHeight: 110, textAlignVertical: 'top' }}
        />
        <Button label="저장" onPress={saveProfile} />
      </Card>

      <Card>
        <Title>Claude API 키</Title>
        <Body muted>{state.apiKey ? `저장됨 (…${state.apiKey.slice(-4)})` : '아직 없어요. Anthropic 콘솔에서 발급받을 수 있어요.'}</Body>
        <Input value={key} onChangeText={setKey} placeholder="sk-ant-…" autoCapitalize="none" autoCorrect={false} secureTextEntry />
        <Button label="키 저장" onPress={saveKey} disabled={!key.trim()} />
        <Button label="API 키 발급받기" variant="ghost" onPress={() => Linking.openURL('https://platform.claude.com/settings/keys')} />
      </Card>

      <Card>
        <Title>아침 브리핑 알림</Title>
        <Body muted>매일 정해진 시간에 오늘의 브리핑을 확인하라고 알려 드려요.</Body>
        <View style={styles.hours}>
          {[null, ...HOURS].map((h) => {
            const active = state.briefingHour === h;
            return (
              <Pressable
                key={String(h)}
                onPress={() => setBriefing(h)}
                style={[styles.hour, { borderColor: active ? t.accent : t.border, backgroundColor: active ? t.accent : 'transparent' }]}
              >
                <Text style={{ color: active ? t.accentText : t.text }}>{h === null ? '끄기' : `${h}시`}</Text>
              </Pressable>
            );
          })}
        </View>
      </Card>

      <Card>
        <Title>데이터</Title>
        <Body muted>
          기억 {state.memories.length}개 · 할 일 {state.tasks.length}개 · 대화 {state.chat.length}개. 모두 이 기기에만 저장되고, 대화할 때만 Claude에 전송돼요.
        </Body>
        <Button
          label="대화 기록 지우기"
          variant="danger"
          onPress={() => Alert.alert('대화 기록 지우기', '기억과 할 일은 유지돼요.', [{ text: '취소' }, { text: '지우기', style: 'destructive', onPress: actions.clearChat }])}
        />
      </Card>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 16, gap: 12 },
  hours: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  hour: { borderWidth: 1, borderRadius: 16, paddingHorizontal: 14, paddingVertical: 8 },
});
