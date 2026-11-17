import { useEffect, useState } from 'react';
import { Alert } from 'react-native';

import { Body, Button, Card, Title } from './ui';
import { backupReady, backupToDrive, latestBackup, restoreFromDrive } from '../lib/backup';
import { useStore } from '../lib/store';

/**
 * 구글 드라이브 백업·복구: 기억·할 일·설정을 '도로시 백업' 폴더에 올리고, 폰을 바꿔도 되살린다.
 * 비밀(API 키·회사 토큰)과 대화기록은 백업에 넣지 않는다. 구글이 연결돼 있어야 보인다.
 */
export function BackupCard() {
  const { getState, actions } = useStore();
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [last, setLast] = useState<string | null>(null);

  useEffect(() => {
    backupReady().then(setReady).catch(() => setReady(false));
    latestBackup().then((b) => setLast(b?.name ?? null)).catch(() => {});
  }, []);

  if (!ready) {
    return (
      <Card>
        <Title>☁️ 백업 (구글 드라이브)</Title>
        <Body muted>구글을 연결하면 기억·할 일·설정을 드라이브에 자동 백업하고, 폰을 바꿔도 되살릴 수 있어요. 위 &quot;구글 연결&quot;을 먼저 해주세요.</Body>
      </Card>
    );
  }

  const doBackup = async () => {
    setBusy(true);
    try {
      const r = await backupToDrive(getState());
      setLast(r.name);
      Alert.alert('백업 완료', `드라이브 '도로시 백업' 폴더에 저장했어요.\n${r.name}`);
    } catch (e) {
      Alert.alert('백업 실패', e instanceof Error ? e.message : '다시 시도해 주세요.');
    } finally {
      setBusy(false);
    }
  };

  const doRestore = async () => {
    Alert.alert('복구', '가장 최근 백업을 불러와 기억·할 일·설정을 되살려요. 지금 것과 합쳐지고(같은 항목은 유지), 비밀번호/API 키는 복구되지 않아요. 진행할까요?', [
      { text: '취소' },
      {
        text: '복구',
        onPress: async () => {
          setBusy(true);
          try {
            const b = await restoreFromDrive();
            if (!b) { Alert.alert('백업 없음', '드라이브에 복구할 백업이 없어요. 먼저 백업을 만들어 주세요.'); return; }
            const res = await actions.applyBackup(b);
            Alert.alert('복구 완료', `기억 ${res.memories}개, 할 일 ${res.tasks}개, 회신 ${res.followups}개를 되살렸어요(이미 있던 건 유지).`);
          } catch (e) {
            Alert.alert('복구 실패', e instanceof Error ? e.message : '다시 시도해 주세요.');
          } finally {
            setBusy(false);
          }
        },
      },
    ]);
  };

  return (
    <Card>
      <Title>☁️ 백업 (구글 드라이브)</Title>
      <Body muted>
        기억·할 일·회신·설정을 드라이브 &apos;도로시 백업&apos;에 저장해요. 폰을 잃어버리거나 바꿔도 복구할 수 있어요. (API 키·회사 토큰·대화기록은 보안상 백업에 넣지 않아요.)
      </Body>
      {last && <Body muted>최근 백업: {last}</Body>}
      <Button label={busy ? '처리 중…' : '지금 백업하기'} onPress={doBackup} disabled={busy} />
      <Button label="최근 백업에서 복구" variant="ghost" onPress={doRestore} disabled={busy} />
    </Card>
  );
}
