import { useNavigation } from 'expo-router';
import { useLayoutEffect } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { Body } from '../components/ui';
import { useStore } from '../lib/store';
import { useTheme } from '../lib/theme';

const sym = (c: string) => (c === 'KRW' ? '₩' : '₱');
const fmt = (n: number) => Math.round(n).toLocaleString('ko-KR');
const md = (iso?: string) => (iso && iso.length >= 10 ? iso.slice(5, 10).replace('-', '/') : '');

/** 개인 지출 기록(영수증 사진 등에서 저장). 이번 달 통화별 합계 + 전체 목록(길게 눌러 삭제). */
export default function ExpensesScreen() {
  const t = useTheme();
  const navigation = useNavigation();
  const { state, actions } = useStore();

  useLayoutEffect(() => { navigation.setOptions({ title: '내 지출 기록' }); }, [navigation]);

  const expenses = [...(state.expenses ?? [])].sort((a, b) => (b.spentAt || b.createdAt).localeCompare(a.spentAt || a.createdAt));
  const ym = new Date().toISOString().slice(0, 7);
  const monthly: Record<string, number> = {};
  for (const e of expenses) {
    const when = (e.spentAt || e.createdAt || '').slice(0, 7);
    if (when === ym) monthly[e.currency] = (monthly[e.currency] ?? 0) + e.amount;
  }
  const monthKeys = Object.keys(monthly);

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <View style={[styles.totalCard, { backgroundColor: t.accentSoft, borderColor: t.accent }]}>
        <Text style={{ color: t.subtext, fontSize: 13 }}>이번 달 ({ym}) 지출 합계</Text>
        {monthKeys.length ? (
          monthKeys.map((c) => (
            <Text key={c} style={{ color: t.accent, fontSize: 24, fontWeight: '800' }}>{sym(c)}{fmt(monthly[c])}</Text>
          ))
        ) : (
          <Text style={{ color: t.accent, fontSize: 20, fontWeight: '800' }}>아직 없어요</Text>
        )}
        <Text style={{ color: t.subtext, fontSize: 11 }}>영수증 사진을 도로시에게 주면 자동으로 기록돼요(개인 지출 기준, 회사 회계와 별개).</Text>
      </View>

      {expenses.length === 0 ? (
        <Body muted>지출 기록이 없어요. 채팅에서 &apos;🧾 영수증&apos; 버튼으로 영수증 사진을 보내보세요.</Body>
      ) : (
        <View style={[styles.listCard, { backgroundColor: t.card, borderColor: t.border }]}>
          {expenses.map((e, i) => (
            <Pressable
              key={e.id}
              onLongPress={() => actions.deleteExpense(e.id)}
              style={[styles.row, i > 0 && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.border }]}
            >
              <Text style={{ color: t.subtext, fontSize: 12, width: 42 }}>{md(e.spentAt || e.createdAt)}</Text>
              <View style={{ flex: 1 }}>
                <Text style={{ color: t.text, fontSize: 14 }} numberOfLines={1}>{e.memo || e.vendor || e.category || '지출'}</Text>
                {(e.category || e.vendor) && (
                  <Text style={{ color: t.subtext, fontSize: 11 }} numberOfLines={1}>{[e.category, e.vendor].filter(Boolean).join(' · ')}</Text>
                )}
              </View>
              <Text style={{ color: t.danger, fontSize: 14, fontWeight: '700' }}>−{sym(e.currency)}{fmt(e.amount)}</Text>
            </Pressable>
          ))}
        </View>
      )}
      <Body muted>항목을 길게 누르면 삭제돼요.</Body>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 16, gap: 12 },
  totalCard: { borderRadius: 18, borderWidth: 1, padding: 16, gap: 2, alignItems: 'flex-start' },
  listCard: { borderRadius: 16, borderWidth: StyleSheet.hairlineWidth, overflow: 'hidden' },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 14, paddingVertical: 11 },
});
