import { useCallback, useEffect, useState } from 'react';
import { AppState, StyleSheet, Switch, Text, View } from 'react-native';

import { Body, Button, Card, Title } from './ui';
import {
  clearSentReplies,
  getReplyDebug,
  getSentReplies,
  isReplyDebug,
  openAccessibilitySettings,
  openAppDetails,
  replyDebugSupported,
  replyWatchGranted,
  replyWatchSupported,
  setReplyDebug,
  type ReplyDebugEntry,
  type SentReply,
} from '../lib/inbox';
import { useTheme } from '../lib/theme';

/**
 * Opt-in: lets Dorothy notice when the owner sends a KakaoTalk/messenger reply (via Android
 * accessibility) and auto-clear the matching "회신 대기". Experimental — shows what it has detected so
 * the owner can confirm it is working. No message text is stored.
 */
export function ReplyWatchCard() {
  const t = useTheme();
  const [granted, setGranted] = useState(false);
  const [recent, setRecent] = useState<SentReply[]>([]);
  const [debug, setDebug] = useState(false);
  const [debugLog, setDebugLog] = useState<ReplyDebugEntry[]>([]);

  const refresh = useCallback(async () => {
    const g = replyWatchGranted();
    const list = await getSentReplies(Date.now() - 7 * 24 * 60 * 60 * 1000, 8).catch(() => []);
    const d = isReplyDebug();
    const dlog = d ? await getReplyDebug(60).catch(() => []) : [];
    setGranted(g);
    setRecent(list);
    setDebug(d);
    setDebugLog(dlog);
  }, []);

  useEffect(() => {
    const id = setTimeout(refresh, 0);
    const sub = AppState.addEventListener('change', (next) => {
      if (next === 'active') refresh();
    });
    return () => {
      clearTimeout(id);
      sub.remove();
    };
  }, [refresh]);

  if (!replyWatchSupported()) return null;

  return (
    <Card>
      <Title>🤖 카톡 답장 자동 감지 (실험)</Title>
      <Body muted>
        켜면 도로시가 카카오톡 등에서 &quot;내가 보낸 답장&quot;을 알아내 회신 대기 목록에서 자동으로 빼요. 안드로이드는 메신저로 내가 보낸 메시지를 앱에 안 알려줘서, 화면 읽기(접근성) 권한이 필요해요. 메시지 내용은 저장하지 않고, 어느 방에 답장했는지만 봐요.
      </Body>
      <Body>
        상태: <Text style={{ color: granted ? t.accent : t.danger, fontWeight: '600' }}>{granted ? '켜짐' : '꺼짐 (권한 필요)'}</Text>
      </Body>
      <Button label={granted ? '접근성 설정 열기 (끄기/확인)' : '접근성 권한 켜기'} onPress={() => openAccessibilitySettings?.()} />
      <Body muted>
        설정 화면에서 &quot;설치된 앱/다운로드한 앱&quot; → &quot;도로시&quot;를 켜 주세요. 켜는 스위치가 회색으로 막혀 있으면, 아래 &quot;앱 정보 열기&quot;에서 점 3개 메뉴 → &quot;제한된 설정 허용&quot;을 먼저 눌러야 해요(삼성/안드로이드 13+).
      </Body>
      <Button label="앱 정보 열기 (제한된 설정 허용)" variant="ghost" onPress={() => openAppDetails?.()} />

      {granted && (
        <>
          <View style={styles.hr} />
          <Body>최근 감지된 내 답장 (확인용)</Body>
          {recent.length === 0 ? (
            <Body muted>아직 없어요. 카카오톡에서 아무 방에나 답장을 한 번 보내고 도로시로 돌아오면 여기에 떠요. 안 뜨면 알려 주세요 — 폰에 맞게 맞출게요.</Body>
          ) : (
            recent
              .slice()
              .reverse()
              .map((r, i) => (
                <Body key={`${r.time}-${i}`} muted>
                  • {new Date(r.time).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: 'numeric', minute: '2-digit' })} · {r.app}
                  {r.hints[0] ? ` · ${r.hints.slice(0, 2).join(' / ')}` : ''}
                </Body>
              ))
          )}
          {recent.length > 0 && <Button label="감지 기록 지우기" variant="ghost" onPress={async () => { clearSentReplies?.(); await refresh(); }} />}

          {replyDebugSupported() && (
            <>
              <View style={styles.hr} />
              <View style={styles.row}>
                <View style={{ flex: 1 }}>
                  <Body>🔧 감지 진단 (안 될 때만)</Body>
                  <Body muted>켜고 카카오톡에서 답장을 한 번 보낸 뒤 여기로 돌아와, 아래에 뜨는 목록을 캡처해 보내주세요. 그걸로 폰에 맞게 맞춰요.</Body>
                </View>
                <Switch
                  value={debug}
                  onValueChange={async (on) => {
                    setReplyDebug?.(on);
                    await refresh();
                  }}
                  trackColor={{ true: t.accent }}
                />
              </View>
              {debug && (
                debugLog.length === 0 ? (
                  <Body muted>아직 신호 없음. 카톡에서 답장 보내고 돌아와서 이 화면을 다시 열어 주세요.</Body>
                ) : (
                  debugLog
                    .slice()
                    .reverse()
                    .map((d, i) => (
                      <Text key={`${d.time}-${i}`} style={{ color: t.subtext, fontSize: 11, fontFamily: 'monospace' }}>
                        {new Date(d.time).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit' })} [{d.kind}] {d.text}
                      </Text>
                    ))
                )
              )}
            </>
          )}
        </>
      )}
    </Card>
  );
}

const styles = StyleSheet.create({
  hr: { height: StyleSheet.hairlineWidth, backgroundColor: 'rgba(127,127,127,0.25)', marginVertical: 4 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10 },
});
