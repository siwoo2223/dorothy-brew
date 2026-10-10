import { useState } from 'react';
import { Alert } from 'react-native';

import { ImportBundle, useStore } from '../lib/store';
import { Body, Button, Card, Input, Title } from './ui';

/** Paste a JSON bundle ({ profile, memories, tasks }) to seed what Dorothy knows. */
export function ImportCard() {
  const { actions } = useStore();
  const [text, setText] = useState('');

  const run = async () => {
    let data: ImportBundle;
    try {
      data = JSON.parse(text);
    } catch {
      Alert.alert('형식 오류', 'JSON 형식이 아니에요. 파일 내용을 처음부터 끝까지 그대로 붙여 넣어 주세요.');
      return;
    }
    const result = await actions.importData(data);
    setText('');
    Alert.alert('가져왔어요', `기억 ${result.memories}개, 할 일 ${result.tasks}개를 추가했어요. 이미 있던 항목은 건너뛰었어요.`);
  };

  return (
    <Card>
      <Title>데이터 가져오기</Title>
      <Body muted>미리 정리한 내 정보 파일(JSON)을 붙여 넣으면 프로필·기억·할 일에 한 번에 추가돼요.</Body>
      <Input
        value={text}
        onChangeText={setText}
        placeholder='{"profile": {...}, "memories": [...], "tasks": [...]}'
        multiline
        autoCapitalize="none"
        autoCorrect={false}
        style={{ minHeight: 90, maxHeight: 200, textAlignVertical: 'top', fontSize: 12 }}
      />
      <Button label="가져오기" onPress={run} disabled={!text.trim()} />
    </Card>
  );
}
