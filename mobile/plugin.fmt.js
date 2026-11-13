const { withDangerousMod } = require('@expo/config-plugins');
const fs = require('fs');
const path = require('path');

/** Idempotency marker written into the generated ios/Podfile. */
const MARKER = 'pos-fmt-consteval-fix';

/**
 * Force `{fmt}` off compile-time format-string checks.
 * fmt 11.0.2 (React Native 0.76) redefines FMT_USE_CONSTEVAL in base.h, so
 * a compiler `-D` flag is ignored. Same transform the Podfile hook applies.
 */
function disableFmtConsteval(header) {
  return header.replace(
    /^(#\s*define\s+FMT_USE_CONSTEVAL)\s+1\s*$/gm,
    '$1 0'
  );
}

function rubyPatch(installerVar) {
  return [
    '',
    `    # === ${MARKER}: disable fmt consteval for Xcode 26.4+ (Apple Clang 21) ===`,
    '    # RN 0.76 ships fmt 11.0.2. Apple Clang 21 rejects FMT_STRING consteval',
    '    # in format-inl.h. Forcing FMT_USE_CONSTEVAL 0 uses runtime checks.',
    '    # Drop this once Expo SDK 56 / React Native >= 0.83.9 (fmt 12.1.0).',
    `    fmt_base = File.join(${installerVar}.sandbox.root, 'fmt', 'include', 'fmt', 'base.h')`,
    '    if File.exist?(fmt_base)',
    '      original = File.read(fmt_base)',
    "      patched = original.gsub(/^(#\\s*define\\s+FMT_USE_CONSTEVAL)\\s+1\\s*$/, '\\1 0')",
    '      if patched != original',
    '        File.chmod(0644, fmt_base)',
    '        File.write(fmt_base, patched)',
    `        Pod::UI.puts '[${MARKER}] disabled fmt consteval (Xcode 26 compatibility)'`,
    '      end',
    '    end',
  ].join('\n');
}

/** Inject the post_install hook. No-op if already present. */
function injectIntoPodfile(contents) {
  if (contents.includes(MARKER)) {
    return contents;
  }
  const match = contents.match(/post_install do \|(\w+)\|/);
  if (!match) {
    throw new Error(
      `[${MARKER}] No "post_install do |installer|" block in Podfile`
    );
  }
  return contents.replace(match[0], `${match[0]}\n${rubyPatch(match[1])}`);
}

function withFmtConstevalFix(config) {
  return withDangerousMod(config, [
    'ios',
    (cfg) => {
      const podfilePath = path.join(
        cfg.modRequest.platformProjectRoot,
        'Podfile'
      );
      const contents = fs.readFileSync(podfilePath, 'utf8');
      fs.writeFileSync(podfilePath, injectIntoPodfile(contents));
      return cfg;
    },
  ]);
}

module.exports = withFmtConstevalFix;
module.exports.disableFmtConsteval = disableFmtConsteval;
module.exports.injectIntoPodfile = injectIntoPodfile;
module.exports.MARKER = MARKER;
