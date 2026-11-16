import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';

import { Body, Card, Title } from '../components/ui';
import { Chart } from '../components/Chart';
import { companyConfigured, loadCompanyConfig } from '../lib/company';
import { companyDashboard, Dashboard } from '../lib/homeSummary';
import { useTheme } from '../lib/theme';

type Shortcut = { icon: string; label: string; nav?: { pathname: string; params?: Record<string, string> }; ask?: string };

/** Quick entries — most open a dedicated page (not chat); only the open-ended one asks Dorothy. */
const SHORTCUTS: Shortcut[] = [
  { icon: '🚢', label: '출항 스케줄', nav: { pathname: '/schedule' } },
  { icon: '💰', label: '자금 · 손익', nav: { pathname: '/finance' } },
  { icon: '🧾', label: '미수금', nav: { pathname: '/shipments', params: { status: '배송완료', filter: 'unpaid', title: '미수 가능(배송완료·입금 0)' } } },
  { icon: '📅', label: '이번 달 손익', nav: { pathname: '/finance', params: { focus: 'net' } } },
  { icon: '📋', label: '요약 묻기', ask: '회사 현황 요약해줘. 통관·배송중·입고 상태별 건수랑 이번 달 손익을 그래프로 보여줘.' },
];

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
          {/* 상태별 건수 그래프 + 눌러서 상세 목록 */}
          {loading && !dash ? (
            <Card><ActivityIndicator color={t.accent} /><Body muted>회사 현황을 불러오는 중…</Body></Card>
          ) : dash && dash.statuses.length > 0 ? (
            <>
              <Chart spec={{ type: 'bar', title: '🏢 화물 상태별 건수', data: dash.statuses }} />
              <Text style={{ color: t.subtext, fontSize: 12, paddingHorizontal: 4 }}>상태를 누르면 그 화물 목록(상세)이 열려요 ›</Text>
              <View style={styles.shortcuts}>
                {dash.statuses.map((s) => (
                  <Pressable
                    key={s.label}
                    onPress={() => router.navigate({ pathname: '/shipments', params: { status: s.label, title: `${s.label} 화물 (${s.value}건)` } })}
                    style={({ pressed }) => [styles.statusChip, { backgroundColor: t.card, borderColor: t.border, opacity: pressed ? 0.8 : 1 }]}
                  >
                    <Text style={{ color: t.text, fontSize: 13, fontWeight: '700' }}>{s.label}</Text>
                    <Text style={{ color: t.accent, fontSize: 13, fontWeight: '800' }}>{s.value}</Text>
                  </Pressable>
                ))}
              </View>
            </>
          ) : (
            <Card>
              <Title>🏢 화물 상태</Title>
              <Body muted>상태별 건수를 읽지 못했어요. 아래 &quot;전체 현황 요약&quot;으로 도로시에게 물어보면 최신 수치를 알려드려요.</Body>
            </Card>
          )}

          {/* 자금·손익은 통화(₱/₩)를 섞으면 안 되므로 전용 페이지(정확한 서버 수치)로 안내 */}
          <Pressable
            onPress={() => router.navigate('/finance')}
            style={({ pressed }) => [styles.listCard, { backgroundColor: t.accentSoft, borderColor: t.accent, opacity: pressed ? 0.85 : 1 }]}
          >
            <View style={{ paddingHorizontal: 16, paddingVertical: 14, gap: 2 }}>
              <Text style={{ color: t.accent, fontSize: 15, fontWeight: '800' }}>💰 자금 · 손익 보기 ›</Text>
              <Text style={{ color: t.subtext, fontSize: 12 }}>현재 계좌별 잔액 + 이번 달 매출·입금·지출·손익 (₱·₩ 통화별, 회계 화면과 동일)</Text>
            </View>
          </Pressable>
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
                  <Pressable
                    key={it.title + i}
                    onPress={() => router.navigate({ pathname: '/shipments', params: { status: it.status ?? '', code: it.code ?? '', filter: it.filter ?? '', title: it.title } })}
                    style={({ pressed }) => [styles.row, { opacity: pressed ? 0.85 : 1 }, i > 0 && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.border }]}
                  >
                    <View style={{ flex: 1 }}>
                      <Text style={{ color: t.text, fontSize: 15, fontWeight: '600' }} numberOfLines={1}>{it.title}</Text>
                      {!!it.sub && <Text style={{ color: t.subtext, fontSize: 12 }} numberOfLines={1}>{it.sub}</Text>}
                    </View>
                    <Text style={{ color: t.accent, fontSize: 13, fontWeight: '700' }}>조회 ›</Text>
                  </Pressable>
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
                onPress={() => (s.nav ? router.navigate(s.nav) : s.ask ? ask(s.ask) : undefined)}
                style={({ pressed }) => [styles.shortcut, { backgroundColor: t.card, borderColor: t.border, opacity: pressed ? 0.8 : 1 }]}
              >
                <Text style={{ fontSize: 22 }}>{s.icon}</Text>
                <Text style={{ color: t.text, fontSize: 13, fontWeight: '700' }} numberOfLines={1}>{s.label}</Text>
              </Pressable>
            ))}
          </View>

          <Body muted>숫자는 회사 서버에서 바로 읽은 값이에요. 당겨서 새로고침할 수 있어요.</Body>
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
  statusChip: { flexDirection: 'row', alignItems: 'center', gap: 7, borderRadius: 14, borderWidth: StyleSheet.hairlineWidth, paddingHorizontal: 12, paddingVertical: 8 },
});
