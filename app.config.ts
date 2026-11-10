import type { ConfigContext, ExpoConfig } from 'expo/config';

// Google sends the OAuth result back to the reversed client ID scheme
// (com.googleusercontent.apps.<id>), so the app must register it alongside its own scheme.
const reversed = (clientId?: string) => (clientId ? clientId.split('.').reverse().join('.') : null);

export default ({ config }: ConfigContext): ExpoConfig => {
  const schemes = [
    'dorothy',
    reversed(process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID),
    reversed(process.env.EXPO_PUBLIC_GOOGLE_ANDROID_CLIENT_ID),
  ].filter((s): s is string => !!s);

  // The CI build passes its run number as EXPO_PUBLIC_BUILD_NUMBER. Baking it into versionCode
  // makes every published APK a proper Android upgrade (so the in-app updater's install is
  // accepted, not rejected as a same-version reinstall), and the app reads the same value to
  // know which build it is. Omitted in local dev, where the app.json default (1) is fine.
  const buildNumber = Number(process.env.EXPO_PUBLIC_BUILD_NUMBER) || undefined;

  return {
    ...config,
    name: config.name ?? '도로시',
    slug: config.slug ?? 'dorothy',
    scheme: [...new Set(schemes)],
    android: {
      ...config.android,
      ...(buildNumber ? { versionCode: buildNumber } : {}),
    },
  };
};
