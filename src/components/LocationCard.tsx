import { useEffect, useState } from 'react';
import { Alert, Linking, Platform } from 'react-native';

import { Body, Button, Card, Title } from './ui';
import { currentPlace, locationGranted, requestLocation } from '../lib/location';

/**
 * '내 위치': 켜두면 도로시가 "지금 내 위치 기준"으로 추천·길안내·날씨를 챙긴다. 위치는 필요할 때만
 * 읽고(눌렀을 때만) 휴대폰 밖으로 저장·전송하지 않는다. 여기서 권한을 켜고 지금 위치를 확인할 수 있다.
 */
export function LocationCard() {
  const [granted, setGranted] = useState(false);
  const [place, setPlace] = useState<string>('');
  const [busy, setBusy] = useState(false);

  useEffect(() => { locationGranted().then(setGranted).catch(() => {}); }, []);

  const enable = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const ok = await requestLocation();
      setGranted(ok);
      if (!ok) {
        Alert.alert(
          '위치 권한이 꺼져 있어요',
          '휴대폰 설정에서 도로시의 위치 권한을 허용해 주세요.',
          [{ text: '취소' }, { text: '설정 열기', onPress: () => Linking.openSettings() }],
        );
        return;
      }
      const p = await currentPlace();
      setPlace(p ? `${p.label}` : '위치를 가져오지 못했어요(GPS 확인).');
    } catch (e) {
      Alert.alert('오류', e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const check = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const p = await currentPlace();
      setPlace(p ? `${p.label} (${p.latitude.toFixed(3)}, ${p.longitude.toFixed(3)})` : '위치를 가져오지 못했어요. 권한·GPS를 확인해 주세요.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <Title>📍 내 위치</Title>
      <Body muted>
        켜두면 &quot;지금 내 위치에서 가까운 곳 추천&quot;, 길안내, 오늘 날씨를 현재 위치 기준으로 챙겨요. 위치는
        필요할 때만 읽고(늘 추적하지 않음), 휴대폰 밖으로 저장·전송하지 않아요.
      </Body>
      {Platform.OS === 'web' ? (
        <Body muted>이 기능은 휴대폰 앱에서만 돼요.</Body>
      ) : (
        <>
          <Body>{granted ? '✅ 위치 사용 켜짐' : '⚪️ 위치 사용 꺼짐'}</Body>
          {!granted && <Button label={busy ? '여는 중…' : '위치 사용 켜기'} onPress={enable} disabled={busy} />}
          {granted && <Button label={busy ? '확인 중…' : '지금 내 위치 확인'} variant="ghost" onPress={check} disabled={busy} />}
          {!!place && <Body muted>지금 위치: {place}</Body>}
        </>
      )}
    </Card>
  );
}
