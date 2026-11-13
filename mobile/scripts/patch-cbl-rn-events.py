#!/usr/bin/env python3
"""Idempotent: marshal CBL RN sendEvent onto the main queue (SIGABRT fix)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "node_modules/cbl-reactnative/ios/CblReactnative.swift"
MARKER = "private func emitOnJsQueue"

HELPER = """
  /// RCTEventEmitter is not thread-safe. CBL listeners run on backgroundQueue;
  /// sending from there aborts (RCTEventEmitter.m sendEventWithName).
  private func emitOnJsQueue(_ name: String, body: Any) {
    DispatchQueue.main.async { [weak self] in
      self?.sendEvent(withName: name, body: body)
    }
  }
"""


def main() -> int:
    if not TARGET.exists():
        print("skip: CblReactnative.swift missing")
        return 0
    text = TARGET.read_text()
    if MARKER in text:
        print("cbl-rn events already on main queue")
        return 0
    needle = 'let backgroundQueue = DispatchQueue(label: "com.cblite.reactnative.backgroundQueue")'
    if needle not in text or "self.sendEvent(withName:" not in text:
        print("skip: unexpected CblReactnative.swift shape")
        return 0
    text = text.replace(needle, needle + "\n" + HELPER, 1)
    text = text.replace("self.sendEvent(withName:", "self.emitOnJsQueue(")
    TARGET.write_text(text)
    print("patched CblReactnative.swift sendEvent -> main queue")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
