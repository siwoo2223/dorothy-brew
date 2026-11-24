import { useEffect, useState } from 'react';
import { Alert, Linking, Pressable, Text, View } from 'react-native';

import { Body, Button, Card, Input, Title } from './ui';
import { ELEVEN_VOICES, elevenKeyHint, elevenVoiceId, setElevenKey, setElevenVoice } from '../lib/elevenlabs';
import { useTheme } from '../lib/theme';
import { speak, stopSpeaking } from '../lib/voice';

/** 도로시 목소리를 폰 기본 TTS 대신 ElevenLabs 고급 성우로. 키는 이 기기에만 저장된다. */
export function ElevenLabsCard() {
  const t = useTheme();
  const [key, setKey] = useState('');
  const [hint, setHint] = useState('');
  const [voice, setVoice] = useState(elevenVoiceId());

  useEffect(() => { elevenKeyHint().then(setHint); }, []);

  const saveKey = async () => {
    await setElevenKey(key);
    setKey('');
    setHint(await elevenKeyHint());
    Alert.alert('저장됨', key.trim() ? '이제 도로시가 고급 목소리로 말해요.' : '고급 목소리를 껐어요(폰 기본 목소리 사용).');
  };

  const pickVoice = async (id: string) => {
    await setElevenVoice(id);
    setVoice(id);
    stopSpeaking();
    speak('안녕하세요, 저는 도로시예요. 이 목소리로 말할게요.');
  };

  return (
    <Card>
      <Title>🎙️ 고급 목소리 (ElevenLabs)</Title>
      <Body muted>
        켜면 도로시가 폰 기본 목소리 대신 사람 같은 성우 목소리로 말해요. ElevenLabs 키가 필요하고(무료 한도 있음),
        키는 이 기기에만 저장돼요. 실패하면 자동으로 폰 기본 목소리로 돌아가요.
      </Body>

      <Input
        value={key}
        onChangeText={setKey}
        placeholder={hint ? `저장됨 (…${hint}) — 바꿀 때만 입력` : 'ElevenLabs API 키 (xi-api-key)'}
        autoCapitalize="none"
        autoCorrect={false}
        secureTextEntry
      />
      <Button label="목소리 키 저장" onPress={saveKey} />

      <Body muted>목소리 고르기 (누르면 들려줘요)</Body>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
        {ELEVEN_VOICES.map((v) => {
          const on = v.id === voice;
          return (
            <Pressable
              key={v.id}
              onPress={() => pickVoice(v.id)}
              style={{ borderWidth: 1, borderRadius: 14, paddingHorizontal: 12, paddingVertical: 8, borderColor: on ? t.accent : t.border, backgroundColor: on ? t.accent : 'transparent' }}
            >
              <Text style={{ color: on ? t.accentText : t.text, fontWeight: '700', fontSize: 13 }}>{v.name}</Text>
              <Text style={{ color: on ? t.accentText : t.subtext, fontSize: 11 }}>{v.note}</Text>
            </Pressable>
          );
        })}
      </View>

      <Button label="🔊 지금 목소리 들어보기" variant="ghost" onPress={() => { stopSpeaking(); speak('안녕하세요 사장님, 도로시입니다. 무엇을 도와드릴까요?'); }} />
      <Pressable onPress={() => Linking.openURL('https://elevenlabs.io/app/settings/api-keys')}>
        <Text style={{ color: t.accent, fontSize: 12 }}>→ ElevenLabs에서 API 키 받기 (elevenlabs.io)</Text>
      </Pressable>
    </Card>
  );
}
