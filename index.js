// 앱 진입점. expo-router를 그대로 띄우고, 추가로 안드로이드 홈 위젯의 백그라운드 핸들러를 등록한다.
import 'expo-router/entry';

import { registerWidgetTaskHandler } from 'react-native-android-widget';

import { widgetTaskHandler } from './src/widget-task-handler';

registerWidgetTaskHandler(widgetTaskHandler);
