import { useFocusEffect, useLocalSearchParams, useNavigation, useRouter } from 'expo-router';
import { useCallback, useLayoutEffect, useState } from 'react';
import { ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';

import { Body, Card, Title } from '../components/ui';
import { Chart } from '../components/Chart';
import { companyConfigured, loadCompanyConfig } from '../lib/company';
import { chatAverageRate, ChatRate, naverPhpKrw, NaverRate } from '../lib/fx';
import { companyFinance, Finance, money, Money } from '../lib/homeSummary';
import { useTheme } from '../lib/theme';

const md = (ms: number) => new Date(ms).toLocaleDateString('ko-KR', { month: 'numeric', day: 'numeric' });

const sym = (cur: string) => (cur === 'PHP' ? '₱' : cur === 'KRW' ? '₩' : cur + ' ');

/**
 * Funds on hand + this month's revenue / deposits / expense / profit — read straight from the
 * company server (dorothy_finance.php), the SAME figures as the admin accounting page, split by
 * currency (₱/₩) so nothing is wrongly added across currencies. Refreshes every time it opens.
 */
export default function FinanceScreen() {
  const t = useTheme();
  const navigation = useNavigation();
  const router = useRouter();
  const params = useLocalSearchParams<{ focus?: string }>(); // 'net' highlights 손익
  const [fin, setFin] = useState<Finance | null>(null);
  const [loading, setLoading] = useState(true);
  const [configured, setConfigured] = useState(true);
  const [error, setError] = useState('');
  const [naver, setNaver] = useState<NaverRate>(null);
  const [chatRate, setChatRate] = useState<ChatRate>(null);

  useLayoutEffect(() => {
    navigation.setOptions({ title: '자금 · 손익' });
  }, [navigation]);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    await loadCompanyConfig().catch(() => {});
    if (!companyConfigured()) {
      setConfigured(false);
      setLoading(false);
      return;
    }
    setConfigured(true);
    try {
      const f = await companyFinance();
      setFin(f);
      if (!f) setError('자금 정보를 불러오지 못했어요. 회사 서버가 업데이트됐는지 확인하고, 당겨서 다시 시도해 주세요.');
    } catch {
      setError('자금 정보를 불러오지 못했어요. 당겨서 다시 시도해 주세요.');
    } finally {
      setLoading(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      load();
      // 환율 두 가지는 회사 서버와 별개로 가져온다(네이버 실시간 + 단톡방 평균). 실패해도 화면은 유지.
      naverPhpKrw().then(setNaver).catch(() => setNaver(null));
      chatAverageRate().then(setChatRate).catch(() => setChatRate(null));
    }, [load]),
  );

  // 환산에 쓸 환율: 실시간(네이버→시장 백업) 우선, 없으면 회사 서버 기준 환율(수동).
  const convRate = naver?.krwPerPhp && naver.krwPerPhp > 0 ? naver.krwPerPhp : fin?.fx ?? 0;
  const liveLabel = naver?.source === 'naver' ? '네이버 실시간' : '실시간 시장환율';
  const rateLabel = naver?.krwPerPhp ? liveLabel : '회사 기준(수동)';

  const moneyRow = (list: Money[]) => (list.length ? list.map(money).join('  ·  ') : '—');

  return (
    <ScrollView
      contentContainerStyle={styles.container}
      refreshControl={<RefreshControl refreshing={loading} onRefresh={load} tintColor={t.accent} />}
    >
      {!configured ? (
        <Card>
          <Title>자금 · 손익</Title>
          <Body muted>회사 서버가 아직 연결되지 않았어요. 설정 → 회사·구글에서 주소와 토큰을 넣어 주세요.</Body>
        </Card>
      ) : loading && !fin ? (
        <Card><ActivityIndicator color={t.accent} /><Body muted>불러오는 중…</Body></Card>
      ) : error ? (
        <Card><Body muted>{error}</Body></Card>
      ) : fin ? (
        <>
          {/* 현재 자금 */}
          <Text style={[styles.section, { color: t.text }]}>💰 현재 자금 (지금 통장·지갑에 있는 돈)</Text>
          <View style={[styles.totalCard, { backgroundColor: t.accentSoft, borderColor: t.accent }]}>
            {fin.funds.length ? (
              fin.funds.map((m) => (
                <Text key={m.currency} style={[styles.totalN, { color: t.accent }]}>{money(m)}</Text>
              ))
            ) : (
              <Body muted>계좌 잔액을 읽지 못했어요.</Body>
            )}
            <Text style={{ color: t.subtext, fontSize: 12 }}>통화별 합계 (가상계좌 제외)</Text>
            {fin.funds.length > 0 && convRate > 0 && (() => {
              const php = fin.funds.find((f) => f.currency === 'PHP')?.amount ?? 0;
              const krw = fin.funds.find((f) => f.currency === 'KRW')?.amount ?? 0;
              const inPhp = php + krw / convRate;
              const inKrw = krw + php * convRate;
              return (
                <View style={{ borderTopColor: t.accent, borderTopWidth: StyleSheet.hairlineWidth, marginTop: 8, paddingTop: 8, gap: 2 }}>
                  <Text style={{ color: t.text, fontSize: 15, fontWeight: '800' }}>환산 합계 ({rateLabel} ₩{convRate.toFixed(2)}/₱1)</Text>
                  <Text style={{ color: t.text, fontSize: 15 }}>전부 원화로: <Text style={{ fontWeight: '800' }}>{money({ currency: 'KRW', amount: inKrw })}</Text></Text>
                  <Text style={{ color: t.text, fontSize: 15 }}>전부 페소로: <Text style={{ fontWeight: '800' }}>{money({ currency: 'PHP', amount: inPhp })}</Text></Text>
                </View>
              );
            })()}
          </View>

          {/* 환율: 네이버 실시간 + 단톡방 평균 (따로) */}
          <View style={[styles.listCard, { backgroundColor: t.card, borderColor: t.border }]}>
            <View style={styles.rateRow}>
              <Text style={{ color: t.subtext, fontSize: 14, flex: 1 }}>🟢 {naver ? liveLabel : '실시간 환율'}</Text>
              <Text style={{ color: t.text, fontSize: 15, fontWeight: '800' }}>
                {naver ? `₩${naver.krwPerPhp.toFixed(2)} / ₱1` : '못 가져옴'}
              </Text>
            </View>
            <View style={[styles.rateRow, { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.border }]}>
              <View style={{ flex: 1 }}>
                <Text style={{ color: t.subtext, fontSize: 14 }}>💬 단톡방 평균</Text>
                {chatRate && (
                  <Text style={{ color: t.subtext, fontSize: 11 }}>
                    최근 {chatRate.count}건 · {md(chatRate.latest)}까지 · {chatRate.min.toFixed(1)}~{chatRate.max.toFixed(1)}
                  </Text>
                )}
              </View>
              <Text style={{ color: t.text, fontSize: 15, fontWeight: '800' }}>
                {chatRate ? `₩${chatRate.avg.toFixed(2)} / ₱1` : '언급 못 찾음'}
              </Text>
            </View>
            {!chatRate && (
              <Text style={{ color: t.subtext, fontSize: 11, paddingHorizontal: 16, paddingBottom: 12 }}>
                단톡방 환율은 메시지 알림에서 &apos;환율·페소·원&apos;이 적힌 숫자만 모아 평균 내요. 알림 읽기가 켜져 있고 환율이 올라온 방이 있어야 보여요.
              </Text>
            )}
          </View>

          {/* 계좌: 통화별로 묶어 소계 + 계좌 누르면 그 계좌 이번 달 거래 상세 */}
          {fin.funds.map((fund) => {
            const accts = fin.accounts.filter((a) => a.currency === fund.currency);
            if (accts.length === 0) return null;
            const label = fund.currency === 'PHP' ? '페소' : fund.currency === 'KRW' ? '원화' : fund.currency;
            return (
              <View key={fund.currency} style={{ gap: 4 }}>
                <Text style={{ color: t.subtext, fontSize: 13, fontWeight: '700', paddingHorizontal: 4 }}>
                  {sym(fund.currency)} {label} 계좌 — 합계 {money(fund)}
                </Text>
                <View style={[styles.listCard, { backgroundColor: t.card, borderColor: t.border }]}>
                  {accts.map((a, i) => (
                    <Pressable
                      key={a.id || a.name + i}
                      onPress={() => router.navigate({ pathname: '/account', params: { id: String(a.id), name: a.name, currency: a.currency } })}
                      style={({ pressed }) => [styles.acctRow, { opacity: pressed ? 0.85 : 1 }, i > 0 && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.border }]}
                    >
                      <Text style={{ color: t.text, fontSize: 14, flex: 1 }} numberOfLines={1}>{a.name}</Text>
                      <Text style={{ color: t.text, fontSize: 14, fontWeight: '700' }}>{money({ currency: a.currency, amount: a.balance })}</Text>
                      <Text style={{ color: t.accent, fontSize: 13 }}> ›</Text>
                    </Pressable>
                  ))}
                </View>
              </View>
            );
          })}

          {/* 이번 달 */}
          <Text style={[styles.section, { color: t.text }]}>📅 이번 달 ({fin.month})</Text>
          <View style={[styles.listCard, { backgroundColor: t.card, borderColor: t.border }]}>
            <Line label="매출 (판 금액)" value={moneyRow(fin.revenue)} t={t} />
            <Line label="입금 (실제 들어온 돈)" value={moneyRow(fin.deposits)} t={t} border />
            <Line label="지출 (쓴 돈)" value={moneyRow(fin.expense)} t={t} border />
            <Line label="손익 (매출 − 지출)" value={moneyRow(fin.net)} t={t} border highlight={params.focus === 'net'} />
          </View>

          {/* 이번 달 지출 분류별 (통장 출금 기준) */}
          {fin.expenseByCategory.length > 0 && (
            <>
              <Text style={[styles.section, { color: t.text }]}>🧾 이번 달 지출 — 어디에 썼나 (통장 기준)</Text>
              {fin.expenseByCategory.map((c) => (
                <Chart
                  key={c.currency}
                  spec={{ type: 'bar', title: `${sym(c.currency)} 지출 분류`, unit: c.currency, data: c.items.slice(0, 10).map((i) => ({ label: i.category, value: i.amount })) }}
                />
              ))}
            </>
          )}

          {/* 최근 6개월 결산 */}
          {fin.months.length > 0 && (
            <>
              <Text style={[styles.section, { color: t.text }]}>📊 월별 결산 (최근 6개월)</Text>
              <View style={[styles.listCard, { backgroundColor: t.card, borderColor: t.border }]}>
                {fin.months.map((m, i) => (
                  <View key={m.ym} style={[styles.monthRow, i > 0 && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.border }]}>
                    <Text style={{ color: t.text, fontSize: 14, fontWeight: '700', width: 64 }}>{m.ym.slice(2)}</Text>
                    <View style={{ flex: 1, gap: 2 }}>
                      <Text style={{ color: t.text, fontSize: 14, fontWeight: '700' }}>손익 {m.net.length ? m.net.map(money).join(' · ') : '—'}</Text>
                      <Text style={{ color: t.subtext, fontSize: 11 }}>
                        매출 {m.revenue.length ? m.revenue.map(money).join(' · ') : '—'} · 지출 {m.expense.length ? m.expense.map(money).join(' · ') : '—'}
                      </Text>
                    </View>
                  </View>
                ))}
              </View>
            </>
          )}

          <Body muted>
            매출·지출·손익은 회계(재무제표)와 같은 기준이에요. 입금과 &quot;지출 분류별&quot;은 통장에 실제로 오간 현금 기준이라 발생주의 지출과 다를 수 있어요. 통화(₱·₩)는 섞지 않고 따로 보여드려요.
          </Body>
        </>
      ) : null}
    </ScrollView>
  );
}

function Line({ label, value, t, border, highlight }: { label: string; value: string; t: ReturnType<typeof useTheme>; border?: boolean; highlight?: boolean }) {
  return (
    <View style={[styles.line, border && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.border }, highlight && { backgroundColor: t.accentSoft }]}>
      <Text style={{ color: t.subtext, fontSize: 14, flex: 1 }}>{label}</Text>
      <Text style={{ color: t.text, fontSize: 15, fontWeight: '700' }}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { padding: 16, gap: 12 },
  section: { fontSize: 15, fontWeight: '800', letterSpacing: -0.2, marginTop: 4, marginBottom: -4 },
  totalCard: { borderRadius: 18, borderWidth: 1, padding: 18, gap: 2, alignItems: 'flex-start' },
  totalN: { fontSize: 26, fontWeight: '800', letterSpacing: -0.5 },
  listCard: { borderRadius: 16, borderWidth: StyleSheet.hairlineWidth, overflow: 'hidden' },
  acctRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 16, paddingVertical: 12 },
  rateRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 16, paddingVertical: 13 },
  line: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 16, paddingVertical: 14 },
  monthRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 16, paddingVertical: 12 },
});
