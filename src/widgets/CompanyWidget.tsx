import { FlexWidget, TextWidget } from 'react-native-android-widget';

/** 홈 '회사' 위젯: 서버에서 받은 '오늘 챙길 것'(미수금·출항/입항 등) 요약 줄들. */
export function CompanyWidget({ lines }: { lines: string[] }) {
  return (
    <FlexWidget
      style={{ height: 'match_parent', width: 'match_parent', flexDirection: 'column', backgroundColor: '#1A1714', borderRadius: 16, padding: 12 }}
      clickAction="OPEN_APP"
    >
      <TextWidget text="🏢 도로시 · 회사" style={{ fontSize: 13, fontWeight: 'bold', color: '#E0855F' }} />
      {lines.slice(0, 4).map((l, i) => (
        <TextWidget key={i} text={l} style={{ fontSize: 12, color: '#F1EBE2', marginTop: 5 }} />
      ))}
    </FlexWidget>
  );
}
