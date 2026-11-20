import { useState } from 'react';
import { Alert, Text, View } from 'react-native';

import { Body, Button, Card, Input, Title } from './ui';
import { pingServerBrain } from '../lib/serverBrain';
import { useStore } from '../lib/store';
import { useTheme } from '../lib/theme';

/**
 * Connects Dorothy to an open model the owner runs on their OWN computer/server (the "나만의 AI" path):
 * any OpenAI-compatible server — Ollama, llama.cpp, LM Studio, vLLM. Only the address (and an optional
 * key) are needed; "연결 테스트" confirms it and lists the models the server has. Shown under 도로시 두뇌.
 */
export function ServerBrainCard() {
  const t = useTheme();
  const { state, actions } = useStore();
  const [url, setUrl] = useState(state.serverBrain.url);
  const [model, setModel] = useState(state.serverBrain.model);
  const [key, setKey] = useState(state.serverBrain.key);
  const [testing, setTesting] = useState(false);

  const dirty = url.trim() !== state.serverBrain.url || model.trim() !== state.serverBrain.model || key.trim() !== state.serverBrain.key;

  const save = () => {
    actions.setServerBrain({ url, model, key });
    Alert.alert('저장됨', url.trim() ? '내 서버 두뇌 설정을 저장했어요.' : '내 서버 두뇌 주소를 지웠어요.');
  };

  const test = async () => {
    if (!url.trim()) {
      Alert.alert('주소 필요', '먼저 서버 주소를 넣어 주세요. 예: http://192.168.0.10:11434');
      return;
    }
    setTesting(true);
    try {
      // Save first so the test uses exactly what's shown.
      actions.setServerBrain({ url, model, key });
      const models = await pingServerBrain({ url: url.trim(), model: model.trim(), key: key.trim() });
      const list = models.slice(0, 12).join(', ');
      Alert.alert(
        '연결 성공 🎉',
        models.length
          ? `서버에 있는 모델: ${list}${models.length > 12 ? ' …' : ''}\n\n위 '모델 이름'에 이 중 하나를 적어 주세요.`
          : '연결은 됐어요. 모델 목록은 못 읽었지만, 모델 이름을 직접 넣으면 쓸 수 있어요.',
      );
    } catch (e) {
      Alert.alert('연결 실패', e instanceof Error ? e.message : String(e));
    } finally {
      setTesting(false);
    }
  };

  return (
    <Card>
      <Title>🖥️ 내 서버 두뇌 (나만의 AI)</Title>
      <Body muted>
        사장님 컴퓨터·서버에 올린 오픈 모델을 도로시의 두뇌로 써요. 대화가 외부 업체로 나가지 않고, 메시지당 비용도 없어요. Ollama·LM Studio·llama.cpp 같은 서버면 됩니다. 쓰려면 위 두뇌에서 &apos;🖥️ 내 서버 두뇌&apos;를 선택하세요.
      </Body>

      <Text style={[label, { color: t.text }]}>서버 주소</Text>
      <Input value={url} onChangeText={setUrl} placeholder="http://192.168.0.10:11434" autoCapitalize="none" autoCorrect={false} keyboardType="url" />

      <Text style={[label, { color: t.text }]}>모델 이름</Text>
      <Input value={model} onChangeText={setModel} placeholder="예: qwen2.5:7b" autoCapitalize="none" autoCorrect={false} />

      <Text style={[label, { color: t.text }]}>API 키 (선택 — 서버가 요구할 때만)</Text>
      <Input value={key} onChangeText={setKey} placeholder="보통 비워 둬요" autoCapitalize="none" autoCorrect={false} secureTextEntry />

      <View style={{ flexDirection: 'row', gap: 8, marginTop: 8 }}>
        <View style={{ flex: 1 }}>
          <Button label={testing ? '확인 중…' : '🔌 연결 테스트'} variant="ghost" onPress={test} disabled={testing || !url.trim()} />
        </View>
        <View style={{ flex: 1 }}>
          <Button label="저장" onPress={save} disabled={!dirty} />
        </View>
      </View>

      {state.serverBrain.url ? (
        <Text style={{ color: t.subtext, fontSize: 12, marginTop: 6 }}>
          현재 연결: {state.serverBrain.url}{state.serverBrain.model ? ` · ${state.serverBrain.model}` : ''}
        </Text>
      ) : null}
      <Text style={{ color: t.subtext, fontSize: 11, marginTop: 6, lineHeight: 16 }}>
        · 폰과 서버가 같은 와이파이/네트워크에 있어야 해요. 집·사무실 밖에서 쓰려면 고정 주소(도메인)나 VPN이 필요해요.{'\n'}
        · 아직은 이 두뇌로 검색·캘린더·메일·회사 서버 조회·자동 기억은 못 해요(개인 대화·메모·요약 중심). 그 기능이 필요하면 두뇌를 Claude로 바꾸면 돼요.
      </Text>
    </Card>
  );
}

const label = { marginTop: 6, fontWeight: '700' as const, fontSize: 13 };
