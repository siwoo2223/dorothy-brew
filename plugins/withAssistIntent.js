// Lets Android offer Dorothy as the "digital assistant app" (Settings → Apps → Default apps).
// Once chosen, the system assist gesture (corner swipe, home long-press, or a side key set to
// "digital assistant") opens Dorothy, which starts listening right away.
const { withAndroidManifest, AndroidConfig } = require('expo/config-plugins');

const ACTIONS = ['android.intent.action.ASSIST', 'android.intent.action.VOICE_COMMAND'];

module.exports = function withAssistIntent(config) {
  return withAndroidManifest(config, (cfg) => {
    const activity = AndroidConfig.Manifest.getMainActivityOrThrow(cfg.modResults);
    activity['intent-filter'] = activity['intent-filter'] ?? [];
    for (const action of ACTIONS) {
      const exists = activity['intent-filter'].some((f) => f.action?.some((a) => a.$['android:name'] === action));
      if (!exists) {
        activity['intent-filter'].push({
          action: [{ $: { 'android:name': action } }],
          category: [{ $: { 'android:name': 'android.intent.category.DEFAULT' } }],
        });
      }
    }
    return cfg;
  });
};
