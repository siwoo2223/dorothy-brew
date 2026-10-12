import { useEffect, useState } from 'react';
import { Alert, Pressable, StyleSheet, Text } from 'react-native';

import { clearVoicePrint, deleteRecording, enrollFromRecordings, hasVoicePrint, matchScore } from '../lib/speaker';
import { useStore } from '../lib/store';
import { useTheme } from '../lib/theme';
import { recordUtterance, voiceInputAvailable } from '../lib/voice';
import { Body, Button, Card, Title } from './ui';

// Short phrases to read while enrolling; variety helps capture the voice, not one fixed sentence.
const PHRASES = ['도로시야 안녕', '오늘 날씨 어때', '내일 일정 알려줘', '지금 몇 시야', '이번 주에 놓친 거 있어'];

export function VoiceMatchCard() {
  const t = useTheme();
  const { state, actions } = useStore();
  const [enrolled, setEnrolled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [prompt, setPrompt] = useState('');

  useEffect(() => {
    hasVoicePrint().then(setEnrolled);
  }, []);

  if (!voiceInputAvailable()) return null;

  const enroll = async () => {
    setBusy(true);
    const uris: string[] = [];
    try {
      for (let i = 0; i < PHRASES.length; i++) {
        setPrompt(`(${i + 1}/${PHRASES.length}) 또박또박 말해 주세요: "${PHRASES[i]}"`);
        // Give a moment to read the phrase before the mic opens.
        await new Promise((r) => setTimeout(r, 900));
        const uri = await recordUtterance(`enroll-${i}.wav`);
        if (uri) uris.push(uri);
      }
      setPrompt('목소리를 분석하는 중…');
      const res = await enrollFromRecordings(uris);
      uris.forEach(deleteRecording);
      setEnrolled(await hasVoicePrint());
      if (res.ok) actions.setSpeakerMatch(true);
      Alert.alert(res.ok ? '등록 완료' : '다시 해 주세요', res.message);
    } catch (e) {
      Alert.alert('오류', e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      setPrompt('');
    }
  };

  const test = async () => {
    setBusy(true);
    setPrompt('테스트: "도로시야"라고 말해 주세요');
    try {
      await new Promise((r) => setTimeout(r, 700));
      const uri = await recordUtterance('voice-test.wav');
      const score = uri ? await matchScore(uri) : null;
      deleteRecording(uri);
      Alert.alert(
        '목소리 확인',
        score === null ? '소리를 못 들었어요. 조용한 곳에서 다시 해 주세요.' : `내 목소리와의 일치도: ${Math.round(score * 100)}%\n(높을수록 Leo 목소리에 가까워요.)`,
      );
    } finally {
      setBusy(false);
      setPrompt('');
    }
  };

  const remove = () =>
    Alert.alert('목소리 등록 지우기', '등록한 목소리를 지우면 대기 모드에서 다시 모든 목소리에 반응해요.', [
      { text: '취소' },
      {
        text: '지우기',
        style: 'destructive',
        onPress: async () => {
          await clearVoicePrint();
          actions.setSpeakerMatch(false);
          setEnrolled(false);
        },
      },
    ]);

  const toggle = () => {
    if (!enrolled) {
      Alert.alert('먼저 목소리를 등록하세요', '아래 "내 목소리 등록하기"를 먼저 해 주세요.');
      return;
    }
    actions.setSpeakerMatch(!state.speakerMatch);
  };

  return (
    <Card>
      <Title>내 목소리만 (대기 모드)</Title>
      <Body muted>
        대기 모드에서 내 목소리일 때만 반응하게 해요. 내 목소리를 몇 번 녹음해 휴대폰 안에서만 분석하고, 부를 때마다 비교해요. 녹음은 밖으로 나가지 않아요. 완벽하진 않아서, 시끄러운 곳이나 비슷한 목소리는 가끔 틀릴 수 있어요.
      </Body>

      <Pressable
        onPress={toggle}
        disabled={busy}
        style={[styles.pill, { borderColor: state.speakerMatch ? t.accent : t.border, backgroundColor: state.speakerMatch ? t.accent : 'transparent' }]}
      >
        <Text style={{ color: state.speakerMatch ? t.accentText : t.text }}>
          {state.speakerMatch ? '✅ 내 목소리만 켜짐' : '내 목소리만 쓰기 (꺼짐)'}
        </Text>
      </Pressable>

      {prompt ? <Body>{prompt}</Body> : null}

      <Button label={busy ? '녹음 중…' : enrolled ? '내 목소리 다시 등록하기' : '내 목소리 등록하기'} onPress={enroll} disabled={busy} />
      {enrolled && !busy && (
        <>
          <Button label="목소리 테스트" variant="ghost" onPress={test} />
          <Button label="목소리 등록 지우기" variant="danger" onPress={remove} />
        </>
      )}
    </Card>
  );
}

const styles = StyleSheet.create({
  pill: { borderWidth: 1, borderRadius: 16, paddingHorizontal: 14, paddingVertical: 10, alignItems: 'center' },
});
