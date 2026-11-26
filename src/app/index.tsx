import { Redirect } from 'expo-router';

/**
 * 앱의 첫 화면은 '대화'(클로드처럼 대화 중심). 앱을 열면 여기로 들어와 바로 대화 탭으로 보낸다.
 * '오늘' 대시보드는 /today 로 옮겼고, 대화 화면의 🏠 버튼으로 연다.
 */
export default function Index() {
  return <Redirect href="/chat" />;
}
