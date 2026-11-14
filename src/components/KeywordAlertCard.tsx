import { useEffect, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { Body, Button, Card, Input, Title } from './ui';
import { getWatchKeywords, keywordAlertSupported, notificationAccessGranted, setWatchKeywords } from '../lib/inbox';
import { useTheme } from '../lib/theme';

const PRESETS = ['페소', '레이트', '삽니다', '구합니다', 'PHP', '환전'];

/**
 * Real-time keyword alerts. When a messenger message (which Dorothy already reads for the inbox)
 * contains a watched word, the phone gets an instant notification — even with the app closed. Great
 * for catching exchange-rate offers, orders, etc. the moment they come in.
 */
export function KeywordAlertCard() {
  const t = useTheme();
  const [words, setWords] = useState<string[]>([]);
  const [input, setInput] = useState('');
  const [granted, setGranted] = useState(true);

  useEffect(() => {
    const id = setTimeout(() => {
      setWords(getWatchKeywords());
      setGranted(notificationAccessGranted());
    }, 0);
    return () => clearTimeout(id);
  }, []);

  if (!keywordAlertSupported()) return null;

  const save = (next: string[]) => {
    const clean = Array.from(new Set(next.map((w) => w.trim()).filter(Boolean)));
    setWords(clean);
    setWatchKeywords(clean);
  };
  const add = (w: string) => { if (w.trim() && !words.includes(w.trim())) save([...words, w.trim()]); setInput(''); };
  const remove = (w: string) => save(words.filter((x) => x !== w));

  return (
    <Card>
      <Title>🔔 키워드 알림 (실시간)</Title>
      <Body muted>
        등록한 단어가 카톡·메신저 메시지에 뜨면 즉시 폰 알림으로 알려줘요(앱이 꺼져 있어도). 환전 레이트·사자/팔자 글 같은 걸 놓치지 않게요. 메시지 내용은 저장하지 않아요.
      </Body>
      {!granted && <Body muted>먼저 위 &quot;메시지 알림 접근&quot;을 켜 주세요. 그래야 메시지를 보고 알림을 보낼 수 있어요.</Body>}

      <View style={styles.wrap}>
        {words.length === 0 ? (
          <Body muted>아직 등록된 키워드가 없어요. 아래에서 추가해 보세요.</Body>
        ) : (
          words.map((w) => (
            <Pressable key={w} onPress={() => remove(w)} style={[styles.chip, { backgroundColor: t.accentSoft, borderColor: t.accent }]}>
              <Text style={{ color: t.accent, fontWeight: '700' }}>{w}</Text>
              <Text style={{ color: t.accent, fontWeight: '700' }}> ✕</Text>
            </Pressable>
          ))
        )}
      </View>

      <Input value={input} onChangeText={setInput} placeholder="키워드 입력 후 추가" autoCapitalize="none" onSubmitEditing={() => add(input)} />
      <Button label="키워드 추가" variant="ghost" onPress={() => add(input)} disabled={!input.trim()} />

      <Body muted>자주 쓰는 것:</Body>
      <View style={styles.wrap}>
        {PRESETS.filter((p) => !words.includes(p)).map((p) => (
          <Pressable key={p} onPress={() => add(p)} style={[styles.chip, { backgroundColor: t.card, borderColor: t.border }]}>
            <Text style={{ color: t.text }}>+ {p}</Text>
          </Pressable>
        ))}
      </View>
    </Card>
  );
}

const styles = StyleSheet.create({
  wrap: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: { flexDirection: 'row', alignItems: 'center', borderWidth: 1, borderRadius: 999, paddingHorizontal: 12, paddingVertical: 7 },
});
