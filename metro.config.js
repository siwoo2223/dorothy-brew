// Expo's default Metro config, plus a `buffer` polyfill.
// The `docx` library (used to generate Word files) does `require('buffer')`, a Node core module that
// React Native's bundler doesn't provide. Aliasing it to the npm `buffer` package makes that work.
const { getDefaultConfig } = require('expo/metro-config');

const config = getDefaultConfig(__dirname);

config.resolver.extraNodeModules = {
  ...config.resolver.extraNodeModules,
  buffer: require.resolve('buffer'),
};

module.exports = config;
