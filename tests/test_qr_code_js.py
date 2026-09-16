"""QR encoder and organizer-integration checks for Task 17."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
QR_SCRIPT = ROOT / "app" / "static" / "vendor" / "qrcode.min.js"
ORGANIZER_HTML = ROOT / "app" / "static" / "group-order-organizer.html"
ORGANIZER_JS = ROOT / "app" / "static" / "group-order-organizer.js"
JSC_CANDIDATES = (
    "jsc",
    "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc",
)


def find_engine() -> str | None:
    for candidate in JSC_CANDIDATES:
        found = shutil.which(candidate) or (candidate if Path(candidate).exists() else None)
        if found:
            return str(found)
    return None


ENGINE = find_engine()
SWIFT = shutil.which("swift")
GROUP_LINK = "https://boba.example/group-order/NT-Wwy6L1tgL0qgTBQ6kYpvR"


def generate_matrix(value: str) -> list[str]:
    driver = f"""
var navigator = {{userAgent: ''}};
function Element() {{ this.children = []; this.title = ''; }}
Element.prototype.setAttribute = function () {{}};
Element.prototype.setAttributeNS = function () {{}};
Element.prototype.appendChild = function (child) {{ this.children.push(child); }};
Element.prototype.hasChildNodes = function () {{ return this.children.length > 0; }};
Element.prototype.removeChild = function () {{ this.children.pop(); }};
Object.defineProperty(Element.prototype, 'lastChild', {{
  get: function () {{ return this.children[this.children.length - 1]; }}
}});
var document = {{
  documentElement: {{tagName: 'svg'}},
  createElementNS: function () {{ return new Element(); }},
  getElementById: function () {{ return new Element(); }}
}};
load({str(QR_SCRIPT)!r});
var code = new QRCode(new Element(), {{
  text: {value!r}, correctLevel: QRCode.CorrectLevel.M
}});
print(JSON.stringify(code._oQRCode.modules.map(function (row) {{
  return row.map(function (dark) {{ return dark ? '1' : '0'; }}).join('');
}})));
"""
    done = subprocess.run(
        [ENGINE, "-e", driver], capture_output=True, text=True, timeout=30)
    if done.returncode:
        raise AssertionError(done.stdout + done.stderr)
    return json.loads(done.stdout.strip().splitlines()[-1])


@unittest.skipUnless(ENGINE, "no JavaScript engine on this machine")
class QrCodeScriptTests(unittest.TestCase):
    def test_realistic_group_link_produces_a_stable_qr_matrix(self):
        rows = generate_matrix(GROUP_LINK)

        self.assertEqual(len(rows), 33)
        self.assertTrue(all(len(row) == len(rows) for row in rows))
        self.assertEqual(rows[0][:7], "1111111")
        self.assertEqual(rows[0][-7:], "1111111")
        self.assertEqual(rows[-1][:7], "1111111")
        digest = hashlib.sha256("\n".join(rows).encode()).hexdigest()
        self.assertEqual(digest, "e8f6a2daa85f75b83a3ece777186f8313a37fbf0f7176b67d6d425ee4a4f95e6")

    def test_organizer_uses_only_the_public_link_and_loads_the_encoder_first(self):
        html = ORGANIZER_HTML.read_text(encoding="utf-8")
        script = ORGANIZER_JS.read_text(encoding="utf-8")

        self.assertLess(html.index("/static/vendor/qrcode.min.js"),
                        html.index("/static/group-order-organizer.js"))
        for control in ("qr-preview", "show-qr", "download-qr", "qr-dialog"):
            self.assertIn(f'id="{control}"', html)
        self.assertIn("text: participantUrl()", script)
        self.assertIn("drawQrMatrix", script)
        self.assertNotIn("text: organizerToken", script)

    @unittest.skipUnless(platform.system() == "Darwin" and SWIFT,
                         "Core Image QR decoding is only available on macOS")
    def test_generated_group_link_is_machine_decodable(self):
        rows = generate_matrix(GROUP_LINK)
        decoder = r"""
import Foundation
import CoreGraphics
import CoreImage

let rows = ProcessInfo.processInfo.environment["QR_MATRIX"]!.split(separator: ",").map(String.init)
let quiet = 4, scale = 12, modules = rows.count
let width = (modules + quiet * 2) * scale
var pixels = [UInt8](repeating: 255, count: width * width)
for row in 0..<modules {
  for column in 0..<modules where rows[row][rows[row].index(rows[row].startIndex, offsetBy: column)] == "1" {
    for y in 0..<scale {
      for x in 0..<scale {
        pixels[(row + quiet) * scale * width + y * width + (column + quiet) * scale + x] = 0
      }
    }
  }
}
let provider = CGDataProvider(data: Data(pixels) as CFData)!
let image = CGImage(width: width, height: width, bitsPerComponent: 8, bitsPerPixel: 8,
                    bytesPerRow: width, space: CGColorSpaceCreateDeviceGray(),
                    bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.none.rawValue),
                    provider: provider, decode: nil, shouldInterpolate: false,
                    intent: .defaultIntent)!
let detector = CIDetector(ofType: CIDetectorTypeQRCode, context: CIContext(),
                          options: [CIDetectorAccuracy: CIDetectorAccuracyHigh])!
let result = detector.features(in: CIImage(cgImage: image)).first as? CIQRCodeFeature
print(result?.messageString ?? "NO QR DETECTED")
"""
        environment = {**os.environ, "QR_MATRIX": ",".join(rows)}
        done = subprocess.run(
            [SWIFT, "-e", decoder], capture_output=True, text=True,
            timeout=30, env=environment)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout.strip(), GROUP_LINK)


if __name__ == "__main__":
    unittest.main()
