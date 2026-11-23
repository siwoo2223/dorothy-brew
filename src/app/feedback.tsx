import Constants from 'expo-constants';
import { router } from 'expo-router';
import { useState } from 'react';
import { Alert, Platform, ScrollView, Share } from 'react-native';

import { Body, Button, Card, Input, Title } from '../components/ui';
import { useStore } from '../lib/store';
import { useTheme } from '../lib/theme';

/**
 * 건의·버그 신고: 사장이 "이게 안 돼요"를 앱 안에서 바로 적고, 버전·기기 정보까지 묶어
 * 한 번에 공유(카톡·메일·복사)할 수 있게 한다. 개발(클로드)로 자동 전송되는 통로는 없어서,
 * 공유 후 클로드 대화창에 붙여넣는 방식 — 매번 상황을 다시 설명하는 수고를 줄여 준다.
 */
export default function Feedback() {
  const t = useTheme();
  const { state } = useStore();
  const [text, setText] = useState('');

  const brain =
    state.model === 'opus' ? 'Claude(고급)' :
    state.model === 'haiku' ? 'Claude(기본)' :
    state.model === 'server' ? '내 서버' : '폰(로컬)';
  const version = (Constants.expoConfig?.version as string) || '1.0.0';
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  const stamp = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}`;

  const compose = () =>
    `[도로시 건의·버그 신고]\n작성: ${stamp}\n\n■ 내용\n${text.trim()}\n\n■ 앱 정보 (고치는 데 도움돼요)\n- 버전: ${version}\n- 기기: ${Platform.OS === 'ios' ? 'iOS' : 'Android'} ${Platform.Version}\n- 두뇌: ${brain}`;

  const share = async () => {
    if (!text.trim()) {
      Alert.alert('내용을 적어 주세요', '무엇이 안 되는지 / 어떻게 됐으면 좋겠는지 한두 줄이면 돼요.');
      return;
    }
    try {
      await Share.share({ message: compose() });
    } catch {
      Alert.alert('공유를 열지 못했어요', '잠시 후 다시 시도해 주세요.');
    }
  };

  return (
    <ScrollView style={{ flex: 1, backgroundColor: t.bg }} contentContainerStyle={{ padding: 16, gap: 14 }} keyboardShouldPersistTaps="handled">
      <Card>
        <Title>🛠️ 건의 · 버그 신고</Title>
        <Body muted>
          도로시 쓰다가 안 되는 것·불편한 것·이렇게 됐으면 하는 것을 적어 주세요. 버전·기기 정보까지
          묶어서 한 번에 공유할 수 있어요. 공유한 내용을 클로드 대화창에 붙여넣으면 제가 고쳐서 새 빌드로 드려요.
        </Body>
      </Card>

      <Card>
        <Input
          value={text}
          onChangeText={setText}
          placeholder={'예) 출항 스케줄에서 선사 새로고침을 눌렀는데 아무 반응이 없어요.\n예) 견적서에 회사 로고도 넣어주면 좋겠어요.'}
          multiline
          style={{ minHeight: 140, textAlignVertical: 'top' }}
        />
        <Body muted>
          첨부할 화면 캡처가 있으면, 공유한 글과 함께 클로드 대화창에 사진도 올려 주세요(캡처는 글에 자동으로 안 담겨요).
        </Body>
        <Button label="📤 공유하기 (클로드에 붙여넣기)" onPress={share} />
        <Button label="닫기" variant="ghost" onPress={() => router.back()} />
      </Card>
    </ScrollView>
  );
}
