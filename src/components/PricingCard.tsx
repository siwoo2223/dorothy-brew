import { useState } from 'react';
import { Alert, StyleSheet, Text, View } from 'react-native';

import { Body, Button, Card, Input, Title } from './ui';
import { useStore } from '../lib/store';
import { useTheme } from '../lib/theme';
import type { PricingProfile } from '../lib/types';

/** 즉석 견적용 표준 단가를 한 번 저장해두면, "세부 20CBM 화장품 견적" 한마디에 도로시가 바로 계산한다. */
export function PricingCard() {
  const t = useTheme();
  const { state, actions } = useStore();
  const p = state.pricing;
  const [v, setV] = useState({
    cbmOnce: String(p.cbmOnce), kgOnce: String(p.kgOnce),
    cbmRegular: String(p.cbmRegular), kgRegular: String(p.kgRegular),
    exportDecl: String(p.exportDecl), koreaPickup: String(p.koreaPickup),
    phDelivery: String(p.phDelivery),
    vatKoreaPct: String(p.vatKoreaPct), vatPhPct: String(p.vatPhPct),
    note: p.note,
  });

  const num = (s: string) => Math.max(0, Math.round(Number(s.replace(/[^0-9.]/g, '')) || 0));
  const set = (k: keyof typeof v) => (text: string) => setV((prev) => ({ ...prev, [k]: text }));

  const save = () => {
    const next: PricingProfile = {
      cbmOnce: num(v.cbmOnce), kgOnce: num(v.kgOnce),
      cbmRegular: num(v.cbmRegular), kgRegular: num(v.kgRegular),
      exportDecl: num(v.exportDecl), koreaPickup: num(v.koreaPickup),
      phDelivery: num(v.phDelivery),
      vatKoreaPct: num(v.vatKoreaPct), vatPhPct: num(v.vatPhPct),
      note: v.note.trim(),
    };
    actions.setPricing(next);
    Alert.alert('저장됨', '이제 "세부 20CBM 화장품 견적"처럼 한마디만 하면 도로시가 바로 계산해요.');
  };

  const field = (label: string, k: keyof typeof v, unit: string) => (
    <View key={k} style={styles.field}>
      <Text style={[styles.lab, { color: t.subtext }]}>{label}</Text>
      <View style={styles.inRow}>
        <Input value={v[k]} onChangeText={set(k)} keyboardType="numeric" style={{ flex: 1, paddingVertical: 9 }} />
        <Text style={{ color: t.subtext, fontSize: 13, width: 34 }}>{unit}</Text>
      </View>
    </View>
  );

  return (
    <Card>
      <Title>⚡ 즉석 견적 단가</Title>
      <Body muted>한 번 저장해두면, 도로시에게 &quot;세부 20CBM 화장품 견적&quot;처럼 한마디만 해도 바로 계산해 줘요. 거래 상황이 바뀌면 여기서 수정하세요.</Body>

      <Text style={[styles.group, { color: t.text }]}>해상운임 (W/M — CBM·kg 중 큰 값)</Text>
      <View style={styles.grid}>
        {field('일회성 · CBM당', 'cbmOnce', '원')}
        {field('일회성 · kg당', 'kgOnce', '원')}
        {field('정기 · CBM당', 'cbmRegular', '원')}
        {field('정기 · kg당', 'kgRegular', '원')}
      </View>

      <Text style={[styles.group, { color: t.text }]}>부대비용</Text>
      <View style={styles.grid}>
        {field('면장(수출신고)', 'exportDecl', '원')}
        {field('한국 픽업비', 'koreaPickup', '원')}
        {field('현지 배송비(기본)', 'phDelivery', '페소')}
      </View>

      <Text style={[styles.group, { color: t.text }]}>부가세율</Text>
      <View style={styles.grid}>
        {field('한국', 'vatKoreaPct', '%')}
        {field('필리핀', 'vatPhPct', '%')}
      </View>

      <Text style={[styles.lab, { color: t.subtext, marginTop: 6 }]}>메모(유의사항 등)</Text>
      <Input value={v.note} onChangeText={set('note')} placeholder="예: 상·하차 인부 별도, 관세·세금 실비" multiline style={{ minHeight: 54, textAlignVertical: 'top' }} />

      <Button label="단가 저장" onPress={save} />
    </Card>
  );
}

const styles = StyleSheet.create({
  group: { fontSize: 13, fontWeight: '800', marginTop: 10, marginBottom: 2 },
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 10 },
  field: { width: '47%', gap: 2 },
  inRow: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  lab: { fontSize: 12, fontWeight: '600' },
});
