import { useCallback, useEffect, useState } from 'react';
import { Linking, StyleSheet, Text, View } from 'react-native';

import { Body, Button, Card, Title } from './ui';
import { useTheme } from '../lib/theme';
import { CURRENT_BUILD, RELEASES_PAGE, UpdateCheck, checkForUpdate, downloadAndInstall } from '../lib/update';

export function UpdateCard() {
  const t = useTheme();
  const [checking, setChecking] = useState(false);
  const [result, setResult] = useState<UpdateCheck | null>(null);
  const [error, setError] = useState('');
  const [installing, setInstalling] = useState(false);
  const [progress, setProgress] = useState(0);

  const check = useCallback(async () => {
    setChecking(true);
    setError('');
    try {
      setResult(await checkForUpdate());
    } catch (e) {
      setError(e instanceof Error ? e.message : '업데이트 확인에 실패했어요.');
    } finally {
      setChecking(false);
    }
  }, []);

  // Check once when the screen opens, quietly (no spinner, no error popup if offline).
  // setState only happens inside the async callback, never synchronously in the effect body.
  useEffect(() => {
    let alive = true;
    checkForUpdate()
      .then((r) => {
        if (alive) setResult(r);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  const install = async () => {
    const url = result?.latest?.url;
    if (!url) return;
    setInstalling(true);
    setError('');
    setProgress(0);
    try {
      await downloadAndInstall(url, setProgress);
      // Android's installer now takes over; if it asks for "unknown apps" permission the first time,
      // the user grants it and taps 업데이트 again.
    } catch (e) {
      setError(e instanceof Error ? e.message : '설치에 실패했어요.');
    } finally {
      setInstalling(false);
    }
  };

  const available = result?.available;
  const latestBuild = result?.latest?.build;

  return (
    <Card style={available ? { borderColor: t.accent } : undefined}>
      <Title>앱 업데이트</Title>
      <Body muted>
        지금 버전: 빌드 {CURRENT_BUILD || '—'}
        {latestBuild ? ` · 최신: 빌드 ${latestBuild}` : ''}
      </Body>

      {available ? (
        <>
          <Body>새 업데이트가 있어요 (빌드 {latestBuild}). 아래 버튼으로 바로 받아서 설치할 수 있어요.</Body>
          {installing && (
            <View style={styles.barTrack}>
              <View style={[styles.barFill, { width: `${Math.round(progress * 100)}%`, backgroundColor: t.accent }]} />
            </View>
          )}
          <Button
            label={installing ? `받는 중… ${Math.round(progress * 100)}%` : `지금 업데이트 (빌드 ${latestBuild})`}
            onPress={install}
            disabled={installing}
          />
          <Body muted>처음 한 번은 휴대폰이 &quot;이 출처 설치 허용&quot;을 물어봐요. 허용한 뒤 다시 눌러 주세요.</Body>
        </>
      ) : (
        <>
          <Body muted>{checking ? '확인하는 중…' : result ? '최신 버전이에요. 👍' : '업데이트를 확인해 보세요.'}</Body>
          <Button label={checking ? '확인 중…' : '업데이트 확인'} onPress={() => check()} disabled={checking} variant="ghost" />
        </>
      )}

      {!!error && (
        <>
          <Text style={{ color: t.danger, fontSize: 15, lineHeight: 22 }}>{error}</Text>
          <Button label="릴리스 페이지에서 직접 받기" variant="ghost" onPress={() => Linking.openURL(RELEASES_PAGE)} />
        </>
      )}
    </Card>
  );
}

const styles = StyleSheet.create({
  barTrack: { height: 10, borderRadius: 6, backgroundColor: 'rgba(127,127,127,0.25)', overflow: 'hidden' },
  barFill: { height: '100%', borderRadius: 6 },
});
