const {
  withProjectBuildGradle,
  withXcodeProject,
  withPodfileProperties,
} = require('@expo/config-plugins');

const PKG = 'cbl-reactnative';

function modifyAndroidBuildGradle(config) {
  const lineToAdd = ` apply from: "../node_modules/${PKG}/android/build.gradle"`;
  if (!config.modResults.contents.includes(lineToAdd)) {
    config.modResults.contents += `\n${lineToAdd}`;
  }
  return config;
}

function modifyXcodeProject(config) {
  return config;
}

function includeNativeModulePod(config) {
  return withPodfileProperties(config, async (podConfig) => {
    const podspecPath = `../node_modules/${PKG}/cbl-reactnative.podspec`;
    if (
      podConfig.modResults.podfileProperties !== undefined &&
      podConfig.modResults.podfileProperties.pod !== undefined
    ) {
      podConfig.modResults.podfileProperties.pod(`'cbl-reactnative', :path => '${podspecPath}'`);
    }
    return podConfig;
  });
}

module.exports = (config) => {
  config = withProjectBuildGradle(config, (gradleConfig) => modifyAndroidBuildGradle(gradleConfig));
  config = withXcodeProject(config, (xcodeConfig) => modifyXcodeProject(xcodeConfig));
  config = includeNativeModulePod(config);
  return config;
};
