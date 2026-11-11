import { useEffect, useState } from 'react';
import { StyleSheet, Switch, Text, View } from 'react-native';

import { Body, Button, Card, Title } from './ui';
import { connectedEmails } from '../lib/google';
import * as recordings from '../lib/recordings';
import { useTheme } from '../lib/theme';

function fmtBytes(n: number): string {
  if (n <= 0) return '0 MB';
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function RecordingBackupCard() {
  const t = useTheme();
  const [email, setEmail] = useState<string | null>(null);
  const [folder, setFolder] = useState('');
  const [deleteAfter, setDeleteAfter] = useState(true);
  const [enabled, setEnabled] = useState(false);
  const [lastRun, setLastRun] = useState('');
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<recordings.Progress | null>(null);
  const [result, setResult] = useState<recordings.BackupResult | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let alive = true;
    (async () => {
      const [emails, cfg, label, last] = await Promise.all([
        connectedEmails(),
        recordings.getConfig(),
        recordings.folderLabel(),
        recordings.getLastRun(),
      ]);
      if (!alive) return;
      setEmail(emails[0] ?? null);
      setDeleteAfter(cfg.deleteAfter);
      setEnabled(cfg.enabled);
      setFolder(label);
      setLastRun(last);
    })();
    return () => {
      alive = false;
    };
  }, []);

  const pick = async () => {
    setError('');
    try {
      const uri = await recordings.pickFolder();
      if (uri) setFolder(await recordings.folderLabel());
    } catch {
      setError('폴더를 선택하지 못했어요. 다시 시도해 주세요.');
    }
  };

  const toggleDelete = async (v: boolean) => {
    setDeleteAfter(v);
    await recordings.setDeleteAfter(v);
  };
  const toggleEnabled = async (v: boolean) => {
    setEnabled(v);
    await recordings.setEnabled(v);
  };

  const backup = async () => {
    setBusy(true);
    setError('');
    setResult(null);
    setProgress(null);
    try {
      const r = await recordings.backupNow(setProgress);
      setResult(r);
      setLastRun(await recordings.getLastRun());
    } catch (e) {
      setError(e instanceof Error ? e.message : '백업에 실패했어요.');
    } finally {
      setBusy(false);
      setProgress(null);
    }
  };

  return (
    <Card>
      <Title>📞 통화 녹음 백업</Title>
      <Body muted>
        통화 녹음을 구글 드라이브(&quot;{'도로시 통화녹음'}&quot; 폴더)로 올려 휴대폰 용량을 확보해요. 녹음 내용은 Leo의 드라이브에만 저장돼요.
      </Body>

      <Body>
        구글: <Text style={{ color: email ? t.accent : t.danger, fontWeight: '600' }}>{email ? `연결됨 (${email})` : '연결 필요'}</Text>
      </Body>
      {!email && <Body muted>위 &quot;구글 연결&quot;에서 먼저 연결해 주세요. (드라이브 권한을 위해 이미 연결돼 있어도 한 번 다시 연결이 필요할 수 있어요.)</Body>}

      <Body>
        녹음 폴더: <Text style={{ color: folder ? t.accent : t.subtext, fontWeight: '600' }}>{folder || '아직 선택 안 함'}</Text>
      </Body>
      <Button label={folder ? '녹음 폴더 다시 선택' : '녹음 폴더 선택'} variant="ghost" onPress={pick} />
      {!folder && <Body muted>삼성 기준 보통 &quot;Recordings → Call&quot; 폴더예요. 선택 화면에서 그 폴더를 열고 &quot;이 폴더 사용&quot;을 누르세요.</Body>}

      <View style={styles.row}>
        <Body>올린 뒤 폰에서 삭제 (용량 확보)</Body>
        <Switch value={deleteAfter} onValueChange={toggleDelete} trackColor={{ true: t.accent }} />
      </View>
      <View style={styles.row}>
        <Body>앱 열 때 자동 백업</Body>
        <Switch value={enabled} onValueChange={toggleEnabled} trackColor={{ true: t.accent }} />
      </View>

      {busy && progress && (
        <Body muted>올리는 중… ({progress.index}/{progress.total}) {progress.name}</Body>
      )}
      <Button label={busy ? '백업 중…' : '지금 백업'} onPress={backup} disabled={busy || !folder || !email} />

      {result && (
        <Body>
          ✅ 올림 {result.uploaded}개
          {result.deleted ? ` · 폰에서 삭제 ${result.deleted}개 (${fmtBytes(result.freedBytes)} 확보)` : ''}
          {result.skipped ? ` · 건너뜀 ${result.skipped}` : ''}
          {result.failed ? ` · 실패 ${result.failed}` : ''}
          {result.total === 0 ? ' — 새 녹음이 없어요.' : ''}
        </Body>
      )}
      {!!error && <Text style={{ color: t.danger, fontSize: 15, lineHeight: 22 }}>{error}</Text>}
      {!!lastRun && <Body muted>마지막 백업: {new Date(lastRun).toLocaleString('ko-KR')}</Body>}
    </Card>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 10 },
});
