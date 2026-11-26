import { FlexWidget, TextWidget } from 'react-native-android-widget';

/** 홈 '환율' 위젯: 오늘 KRW↔PHP 환율 한 줄. */
export function FxWidget({ pesoToWon, wonToPeso, date }: { pesoToWon: string; wonToPeso: string; date: string }) {
  return (
    <FlexWidget
      style={{ height: 'match_parent', width: 'match_parent', flexDirection: 'column', backgroundColor: '#1A1714', borderRadius: 16, padding: 12 }}
      clickAction="OPEN_APP"
    >
      <TextWidget text="💱 오늘 환율" style={{ fontSize: 12, fontWeight: 'bold', color: '#E0855F' }} />
      <TextWidget text={pesoToWon} style={{ fontSize: 15, fontWeight: 'bold', color: '#F1EBE2', marginTop: 4 }} />
      <TextWidget text={wonToPeso} style={{ fontSize: 12, color: '#A79E93', marginTop: 2 }} />
      {date ? <TextWidget text={date} style={{ fontSize: 10, color: '#A79E93', marginTop: 2 }} /> : null}
    </FlexWidget>
  );
}
