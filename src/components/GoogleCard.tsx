import * as AuthSession from 'expo-auth-session';
import { useEffect, useState } from 'react';
import { Alert, Platform, View } from 'react-native';

import { completeSignIn, connectedEmails, disconnect, discovery, googleClientId, googleRedirectUri, SCOPES } from '../lib/google';
import { Body, Button, Card, Title } from './ui';

export function GoogleCard() {
  const clientId = googleClientId();
  const [emails, setEmails] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  const [request, , promptAsync] = AuthSession.useAuthRequest(
    {
      clientId,
      redirectUri: googleRedirectUri(),
      scopes: SCOPES,
      usePKCE: true,
      // select_account shows the chooser so a different account can be added; consent + offline
      // (below) make Google return a refresh token so each connection survives restarts.
      extraParams: { access_type: 'offline', prompt: 'select_account consent' },
    },
    discovery,
  );

  useEffect(() => {
    connectedEmails().then(setEmails);
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
      setEmails(await connectedEmails());
      Alert.alert('연결됐어요', `${mail} 계정을 연결했어요. 다른 계정도 "계정 추가"로 더 연결할 수 있어요.`);
    } catch (e) {
      Alert.alert('Google 연결 실패', e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = (email: string) =>
    Alert.alert('연결 해제', `${email} 연결을 해제할까요?`, [
      { text: '취소' },
      {
        text: '해제',
        style: 'destructive',
        onPress: async () => {
          await disconnect(email);
          setEmails(await connectedEmails());
        },
      },
    ]);

  const unavailable = Platform.OS === 'web' || !clientId;

  return (
    <Card>
      <Title>Google 캘린더 · Gmail</Title>
      {emails.length > 0 ? (
        <>
          <Body muted>도로시가 연결된 모든 계정의 일정을 확인·등록하고, 메일을 찾아 읽고, 답장 초안을 임시보관함에 써 둬요. 메일을 직접 보내지는 않아요.</Body>
          {emails.map((email) => (
            <View key={email} style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
              <Body>✅ {email}</Body>
              <View style={{ flex: 1 }} />
              <Button label="해제" variant="ghost" onPress={() => remove(email)} />
            </View>
          ))}
          <Button label={busy ? '연결 중…' : '＋ 계정 추가'} onPress={connect} disabled={unavailable || !request || busy} />
        </>
      ) : (
        <>
          <Body muted>
            {unavailable
              ? 'Google 연결은 휴대폰용 개발 빌드에서 쓸 수 있어요. README의 "Google 연결 설정"을 따라 Client ID를 넣고 다시 빌드해 주세요.'
              : '연결하면 오늘 일정과 안 읽은 중요 메일이 브리핑에 들어가고, "내일 3시 미팅 잡아줘", "김 대표 메일 요약해줘" 같은 부탁을 할 수 있어요. 여러 계정을 모두 연결할 수 있어요.'}
          </Body>
          <Button label={busy ? '연결 중…' : 'Google 계정 연결'} onPress={connect} disabled={unavailable || !request || busy} />
        </>
      )}
      <Body muted>업무·개인 계정을 각각 추가하려면 &quot;계정 추가&quot;를 누르고 매번 다른 계정을 골라 주세요.</Body>
    </Card>
  );
}
