import * as DocumentPicker from 'expo-document-picker';
import * as FS from 'expo-file-system/legacy';
import { useState } from 'react';
import { Alert } from 'react-native';

import { ImportBundle, useStore } from '../lib/store';
import { Body, Button, Card, Input, Title } from './ui';

/** Seed what Dorothy knows from a JSON bundle ({ profile, memories, tasks }) — by file or paste. */
export function ImportCard() {
  const { actions } = useStore();
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);

  const apply = async (raw: string): Promise<boolean> => {
    let data: ImportBundle;
    try {
      data = JSON.parse(raw);
    } catch {
      Alert.alert('형식 오류', 'JSON 형식이 아니에요. 파일을 다시 확인해 주세요.');
      return false;
    }
    const result = await actions.importData(data);
    Alert.alert('가져왔어요', `기억 ${result.memories}개, 할 일 ${result.tasks}개를 추가했어요. 이미 있던 항목은 건너뛰었어요.`);
    return true;
  };

  const runFile = async () => {
    const picked = await DocumentPicker.getDocumentAsync({ type: ['application/json', 'text/*', 'application/octet-stream'], copyToCacheDirectory: true });
    if (picked.canceled) return;
    setBusy(true);
    try {
      const raw = await FS.readAsStringAsync(picked.assets[0].uri);
      await apply(raw);
    } catch (e) {
      Alert.alert('가져오기 실패', e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const runPaste = async () => {
    setBusy(true);
    try {
      if (await apply(text)) setText('');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <Title>데이터 가져오기</Title>
      <Body muted>미리 정리한 내 정보 파일(JSON)의 프로필·기억·할 일을 한 번에 추가해요. 파일을 고르거나(간편), 내용을 붙여 넣어도 돼요.</Body>
      <Button label={busy ? '가져오는 중…' : '파일 선택해서 가져오기'} onPress={runFile} disabled={busy} />
      <Input
        value={text}
        onChangeText={setText}
        placeholder='또는 JSON 내용 붙여넣기: {"memories": [...], "tasks": [...]}'
        multiline
        autoCapitalize="none"
        autoCorrect={false}
        style={{ minHeight: 90, maxHeight: 200, textAlignVertical: 'top', fontSize: 12 }}
      />
      <Button label="붙여넣은 내용 가져오기" variant="ghost" onPress={runPaste} disabled={busy || !text.trim()} />
    </Card>
  );
}
