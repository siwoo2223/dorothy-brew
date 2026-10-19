import { useEffect, useState } from 'react';
import { Platform, Pressable, StyleSheet, Text, View } from 'react-native';

import { useStore } from '../lib/store';
import { useTheme } from '../lib/theme';
import { listKoreanVoices, previewVoice } from '../lib/voice';
import { Body, Card, Title } from './ui';

/** Lets the user pick which system voice reads Dorothy's spoken replies. */
export function VoicePickerCard() {
  const t = useTheme();
  const { state, actions } = useStore();
  const [voices, setVoices] = useState<{ identifier: string; name: string }[]>([]);

  useEffect(() => {
    listKoreanVoices().then(setVoices);
  }, []);

  if (Platform.OS === 'web') return null;

  const options = [{ identifier: '', name: '휴대폰 기본' }, ...voices];

  const pick = (id: string) => {
    actions.setVoiceId(id);
    if (id) previewVoice(id);
  };

  return (
    <Card>
      <Title>도로시 목소리</Title>
      <Body muted>
        도로시가 소리 내어 답할 때 쓰는 목소리를 골라요. 눌러서 들어보고 정하면 돼요. 목소리가 하나뿐이면, 휴대폰 설정 → 일반 → 글자 읽어주기(TTS)에서 음성 데이터를 더 받으면 많아져요.
      </Body>
      <View style={styles.row}>
        {options.map((v) => {
          const active = state.voiceId === v.identifier;
          return (
            <Pressable
              key={v.identifier || 'default'}
              onPress={() => pick(v.identifier)}
              style={[styles.pill, { borderColor: active ? t.accent : t.border, backgroundColor: active ? t.accent : 'transparent' }]}
            >
              <Text style={{ color: active ? t.accentText : t.text, fontSize: 13 }}>{active ? '✅ ' : ''}{v.name}</Text>
            </Pressable>
          );
        })}
      </View>
      {voices.length === 0 && <Body muted>이 휴대폰에서 고를 수 있는 한국어 목소리를 찾지 못했어요. 휴대폰 TTS 설정을 확인해 주세요.</Body>}

      <Body muted>말하기 속도</Body>
      <View style={styles.row}>
        {SPEEDS.map((s) => {
          const active = Math.abs(state.speechRate - s.value) < 0.01;
          return (
            <Pressable
              key={s.label}
              onPress={() => {
                actions.setSpeechRate(s.value);
                previewVoice(state.voiceId);
              }}
              style={[styles.pill, { borderColor: active ? t.accent : t.border, backgroundColor: active ? t.accent : 'transparent' }]}
            >
              <Text style={{ color: active ? t.accentText : t.text, fontSize: 13 }}>{s.label}</Text>
            </Pressable>
          );
        })}
      </View>
    </Card>
  );
}

const SPEEDS = [
  { label: '느리게', value: 0.85 },
  { label: '보통', value: 1.05 },
  { label: '빠르게', value: 1.3 },
];

const styles = StyleSheet.create({
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  pill: { borderWidth: 1, borderRadius: 16, paddingHorizontal: 14, paddingVertical: 8 },
});
