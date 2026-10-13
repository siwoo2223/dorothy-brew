import { useState } from 'react';
import { Alert } from 'react-native';

import { fetchCompanyReport } from '../lib/company';
import { useStore } from '../lib/store';
import { Body, Button, Card, Input, Title } from './ui';

/** Connects Dorothy to the user's own read-only company-status endpoint (URL + secret token). */
export function CompanyCard() {
  const { state, actions } = useStore();
  const [url, setUrl] = useState(state.companyUrl);
  const [tokenInput, setTokenInput] = useState('');
  const [testing, setTesting] = useState(false);

  const connected = !!state.companyUrl && !!state.companyToken;

  const save = () => {
    actions.setCompanyUrl(url);
    if (tokenInput.trim()) actions.setCompanyToken(tokenInput);
    setTokenInput('');
    Alert.alert('저장됨', '회사 현황 주소와 토큰은 이 기기에만 저장돼요.');
  };

  const test = async () => {
    // Persist first so the test uses the freshly typed values.
    actions.setCompanyUrl(url);
    if (tokenInput.trim()) actions.setCompanyToken(tokenInput);
    setTokenInput('');
    setTesting(true);
    try {
      const text = await fetchCompanyReport('report');
      let msg = text.slice(0, 400);
      try {
        const d = JSON.parse(text);
        const s = d.shipments;
        if (s) msg = `연결 성공!\n화물 총 ${s.total}건 · 진행중 ${s.in_progress}건 · 오늘 신규 ${s.today_new}건`;
      } catch {
        // not JSON — show the raw start
      }
      Alert.alert('테스트 결과', msg);
    } catch (e) {
      Alert.alert('연결 실패', e instanceof Error ? e.message : String(e));
    } finally {
      setTesting(false);
    }
  };

  const disconnect = () => {
    Alert.alert('연결 끊기', '주소와 토큰을 지울까요?', [
      { text: '취소' },
      {
        text: '끊기',
        style: 'destructive',
        onPress: () => {
          actions.setCompanyUrl('');
          actions.setCompanyToken('');
          setUrl('');
          setTokenInput('');
        },
      },
    ]);
  };

  return (
    <Card>
      <Title>회사 현황 연결</Title>
      <Body muted>
        내 회사 서버의 현황 요약 주소와 비밀 토큰을 넣으면, 도로시가 화물·배송·고객·매출 같은 회사 현황을 바로 읽어 답해요.
        {connected ? ' 지금 연결돼 있어요.' : ' 아직 연결 안 됨.'}
      </Body>
      <Input
        value={url}
        onChangeText={setUrl}
        placeholder="https://…/backend/api/dorothy_report.php"
        autoCapitalize="none"
        autoCorrect={false}
      />
      <Input
        value={tokenInput}
        onChangeText={setTokenInput}
        placeholder={state.companyToken ? '비밀 토큰 (저장됨 — 바꿀 때만 입력)' : '비밀 토큰'}
        autoCapitalize="none"
        autoCorrect={false}
        secureTextEntry
      />
      <Button label="저장" onPress={save} disabled={!url.trim()} />
      <Button label={testing ? '확인 중…' : '연결 테스트'} variant="ghost" onPress={test} disabled={testing || !url.trim()} />
      {connected && <Button label="연결 끊기" variant="danger" onPress={disconnect} />}
    </Card>
  );
}
