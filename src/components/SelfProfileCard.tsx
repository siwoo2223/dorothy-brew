import AsyncStorage from '@react-native-async-storage/async-storage';
import { useEffect, useState } from 'react';
import { Alert, Switch, View } from 'react-native';

import { Body, Button, Card, Title } from './ui';
import { profileOwnerNow } from '../lib/proactive';
import { useStore } from '../lib/store';
import { useTheme } from '../lib/theme';

const PREF = 'dorothy.selfProfile';

/**
 * '나 자동 파악': 말로 알려주지 않아도, 도로시가 최근 메시지·통화·회사 활동을 보고 사장에 대한
 * 새 사실을 스스로 찾아 기억에 쌓는다(하루 한 번 자동). 여기서 켜고/끄고, 지금 바로 돌려볼 수도 있다.
 */
export function SelfProfileCard() {
  const t = useTheme();
  const { getState, actions } = useStore();
  const [on, setOn] = useState(true);
  const [busy, setBusy] = useState(false);

  useEffect(() => { AsyncStorage.getItem(PREF).then((v) => setOn(v !== 'false')); }, []);

  const toggle = async (v: boolean) => {
    setOn(v);
    await AsyncStorage.setItem(PREF, v ? 'true' : 'false');
  };

  const runNow = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const fresh = await profileOwnerNow(getState());
      if (!fresh.length) {
        Alert.alert('새로 알게 된 건 없어요', '최근 대화·메시지·활동에서 더 알아낼 새 사실이 없었어요. 더 쌓이면 자동으로 기억해둘게요.');
        return;
      }
      fresh.forEach((f) => actions.addMemory(f.category, f.content));
      Alert.alert(
        `${fresh.length}가지 새로 알게 됐어요 🧠`,
        fresh.map((f) => `· ${f.content}`).join('\n') + '\n\n기억 탭에서 확인·수정·삭제할 수 있어요.',
      );
    } catch (e) {
      Alert.alert('파악 실패', e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
        <Title>🧠 나 자동 파악</Title>
        <Switch value={on} onValueChange={toggle} trackColor={{ true: t.accent }} />
      </View>
      <Body muted>
        켜두면 도로시가 하루 한 번, 말로 알려주지 않아도 최근 메시지·통화 상대·회사 활동에서 사장님에 대한
        사실(자주 연락하는 사람·관계, 반복 업무, 중요한 거래처, 자주 가는 곳, 취향 등)을 스스로 찾아 기억에
        쌓아요. 추측은 넣지 않고, 모두 이 폰에만 저장되며 기억 탭에서 언제든 확인·삭제할 수 있어요.
      </Body>
      <Button label={busy ? '파악하는 중…' : '🧠 지금 바로 파악하기'} onPress={runNow} disabled={busy} />
    </Card>
  );
}
