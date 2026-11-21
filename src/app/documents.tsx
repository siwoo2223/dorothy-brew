import { useMemo, useState } from 'react';
import { Alert, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { Body, Card, Title } from '../components/ui';
import { RichMessage } from '../components/RichMessage';
import { createAndShareFile } from '../lib/documents';
import { useStore } from '../lib/store';
import { useTheme } from '../lib/theme';
import type { SavedDoc } from '../lib/types';

const KIND_LABEL: Record<SavedDoc['kind'], string> = { quote: '견적서', invoice: '인보이스', report: '보고서', other: '문서' };
const FILTERS: { key: 'all' | SavedDoc['kind']; label: string }[] = [
  { key: 'all', label: '전체' },
  { key: 'quote', label: '견적서' },
  { key: 'invoice', label: '인보이스' },
  { key: 'report', label: '보고서' },
];

/** 도로시가 만들어 보관한 견적서·인보이스를 모아 보고, 다시 보내거나 지운다. */
export default function Documents() {
  const t = useTheme();
  const { state, actions } = useStore();
  const [filter, setFilter] = useState<'all' | SavedDoc['kind']>('all');
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const docs = useMemo(
    () => (state.documents ?? []).filter((d) => filter === 'all' || d.kind === filter),
    [state.documents, filter],
  );

  const reshare = async (d: SavedDoc) => {
    setBusy(d.id);
    try {
      await createAndShareFile({ format: d.format, title: d.title, content: d.content, headers: d.headers, rows: d.rows });
    } catch (e) {
      Alert.alert('다시 보내기 실패', e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const remove = (d: SavedDoc) =>
    Alert.alert('문서 삭제', `"${d.title}"을(를) 보관함에서 지울까요?`, [
      { text: '취소' },
      { text: '삭제', style: 'destructive', onPress: () => actions.deleteDocument(d.id) },
    ]);

  return (
    <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
      <View style={styles.filters}>
        {FILTERS.map((f) => {
          const active = filter === f.key;
          return (
            <Pressable
              key={f.key}
              onPress={() => setFilter(f.key)}
              style={[styles.chip, { borderColor: active ? t.accent : t.border, backgroundColor: active ? t.accent : 'transparent' }]}
            >
              <Text style={{ color: active ? t.accentText : t.text, fontSize: 13 }}>{f.label}</Text>
            </Pressable>
          );
        })}
      </View>

      {docs.length === 0 ? (
        <Card>
          <Title>아직 보관된 문서가 없어요</Title>
          <Body muted>
            도로시에게 &quot;○○ 견적서/인보이스 만들어 줘&quot;라고 하면 여기에 자동으로 쌓여요. 나중에 &quot;지난 ○○ 견적 보여줘&quot;라고 하면 바로 찾아 드려요.
          </Body>
        </Card>
      ) : (
        docs.map((d) => (
          <Card key={d.id}>
            <View style={styles.row}>
              <View style={[styles.badge, { backgroundColor: d.kind === 'invoice' ? '#E5698C' : t.accent }]}>
                <Text style={{ color: '#fff', fontSize: 11, fontWeight: '800' }}>{KIND_LABEL[d.kind]}</Text>
              </View>
              <Text style={{ color: t.subtext, fontSize: 12 }}>{d.format.toUpperCase()} · {d.createdAt.slice(0, 10)}</Text>
            </View>
            <Title>{d.title}</Title>
            {d.customer ? <Body muted>거래처: {d.customer}</Body> : null}

            <View style={styles.actions}>
              <Pressable onPress={() => reshare(d)} disabled={busy === d.id} style={[styles.btn, { backgroundColor: t.accent, opacity: busy === d.id ? 0.5 : 1 }]}>
                <Text style={{ color: t.accentText, fontWeight: '700', fontSize: 13 }}>{busy === d.id ? '여는 중…' : '📤 다시 보내기'}</Text>
              </Pressable>
              <Pressable onPress={() => setOpen(open === d.id ? null : d.id)} style={[styles.btn, { backgroundColor: t.card, borderWidth: StyleSheet.hairlineWidth, borderColor: t.border }]}>
                <Text style={{ color: t.text, fontWeight: '700', fontSize: 13 }}>{open === d.id ? '내용 접기' : '👁 내용 보기'}</Text>
              </Pressable>
              <Pressable onPress={() => remove(d)} hitSlop={6} style={[styles.btn, { backgroundColor: 'transparent' }]}>
                <Text style={{ color: t.danger, fontWeight: '700', fontSize: 13 }}>🗑</Text>
              </Pressable>
            </View>

            {open === d.id ? (
              <View style={{ marginTop: 10, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.border, paddingTop: 10 }}>
                {d.format === 'excel' ? (
                  <Body muted>엑셀 표: {(d.headers ?? []).join(' · ')} · {(d.rows ?? []).length}행</Body>
                ) : (
                  <RichMessage text={d.content ?? '(내용 없음)'} color={t.text} />
                )}
              </View>
            ) : null}
          </Card>
        ))
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 16, gap: 12 },
  filters: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: { borderWidth: 1, borderRadius: 16, paddingHorizontal: 14, paddingVertical: 7 },
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  badge: { borderRadius: 7, paddingHorizontal: 8, paddingVertical: 3 },
  actions: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 10 },
  btn: { borderRadius: 12, paddingHorizontal: 12, paddingVertical: 9 },
});
