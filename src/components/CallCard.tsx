import AsyncStorage from '@react-native-async-storage/async-storage';
import * as DocumentPicker from 'expo-document-picker';
import { useRouter } from 'expo-router';
import { useState } from 'react';
import { Alert, Platform } from 'react-native';

import { audioInfo } from '../lib/inbox';
import { useStore } from '../lib/store';
import { transcribeAudioFile, voiceInputAvailable } from '../lib/voice';
import { Body, Button, Card, Title } from './ui';

/** Pick saved call recordings, transcribe them on-device, then let Dorothy summarize and pull out tasks. */
export function CallCard() {
  const router = useRouter();
  const { state } = useStore();
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('');

  if (Platform.OS === 'web' || !voiceInputAvailable()) return null;

  const run = async () => {
    if (state.model === 'local') {
      Alert.alert('Claude 두뇌가 필요해요', '통화 내용을 정리하려면 설정에서 두뇌를 Claude로 바꿔 주세요. (글로 바꾸는 건 휴대폰이 하고, 정리는 Claude가 해요.)');
      return;
    }
    const picked = await DocumentPicker.getDocumentAsync({ type: 'audio/*', multiple: true, copyToCacheDirectory: true });
    if (picked.canceled) return;
    setBusy(true);
    try {
      const texts: string[] = [];
      for (let i = 0; i < picked.assets.length; i++) {
        setStatus(`녹음 ${i + 1}/${picked.assets.length} 글로 바꾸는 중… (길면 몇 분 걸려요)`);
        const info = await audioInfo(picked.assets[i].uri).catch(() => ({ sampleRate: 16000, channels: 1 }));
        const text = await transcribeAudioFile(picked.assets[i].uri, { sampleRate: info.sampleRate, channels: info.channels });
        if (text) texts.push(`[${picked.assets[i].name}]\n${text}`);
      }
      if (!texts.length) {
        Alert.alert('내용을 못 읽었어요', '녹음에서 말소리를 찾지 못했어요. 통화 녹음 파일이 맞는지 확인해 주세요.');
        return;
      }
      await AsyncStorage.setItem('dorothy.pendingCall', texts.join('\n\n'));
      router.navigate({ pathname: '/chat', params: { call: '1' } });
    } catch (e) {
      Alert.alert('오류', e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      setStatus('');
    }
  };

  return (
    <Card>
      <Title>통화 녹음 정리</Title>
      <Body muted>
        휴대폰에 저장된 통화 녹음을 골라 주면, 도로시가 글로 바꿔서 핵심 내용과 해야 할 일을 정리해요. 녹음은 휴대폰 안에서만 글로 바뀌고, 정리는 Claude가 해요. 갤럭시 통화 녹음은 보통 &quot;내 파일 → 내장 → Recordings → Call&quot; 에 있어요.
      </Body>
      {status ? <Body>{status}</Body> : null}
      <Button label={busy ? '정리하는 중…' : '통화 녹음 고르기'} onPress={run} disabled={busy} />
    </Card>
  );
}
