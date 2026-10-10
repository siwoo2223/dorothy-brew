import * as AuthSession from 'expo-auth-session';
import { useEffect, useState } from 'react';
import { Alert, Platform } from 'react-native';

import { completeSignIn, connectedEmail, disconnect, discovery, googleClientId, googleRedirectUri, SCOPES } from '../lib/google';
import { Body, Button, Card, Title } from './ui';

export function GoogleCard() {
  const clientId = googleClientId();
  const [email, setEmail] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [request, , promptAsync] = AuthSession.useAuthRequest(
    {
      clientId,
      redirectUri: googleRedirectUri(),
      scopes: SCOPES,
      usePKCE: true,
      // offline + consent makes Google return a refresh token so the connection survives restarts.
      extraParams: { access_type: 'offline', prompt: 'consent' },
    },
    discovery,
  );

  useEffect(() => {
    connectedEmail().then(setEmail);
  }, []);

  const connect = async () => {
    if (!request) return;
    const result = await promptAsync();
    if (result.type === 'error') {
      Alert.alert('Google 연결 실패', result.error?.message ?? '다시 시도해 주세요.');
      return;
    }
    if (result.type !== 'success') return;
    setBusy(true);
    try {
      const mail = await completeSignIn(request, result.params.code);
      setEmail(mail || '연결됨');
      Alert.alert('연결됐어요', '이제 도로시가 캘린더와 메일을 함께 챙겨 드려요.');
    } catch (e) {
      Alert.alert('Google 연결 실패', e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const unavailable = Platform.OS === 'web' || !clientId;

  return (
    <Card>
      <Title>Google 캘린더 · Gmail</Title>
      {email ? (
        <>
          <Body>✅ {email}</Body>
          <Body muted>도로시가 일정을 확인·등록하고, 메일을 찾아 읽고, 답장 초안을 임시보관함에 써 둘 수 있어요. 메일을 직접 보내지는 않아요.</Body>
          <Button
            label="연결 해제"
            variant="danger"
            onPress={async () => {
              await disconnect();
              setEmail(null);
            }}
          />
        </>
      ) : (
        <>
          <Body muted>
            {unavailable
              ? 'Google 연결은 휴대폰용 개발 빌드에서 쓸 수 있어요. README의 "Google 연결 설정"을 따라 Client ID를 넣고 다시 빌드해 주세요.'
              : '연결하면 오늘 일정과 안 읽은 중요 메일이 브리핑에 들어가고, "내일 3시 미팅 잡아줘", "김 대표 메일 요약해줘" 같은 부탁을 할 수 있어요.'}
          </Body>
          <Button label={busy ? '연결 중…' : 'Google 계정 연결'} onPress={connect} disabled={unavailable || !request || busy} />
        </>
      )}
    </Card>
  );
}
