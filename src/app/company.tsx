import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';

import { Body, Card, Title } from '../components/ui';
import { Chart } from '../components/Chart';
import { companyConfigured, loadCompanyConfig } from '../lib/company';
import { companyDashboard, Dashboard } from '../lib/homeSummary';
import { useTheme } from '../lib/theme';

/** One-tap questions that open the chat and ask Dorothy for the detail (she answers with graphs/lists). */
const SHORTCUTS = [
  { icon: '📋', label: '전체 현황 요약', ask: '회사 현황 요약해줘. 통관·배송중·입고 상태별 건수랑 이번 달 수지를 그래프로 보여줘.' },
  { icon: '🐢', label: '오래 멈춘 화물', ask: '오래 멈춰 있는 화물 있어? 급한 순서로 목록으로 알려줘.' },
  { icon: '🚢', label: '이번 주 출항·입항', ask: '이번 주 출항·입항 예정 화물 정리해줘.' },
  { icon: '💰', label: '미수금 현황', ask: '미수금 있는 화물 정리해줘. 고객별 금액이랑 합계 알려줘.' },
  { icon: '📅', label: '이번 달 수지', ask: '이번 달 수입·지출·수지 알려주고 지난달이랑 비교해줘. 그래프로 보여줘.' },
] as const;

