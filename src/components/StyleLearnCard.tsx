import AsyncStorage from '@react-native-async-storage/async-storage';
import { useEffect, useState } from 'react';
import { Alert, Switch, View } from 'react-native';

import { Body, Button, Card, Input, Title } from './ui';
import { learnOwnerStyleNow } from '../lib/proactive';
import { useStore } from '../lib/store';
import { useTheme } from '../lib/theme';

const LEARN_PREF = 'dorothy.styleLearn';

/**
 * '사장님 말투 따라하기': 도로시가 사장님이 실제로 쓴 글(도로시에게 쓴 말·보낸 문자)을 보고 말투·성격을
 * 스스로 학습해서, 대신 쓰는 메시지와(켜두면) 답변까지 사장님처럼 들리게 한다. 여기서 켜고/끄고,
 * 지금 바로 학습하고, 학습한 말투를 직접 다듬을 수도 있다.
 */
export function StyleLearnCard() {
  const t = useTheme();
  const { getState, actions } = useStore();
  const sp = getState().styleProfile;
  const [learnOn, setLearnOn] = useState(true);
  const [mirror, setMirror] = useState(sp.mirror);
  const [guide, setGuide] = useState(sp.guide);
  const [busy, setBusy] = useState(false);

  useEffect(() => { AsyncStorage.getItem(LEARN_PREF).then((v) => setLearnOn(v !== 'false')); }, []);

  const toggleLearn = async (v: boolean) => {
    setLearnOn(v);
    await AsyncStorage.setItem(LEARN_PREF, v ? 'true' : 'false');
  };

  const toggleMirror = (v: boolean) => {
    setMirror(v);
    actions.setStyleProfile({ mirror: v });
  };

  const saveGuide = () => {
    actions.setStyleProfile({ guide: guide.trim() });
    Alert.alert('저장됐어요', '이 말투로 메시지를 쓰고' + (mirror ? ', 사장님에게도 이 말투로 답할게요.' : ' 답변은 평소 말투로 할게요.'));
  };

  const learnNow = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const fresh = await learnOwnerStyleNow(getState());
      if (!fresh) {
        Alert.alert(
          '학습할 글이 부족해요',
          '아직 사장님이 쓴 글이 충분하지 않아요. 도로시와 대화를 더 나누거나, 폰(문자) 연결이 돼 있으면 보낸 문자도 재료가 돼요. 쌓이면 자동으로 말투를 익힐게요.',
        );
        return;
      }
      setGuide(fresh);
      actions.setStyleProfile({ guide: fresh, learnedAt: new Date().toISOString() });
      Alert.alert('말투를 익혔어요 🗣️', fresh.slice(0, 500) + (fresh.length > 500 ? '…' : '') + '\n\n여기서 바로 고칠 수도 있어요.');
    } catch (e) {
      Alert.alert('학습 실패', e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const learnedLabel = sp.learnedAt ? new Date(sp.learnedAt).toLocaleDateString('ko-KR') : '';

  return (
    <Card>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
        <Title>🗣️ 사장님 말투 따라하기</Title>
        <Switch value={learnOn} onValueChange={toggleLearn} trackColor={{ true: t.accent }} />
      </View>
      <Body muted>
        켜두면 도로시가 하루 한 번, 사장님이 실제로 쓴 글(도로시에게 쓴 말·보낸 문자)을 보고 말투와 성격을
        스스로 익혀요. 그럼 사장님을 대신해 보내는 메시지(회신·견적·안내문)가 사장님이 쓴 것처럼 들려요.
        남의 말투는 섞지 않고, 모두 이 폰에만 저장돼요.
      </Body>

      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 2 }}>
        <Body>도로시도 내 말투로 답하기</Body>
        <Switch value={mirror} onValueChange={toggleMirror} trackColor={{ true: t.accent }} />
      </View>

      <Button label={busy ? '말투 익히는 중…' : '🗣️ 지금 말투 학습하기'} onPress={learnNow} disabled={busy} />

      {guide ? (
        <>
          <Body muted>{learnedLabel ? `학습한 말투 (${learnedLabel} 기준) — 직접 고칠 수 있어요` : '학습한 말투 — 직접 고칠 수 있어요'}</Body>
          <Input
            value={guide}
            onChangeText={setGuide}
            multiline
            style={{ minHeight: 120, textAlignVertical: 'top' }}
            placeholder="학습된 말투 설명이 여기 나와요"
          />
          <Button label="말투 저장" variant="ghost" onPress={saveGuide} />
        </>
      ) : null}
    </Card>
  );
}
