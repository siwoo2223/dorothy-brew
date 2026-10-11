import { useEffect, useState } from 'react';
import { Alert, Text, View } from 'react-native';

import {
  cancelLocalDownload,
  deleteLocalModel,
  downloadLocalModel,
  LOCAL_MODEL,
  localBrainSupported,
  localModelDownloaded,
} from '../lib/localBrain';
import { useStore } from '../lib/store';
import { useTheme } from '../lib/theme';
import { Body, Button } from './ui';

/** Download / delete controls for the on-device model (about 2 GB). */
export function LocalBrainPanel() {
  const t = useTheme();
  const { actions } = useStore();
  const [ready, setReady] = useState<boolean | null>(null);
  const [progress, setProgress] = useState<number | null>(null);

  useEffect(() => {
    localModelDownloaded().then(setReady);
  }, []);

  if (!localBrainSupported()) {
    return <Body muted>휴대폰 두뇌는 설치용 앱(APK)에서만 쓸 수 있어요.</Body>;
  }

  const start = () =>
    Alert.alert('휴대폰 두뇌 내려받기', `${LOCAL_MODEL.name} 모델(약 ${(LOCAL_MODEL.approxMB / 1000).toFixed(1)}GB)을 내려받아요. Wi‑Fi에서 받는 걸 추천해요.`, [
      { text: '취소' },
      {
        text: '내려받기',
        onPress: async () => {
          setProgress(0);
          try {
            await downloadLocalModel(setProgress);
            const ok = await localModelDownloaded();
            setReady(ok);
            if (ok) {
              actions.setModel('local');
              Alert.alert('준비됐어요', '이제 도로시가 휴대폰 안에서 대답해요. 인터넷이 없어도 되고 비용도 들지 않아요.');
            }
          } catch (e) {
            Alert.alert('내려받기 실패', e instanceof Error ? e.message : String(e));
          } finally {
            setProgress(null);
          }
        },
      },
    ]);

  if (progress !== null) {
    return (
      <View style={{ gap: 6 }}>
        <Body>내려받는 중… {Math.round(progress * 100)}%</Body>
        <View style={{ height: 8, borderRadius: 4, backgroundColor: t.border, overflow: 'hidden' }}>
          <View style={{ width: `${Math.round(progress * 100)}%`, height: 8, backgroundColor: t.accent }} />
        </View>
        <Text style={{ color: t.subtext, fontSize: 12 }}>앱을 켜 둔 채로 기다려 주세요.</Text>
        <Button label="취소" variant="ghost" onPress={() => cancelLocalDownload().then(() => setProgress(null))} />
      </View>
    );
  }

  if (ready) {
    return (
      <View style={{ gap: 6 }}>
        <Body muted>✅ {LOCAL_MODEL.name} 준비됨 · 휴대폰 안에서만 동작해요.</Body>
        <Button
          label="모델 삭제 (저장 공간 확보)"
          variant="danger"
          onPress={() =>
            Alert.alert('모델 삭제', '휴대폰 두뇌를 지우면 다시 내려받아야 해요.', [
              { text: '취소' },
              {
                text: '삭제',
                style: 'destructive',
                onPress: async () => {
                  await deleteLocalModel();
                  setReady(false);
                  actions.setModel('haiku');
                },
              },
            ])
          }
        />
      </View>
    );
  }

  return <Button label={`휴대폰 두뇌 내려받기 (약 ${(LOCAL_MODEL.approxMB / 1000).toFixed(1)}GB)`} onPress={start} disabled={ready === null} />;
}
