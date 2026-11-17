import { useFocusEffect, useLocalSearchParams, useNavigation } from 'expo-router';
import { useCallback, useLayoutEffect, useState } from 'react';
import { ActivityIndicator, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';

import { Body } from '../components/ui';
import { AccountDetail, companyAccount } from '../lib/homeSummary';
import { useTheme } from '../lib/theme';

const sym = (cur: string) => (cur === 'PHP' ? '₱' : cur === 'KRW' ? '₩' : cur + ' ');
const fmt = (n: number) => Math.round(n).toLocaleString('ko-KR');
const md = (iso: string) => (iso && iso.length >= 10 ? iso.slice(5, 10).replace('-', '/') : iso);

/** One account's current-month transactions (dorothy_finance.php?account=id). Refreshes on open. */
export default function AccountScreen() {
  const t = useTheme();
  const navigation = useNavigation();
  const p = useLocalSearchParams<{ id?: string; name?: string; currency?: string }>();
  const id = Number(p.id || 0);
  const [detail, setDetail] = useState<AccountDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useLayoutEffect(() => {
    navigation.setOptions({ title: p.name || '계좌 상세' });
  }, [navigation, p.name]);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const d = await companyAccount(id);
      setDetail(d);
      if (!d) setError('계좌 내역을 불러오지 못했어요. 당겨서 다시 시도해 주세요.');
    } catch {
      setError('계좌 내역을 불러오지 못했어요. 당겨서 다시 시도해 주세요.');
    } finally {
      setLoading(false);
    }
  }, [id]);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  const cur = detail?.currency || p.currency || 'PHP';

  return (
    <ScrollView
      contentContainerStyle={styles.container}
      refreshControl={<RefreshControl refreshing={loading} onRefresh={load} tintColor={t.accent} />}
    >
      <View style={[styles.head, { backgroundColor: t.accentSoft, borderColor: t.accent }]}>
        <Text style={{ color: t.subtext, fontSize: 13 }}>{p.name || detail?.name} · 현재 잔액</Text>
        <Text style={{ color: t.accent, fontSize: 24, fontWeight: '800' }}>
          {detail ? `${sym(cur)}${fmt(detail.balance)}` : '—'}
        </Text>
        {detail && <Text style={{ color: t.subtext, fontSize: 12 }}>최근 거래 {detail.transactions.length}건</Text>}
      </View>

      {loading && !detail ? (
        <View style={{ paddingTop: 30 }}><ActivityIndicator color={t.accent} /></View>
      ) : error ? (
        <Body muted>{error}</Body>
      ) : detail && detail.transactions.length > 0 ? (
        <View style={[styles.listCard, { backgroundColor: t.card, borderColor: t.border }]}>
          {detail.transactions.map((x, i) => {
            const inflow = x.direction === 'in';
            return (
              <View key={i} style={[styles.row, i > 0 && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.border }]}>
                <Text style={{ color: t.subtext, fontSize: 12, width: 42 }}>{md(x.date)}</Text>
                <View style={{ flex: 1 }}>
                  <Text style={{ color: t.text, fontSize: 14 }} numberOfLines={1}>{x.memo || x.counterparty || x.category || (inflow ? '입금' : '출금')}</Text>
                  {(x.category || x.counterparty) && (
                    <Text style={{ color: t.subtext, fontSize: 11 }} numberOfLines={1}>
                      {[x.category, x.counterparty].filter(Boolean).join(' · ')}
                    </Text>
                  )}
                </View>
                <Text style={{ color: inflow ? t.accent : t.danger, fontSize: 14, fontWeight: '700' }}>
                  {inflow ? '+' : '−'}{sym(cur)}{fmt(x.amount)}
                </Text>
              </View>
            );
          })}
        </View>
      ) : (
        <Body muted>거래 내역이 없어요.</Body>
      )}
      <Body muted>최근 거래를 최신순으로 보여드려요(최대 100건). 당겨서 새로고침할 수 있어요.</Body>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 16, gap: 12 },
  head: { borderRadius: 18, borderWidth: 1, padding: 16, gap: 2 },
  listCard: { borderRadius: 16, borderWidth: StyleSheet.hairlineWidth, overflow: 'hidden' },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 14, paddingVertical: 11 },
});
