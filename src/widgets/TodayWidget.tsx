import { FlexWidget, TextWidget } from 'react-native-android-widget';

/** 홈 화면 '오늘' 위젯: 할 일·회신 대기를 한눈에. 데이터는 핸들러가 넣어 준다. */
export function TodayWidget({ lines }: { lines: string[] }) {
  return (
    <FlexWidget
      style={{
        height: 'match_parent',
        width: 'match_parent',
        flexDirection: 'column',
        backgroundColor: '#1A1714',
        borderRadius: 16,
        padding: 12,
      }}
      clickAction="OPEN_APP"
    >
      <TextWidget text="🏠 도로시 · 오늘" style={{ fontSize: 13, fontWeight: 'bold', color: '#E0855F' }} />
      {lines.slice(0, 4).map((l, i) => (
        <TextWidget key={i} text={l} style={{ fontSize: 13, color: '#F1EBE2', marginTop: 5 }} />
      ))}
    </FlexWidget>
  );
}
