import { useFocusEffect, useLocalSearchParams, useNavigation, useRouter } from 'expo-router';
import { useCallback, useLayoutEffect, useState } from 'react';
import { ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';

import { Body, Card } from '../components/ui';
import { ImageBlock } from '../components/ImageBlock';
import { companyConfigured, fetchCompanyPhotos, loadCompanyConfig } from '../lib/company';
import { useTheme } from '../lib/theme';

/**
 * One shipment's detail + its intake photos. Opened by tapping a row in the shipment list. Basic
 * fields come through params (instant); photos are fetched from the server (dorothy_photos.php).
 */
export default function ShipmentDetail() {
  const t = useTheme();
  const navigation = useNavigation();
  const router = useRouter();
  const p = useLocalSearchParams<{
    no?: string; customer?: string; voyage?: string; status?: string; days?: string;
    items?: string; boxes?: string; cbm?: string; weight?: string; unpaid?: string;
  }>();
  const no = p.no || '';

  const [photos, setPhotos] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [note, setNote] = useState('');

  useLayoutEffect(() => {
    navigation.setOptions({ title: no || '화물 상세' });
  }, [navigation, no]);

  const load = useCallback(async () => {
    setLoading(true);
    setNote('');
    await loadCompanyConfig().catch(() => {});
    if (!companyConfigured() || !no) {
      setLoading(false);
      return;
    }
    try {
      const d = JSON.parse(await fetchCompanyPhotos(no)) as { photos?: { url?: unknown }[]; found?: boolean };
      const urls = Array.isArray(d.photos) ? d.photos.map((x) => (typeof x.url === 'string' ? x.url : '')).filter(Boolean) : [];
      setPhotos(urls);
      if (urls.length === 0) setNote(d.found === false ? '해당 조회번호를 찾지 못했어요.' : '등록된 입고 사진이 없어요.');
    } catch {
      setNote('사진을 불러오지 못했어요. 당겨서 다시 시도해 주세요.');
    } finally {
      setLoading(false);
    }
  }, [no]);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load]),
  );

  const line = (label: string, value?: string) =>
    value ? (
      <View style={styles.row}>
        <Text style={{ color: t.subtext, fontSize: 13, width: 72 }}>{label}</Text>
        <Text style={{ color: t.text, fontSize: 15, flex: 1 }}>{value}</Text>
      </View>
    ) : null;

  return (
    <ScrollView
      contentContainerStyle={styles.container}
      refreshControl={<RefreshControl refreshing={loading} onRefresh={load} tintColor={t.accent} />}
    >
      <Card>
        <Text style={{ color: t.text, fontSize: 20, fontWeight: '800' }}>{no || '화물'}</Text>
        {line('고객', p.customer)}
        {line('항차', p.voyage)}
        {line('상태', p.status ? `${p.status}${p.days ? ` · ${p.days}일째` : ''}` : undefined)}
        {line('품목', p.items)}
        {line('수량', [p.boxes ? `박스 ${p.boxes}` : '', p.cbm ? `${p.cbm} CBM` : '', p.weight ? `${p.weight} kg` : ''].filter(Boolean).join(' · ') || undefined)}
        {p.unpaid === '1' && <Text style={{ color: t.danger, fontSize: 13, fontWeight: '700', marginTop: 4 }}>💰 배송완료인데 입금 기록 0 — 미수 확인 필요</Text>}
      </Card>

      <Text style={[styles.section, { color: t.text }]}>📷 입고 사진</Text>
      {loading && photos.length === 0 ? (
        <View style={{ paddingVertical: 20 }}><ActivityIndicator color={t.accent} /></View>
      ) : photos.length > 0 ? (
        <ImageBlock urls={photos} caption={`${no} 입고 사진 ${photos.length}장`} />
      ) : (
        <Body muted>{note || '등록된 사진이 없어요.'}</Body>
      )}

      <Pressable
        onPress={() => router.navigate({ pathname: '/chat', params: { ask: `${no} 화물 영수증 금액(견적)이랑 미수금 정확히 계산해줘.` } })}
        style={({ pressed }) => [styles.askBtn, { borderColor: t.border, opacity: pressed ? 0.85 : 1 }]}
      >
        <Text style={{ color: t.accent, fontWeight: '700' }}>💵 이 화물 영수증·금액 계산받기</Text>
      </Pressable>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 16, gap: 12 },
  row: { flexDirection: 'row', alignItems: 'flex-start', gap: 10, marginTop: 6 },
  section: { fontSize: 15, fontWeight: '800', letterSpacing: -0.2, marginTop: 4 },
  askBtn: { borderWidth: 1, borderRadius: 14, paddingVertical: 13, alignItems: 'center', marginTop: 4 },
});
