import { useEffect, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { Body, Button, Card, Input, Title } from './ui';
import { connectedEmails } from '../lib/google';
import * as sheets from '../lib/sheets';
import { useTheme } from '../lib/theme';

export function SheetsCard() {
  const t = useTheme();
  const [email, setEmail] = useState<string | null>(null);
  const [list, setList] = useState<sheets.SavedSheet[]>([]);
  const [link, setLink] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let alive = true;
    (async () => {
      const [emails, saved] = await Promise.all([connectedEmails(), sheets.listSheets()]);
      if (!alive) return;
      setEmail(emails[0] ?? null);
      setList(saved);
    })();
    return () => {
      alive = false;
    };
  }, []);

  const add = async () => {
    if (!link.trim()) return;
    setBusy(true);
    setError('');
    try {
      await sheets.addSheet(link.trim());
      setList(await sheets.listSheets());
      setLink('');
    } catch (e) {
      setError(e instanceof Error ? e.message : '시트를 추가하지 못했어요.');
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: string) => {
    await sheets.removeSheet(id);
    setList(await sheets.listSheets());
  };

  return (
    <Card>
      <Title>📊 구글 시트</Title>
      <Body muted>
        시트 링크를 등록하면 도로시가 그 시트를 읽어 입고·컨테이너·지출 같은 표를 확인해 줘요(읽기 전용). 예: &quot;2026 리스트에서 이번 주 입고 보여줘&quot;.
      </Body>

      {!email && <Body muted>먼저 위 &quot;구글 연결&quot;에서 연결해 주세요. (시트 권한을 위해 이미 연결돼 있어도 한 번 다시 연결이 필요할 수 있어요.)</Body>}

      {list.length > 0 && (
        <View style={styles.list}>
          {list.map((s) => (
            <View key={s.id} style={styles.row}>
              <Body>• {s.title}</Body>
              <Text onPress={() => remove(s.id)} style={{ color: t.danger, fontWeight: '600' }}>삭제</Text>
            </View>
          ))}
        </View>
      )}

      <Input value={link} onChangeText={setLink} placeholder="구글 시트 링크 붙여넣기 (https://docs.google.com/spreadsheets/…)" autoCapitalize="none" autoCorrect={false} />
      <Button label={busy ? '추가 중…' : '시트 추가'} onPress={add} disabled={busy || !link.trim() || !email} />
      {!!error && <Text style={{ color: t.danger, fontSize: 15, lineHeight: 22 }}>{error}</Text>}
    </Card>
  );
}

const styles = StyleSheet.create({
  list: { gap: 4 },
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 10 },
});
