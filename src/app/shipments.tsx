import { useFocusEffect, useLocalSearchParams, useNavigation } from 'expo-router';
import { useCallback, useLayoutEffect, useState } from 'react';
import { ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';

import { Body } from '../components/ui';
import { Shipment, shipmentsPage } from '../lib/homeSummary';
import { useTheme } from '../lib/theme';

/**
 * A clean, self-updating list for one shipment group (a status, a 7-day-stuck group, unpaid, or a
 * voyage code). Opened from the 회사 탭's 조회 buttons instead of re-asking Dorothy in the chat.
 * Pull down to refresh; "더 보기" loads the next page.
 */
export default function Shipments() {
  const t = useTheme();
  const navigation = useNavigation();
  const params = useLocalSearchParams<{ status?: string; code?: string; filter?: string; title?: string }>();
  const filter = params.filter === 'stuck' || params.filter === 'unpaid' ? params.filter : undefined;
  const title = params.title || '화물 목록';
  useLayoutEffect(() => {
    navigation.setOptions({ title });
  }, [navigation, title]);

  const [rows, setRows] = useState<Shipment[]>([]);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [total, setTotal] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');

  const query = useCallback(
    async (p: number) => {
      return shipmentsPage({ status: params.status, code: params.code, filter, page: p });
    },
    [params.status, params.code, filter],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const r = await query(1);
      setRows(r.shipments);
      setHasMore(r.hasMore);
      setTotal(r.total);
      setPage(1);
    } catch {
      setError('목록을 불러오지 못했어요. 당겨서 다시 시도해 주세요.');
    } finally {
      setLoading(false);
    }
  }, [query]);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load]),
  );

  const more = async () => {
    if (loadingMore || !hasMore) return;
    setLoadingMore(true);
    try {
      const next = page + 1;
      const r = await query(next);
      setRows((prev) => [...prev, ...r.shipments]);
      setHasMore(r.hasMore);
      setPage(next);
    } catch {
      /* keep what we have */
    } finally {
      setLoadingMore(false);
    }
  };

  return (
      <ScrollView
        contentContainerStyle={styles.container}
        refreshControl={<RefreshControl refreshing={loading} onRefresh={load} tintColor={t.accent} />}
      >
        {!loading && (
          <Text style={{ color: t.subtext, fontSize: 13 }}>
            {filter === 'stuck' ? '7일 이상 같은 상태로 멈춘 화물' : filter === 'unpaid' ? '배송완료인데 입금 기록이 0인 화물' : '화물 목록'}
            {' · '}불러온 {rows.length}건{total !== null ? ` / 전체 ${total}` : ''}
          </Text>
        )}

        {loading && rows.length === 0 ? (
          <View style={{ paddingTop: 40 }}><ActivityIndicator color={t.accent} /></View>
        ) : error ? (
          <Body muted>{error}</Body>
        ) : rows.length === 0 ? (
          <Body muted>해당하는 화물이 없어요.</Body>
        ) : (
          rows.map((s, i) => (
            <View key={`${s.trackingNo}-${i}`} style={[styles.row, { backgroundColor: t.card, borderColor: t.border }]}>
              <View style={styles.rowTop}>
                <Text style={{ color: t.text, fontSize: 15, fontWeight: '800' }}>{s.trackingNo || '—'}</Text>
                <View style={[styles.badge, { backgroundColor: t.accentSoft }]}>
                  <Text style={{ color: t.accent, fontSize: 12, fontWeight: '700' }}>{s.status}{s.daysInStatus !== null ? ` · ${s.daysInStatus}일째` : ''}</Text>
                </View>
              </View>
              <Text style={{ color: t.text, fontSize: 14 }}>{s.customer || '고객 미상'}{s.voyageCode ? ` · ${s.voyageCode}` : ''}</Text>
              {!!s.items && <Text style={{ color: t.subtext, fontSize: 13 }} numberOfLines={2}>{s.items}</Text>}
              <Text style={{ color: t.subtext, fontSize: 12 }}>
                박스 {s.boxes} · {s.cbm} CBM · {s.weightKg} kg{s.unpaid ? ' · 💰 입금 0' : ''}
              </Text>
            </View>
          ))
        )}

        {hasMore && !loading && (
          <Pressable onPress={more} disabled={loadingMore} style={[styles.moreBtn, { borderColor: t.border }]}>
            {loadingMore ? <ActivityIndicator color={t.accent} /> : <Text style={{ color: t.accent, fontWeight: '700' }}>더 보기</Text>}
          </Pressable>
        )}
        {filter && !loading && rows.length > 0 && (
          <Body muted>※ 다음 페이지를 더 불러오면 조건에 맞는 화물이 더 나올 수 있어요.</Body>
        )}
      </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 16, gap: 10 },
  row: { borderRadius: 14, borderWidth: StyleSheet.hairlineWidth, padding: 14, gap: 4 },
  rowTop: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8 },
  badge: { borderRadius: 12, paddingHorizontal: 10, paddingVertical: 4 },
  moreBtn: { borderWidth: 1, borderRadius: 14, paddingVertical: 12, alignItems: 'center' },
});
