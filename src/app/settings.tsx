import { useState } from 'react';
import { Alert, Linking, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { Body, Button, Card, Input, Title } from '../components/ui';
import { GoogleCard } from '../components/GoogleCard';
import { ImportCard } from '../components/ImportCard';
import { VoiceCard } from '../components/VoiceCard';
import { cancelDailyBriefing, scheduleDailyBriefing } from '../lib/notifications';
import { useStore } from '../lib/store';
import { useTheme } from '../lib/theme';

const HOURS = [6, 7, 8, 9];

export default function Settings() {
  const t = useTheme();
  const { state, loaded, actions } = useStore();
  const [key, setKey] = useState('');

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
      {loaded && (
        // Re-mount when the stored profile changes (e.g. after an import) so the form never saves stale text over it.
        <ProfileCard key={`${state.profile.name}|${state.profile.about}`} />
      )}

      <Card>
        <Title>Claude API 키</Title>
        <Body muted>{state.apiKey ? `저장됨 (…${state.apiKey.slice(-4)})` : '아직 없어요. Anthropic 콘솔에서 발급받을 수 있어요.'}</Body>
        <Input value={key} onChangeText={setKey} placeholder="sk-ant-…" autoCapitalize="none" autoCorrect={false} secureTextEntry />
        <Button label="키 저장" onPress={saveKey} disabled={!key.trim()} />
        <Button label="API 키 발급받기" variant="ghost" onPress={() => Linking.openURL('https://platform.claude.com/settings/keys')} />
        <WorkspaceField />
      </Card>

      <VoiceCard />

      <GoogleCard />

      <ImportCard />

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

/** Only needed when the key isn't tied to a workspace (the API then asks for anthropic-workspace-id). */
function WorkspaceField() {
  const { state, actions } = useStore();
  const [value, setValue] = useState(state.workspaceId);
  return (
    <>
      <Body muted>워크스페이스 ID (선택) — &quot;not scoped to a workspace&quot; 오류가 날 때만 넣어요. 콘솔의 Settings → Workspaces에서 wrkspc_로 시작하는 ID를 복사하세요.</Body>
      <Input value={value} onChangeText={setValue} placeholder="wrkspc_…" autoCapitalize="none" autoCorrect={false} />
      <Button
        label={state.workspaceId ? '워크스페이스 ID 변경' : '워크스페이스 ID 저장'}
        variant="ghost"
        disabled={value.trim() === state.workspaceId}
        onPress={() => {
          actions.setWorkspaceId(value);
          Alert.alert('저장됨', value.trim() ? '이제 이 워크스페이스로 요청해요.' : '워크스페이스 ID를 지웠어요.');
        }}
      />
    </>
  );
}

function ProfileCard() {
  const { state, actions } = useStore();
  const [name, setName] = useState(state.profile.name);
  const [about, setAbout] = useState(state.profile.about);

  const save = () => {
    actions.setProfile({ name: name.trim(), about: about.trim() });
    Alert.alert('저장됨', '도로시가 이제 당신을 더 잘 알게 됐어요.');
  };

  return (
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
      <Button label="저장" onPress={save} />
    </Card>
  );
}

const styles = StyleSheet.create({
  container: { padding: 16, gap: 12 },
  hours: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  hour: { borderWidth: 1, borderRadius: 16, paddingHorizontal: 14, paddingVertical: 8 },
});