export default function Company() {
  const t = useTheme();
  const router = useRouter();
  const [dash, setDash] = useState<Dashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [configured, setConfigured] = useState(true);

  const load = useCallback(async () => {
    await loadCompanyConfig().catch(() => {});
    if (!companyConfigured()) {
      setConfigured(false);
      setLoading(false);
      return;
    }
    setConfigured(true);
    const d = await companyDashboard().catch(() => null);
    setDash(d);
    setLoading(false);
  }, []);

  useFocusEffect(
    useCallback(() => {
      setLoading(true);
      load();
    }, [load]),
  );

  const ask = (text: string) => router.navigate({ pathname: '/chat', params: { ask: text } });

  return (
    <ScrollView
      contentContainerStyle={styles.container}
      refreshControl={<RefreshControl refreshing={loading} onRefresh={load} tintColor={t.accent} />}
    >
      {!configured ? (
        <Card>
          <Title>🏢 회사 현황</Title>
          <Body muted>아직 회사 서버가 연결되지 않았어요. 설정 → 회사·구글에서 주소와 키를 넣어 주세요.</Body>
          <Pressable onPress={() => router.navigate('/settings')} style={[styles.cta, { backgroundColor: t.accent }]}>
            <Text style={{ color: t.accentText, fontWeight: '700' }}>설정으로 가기</Text>
          </Pressable>
        </Card>
      ) : (
        <>
          {/* 상태별 건수 그래프 */}
          {loading && !dash ? (
            <Card><ActivityIndicator color={t.accent} /><Body muted>회사 현황을 불러오는 중…</Body></Card>
          ) : dash && dash.statuses.length > 0 ? (
            <Chart spec={{ type: 'bar', title: '🏢 화물 상태별 건수', data: dash.statuses }} />
          ) : (
            <Card>
              <Title>🏢 화물 상태</Title>
              <Body muted>상태별 건수를 읽지 못했어요. 아래 &quot;전체 현황 요약&quot;으로 도로시에게 물어보면 최신 수치를 알려드려요.</Body>
            </Card>
          )}

          {/* 이번 달 매출·수지 */}
          {dash && (dash.monthSales || dash.monthNet) && (
            <View style={styles.metrics}>
              {dash.monthSales && (
                <View style={[styles.metric, { backgroundColor: t.card, borderColor: t.border }]}>
                  <Text style={{ color: t.subtext, fontSize: 12 }}>이번 달 매출</Text>
                  <Text style={[styles.metricN, { color: t.accent }]} numberOfLines={1}>{dash.monthSales}</Text>
                  {dash.lastMonthSales && <Text style={{ color: t.subtext, fontSize: 11 }}>지난달 {dash.lastMonthSales}</Text>}
                </View>
              )}
              {dash.monthNet && (
                <View style={[styles.metric, { backgroundColor: t.card, borderColor: t.border }]}>
                  <Text style={{ color: t.subtext, fontSize: 12 }}>이번 달 수지</Text>
                  <Text style={[styles.metricN, { color: t.text }]} numberOfLines={1}>{dash.monthNet}</Text>
                  {dash.todayNew !== null && <Text style={{ color: t.subtext, fontSize: 11 }}>오늘 신규 {dash.todayNew}건</Text>}
                </View>
              )}
            </View>
          )}
          {dash && dash.next && (
            <View style={[styles.listCard, { backgroundColor: t.card, borderColor: t.border }]}>
              <Text style={{ color: t.subtext, fontSize: 13, paddingHorizontal: 16, paddingVertical: 12 }}>🚢 다음 출항 · {dash.next}</Text>
            </View>
          )}

          {/* 오늘 챙길 화물 */}
          {dash && dash.attention.length > 0 && (
            <>
              <Text style={[styles.section, { color: t.text }]}>⚠️ 오늘 챙길 화물</Text>
              <View style={[styles.listCard, { backgroundColor: t.card, borderColor: t.border }]}>
                {dash.attention.map((it, i) => (
                  <View key={it.title + i} style={[styles.row, i > 0 && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.border }]}>
                    <View style={{ flex: 1 }}>
                      <Text style={{ color: t.text, fontSize: 15, fontWeight: '600' }} numberOfLines={1}>{it.title}</Text>
                      {!!it.sub && <Text style={{ color: t.subtext, fontSize: 12 }} numberOfLines={1}>{it.sub}</Text>}
                    </View>
                    <Pressable onPress={() => ask(`${it.title} 화물 지금 어디까지 왔어? 자세히 알려줘.`)} hitSlop={6}>
                      <Text style={{ color: t.accent, fontSize: 13, fontWeight: '700' }}>조회 ›</Text>
                    </Pressable>
                  </View>
                ))}
              </View>
            </>
          )}

          {/* 바로 물어보기 */}
          <Text style={[styles.section, { color: t.text }]}>바로 물어보기</Text>
          <View style={styles.shortcuts}>
            {SHORTCUTS.map((s) => (
              <Pressable
                key={s.label}
                onPress={() => ask(s.ask)}
                style={({ pressed }) => [styles.shortcut, { backgroundColor: t.card, borderColor: t.border, opacity: pressed ? 0.8 : 1 }]}
              >
                <Text style={{ fontSize: 22 }}>{s.icon}</Text>
                <Text style={{ color: t.text, fontSize: 13, fontWeight: '700' }} numberOfLines={1}>{s.label}</Text>
              </Pressable>
            ))}
          </View>

          <Body muted>숫자는 회사 서버에서 바로 읽은 값이에요. 당겨서 새로고침할 수 있어요. 더 자세한 건 위 버튼이나 도로시(채팅)에게 물어보세요.</Body>
        </>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 16, gap: 12 },
  cta: { borderRadius: 12, paddingVertical: 12, alignItems: 'center', marginTop: 8 },
  metrics: { flexDirection: 'row', gap: 10 },
  metric: { flex: 1, borderRadius: 16, borderWidth: StyleSheet.hairlineWidth, padding: 14, gap: 4, justifyContent: 'center' },
  metricN: { fontSize: 22, fontWeight: '800', letterSpacing: -0.5 },
  section: { fontSize: 15, fontWeight: '800', letterSpacing: -0.2, marginTop: 4, marginBottom: -4 },
  listCard: { borderRadius: 16, borderWidth: StyleSheet.hairlineWidth, overflow: 'hidden' },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 16, paddingVertical: 12 },
  shortcuts: { flexDirection: 'row', flexWrap: 'wrap', gap: 10 },
  shortcut: { flexGrow: 1, flexBasis: '46%', borderRadius: 16, borderWidth: StyleSheet.hairlineWidth, paddingVertical: 16, paddingHorizontal: 14, alignItems: 'center', gap: 8 },
});
