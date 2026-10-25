"""Offline OCR: Apple Vision on macOS, Tesseract CLI on Linux.

The Swift source is embedded so wheels need no separately packaged resource.
Input images should have their EXIF orientation normalized by the caller before
OCR, so returned boxes refer to the exact pixels displayed by the preview.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


_SWIFT_SOURCE = r'''
import Foundation
import Vision
import ImageIO

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data(message.utf8))
    exit(1)
}
guard CommandLine.arguments.count == 2 else { fail("Expected one image path.") }
let url = URL(fileURLWithPath: CommandLine.arguments[1])
guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
      let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
      let width = properties[kCGImagePropertyPixelWidth] as? NSNumber,
      let height = properties[kCGImagePropertyPixelHeight] as? NSNumber else {
    fail("Cannot decode this image. Convert it to PNG or JPEG and retry.")
}
guard width.doubleValue > 0, height.doubleValue > 0,
      width.doubleValue * height.doubleValue <= 30_000_000 else {
    fail("Image exceeds the 30 megapixel OCR limit. Resize it and retry.")
}
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = false
do {
    let supported = try request.supportedRecognitionLanguages()
    request.recognitionLanguages = ["en-US", "zh-Hant", "zh-Hans"].filter { supported.contains($0) }
    // The input is already normalized to the displayed pixel orientation.
    let handler = VNImageRequestHandler(url: url, orientation: .up, options: [:])
    try handler.perform([request])
    let blocks: [[String: Any]] = (request.results ?? []).compactMap { observation in
        guard let candidate = observation.topCandidates(1).first,
              !candidate.string.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        let rect = observation.boundingBox
        let x = max(0.0, min(1.0, rect.minX))
        let y = max(0.0, min(1.0, 1.0 - rect.maxY))
        return ["text": candidate.string,
                "confidence": Double(candidate.confidence),
                "box": ["x": x, "y": y,
                        "width": max(0.0, min(1.0 - x, rect.width)),
                        "height": max(0.0, min(1.0 - y, rect.height))]]
    }
    let data = try JSONSerialization.data(withJSONObject: blocks, options: [.sortedKeys])
    FileHandle.standardOutput.write(data)
} catch {
    fail("Apple Vision could not recognize this image. Try a clearer PNG/JPEG scan. " + error.localizedDescription)
}
'''


def ocr_image(path: Path) -> list[dict]:
    """Return recognized lines, confidence and normalized top-left boxes.

    Never upload source pixels. Empty recognition and unavailable runtimes raise
    RuntimeError so the parser can explicitly report needs_ocr instead of ready.
    Swift compilation and recognition together have a 45-second deadline.
    """
    if sys.platform.startswith("linux"):
        from .tesseract import ocr_image as tesseract_ocr
        return tesseract_ocr(path)
    if sys.platform != "darwin":
        raise RuntimeError("Local OCR supports macOS Apple Vision or Linux Tesseract. Supply a searchable PDF on this platform.")
    swift = shutil.which("swift")
    if swift is None:
        raise RuntimeError("Local OCR requires Apple's Swift command-line tools. Install them with xcode-select --install and retry.")
    try:
        image = Path(path).resolve(strict=True)
        if not image.is_file() or image.stat().st_size > 50 * 1024 * 1024:
            raise RuntimeError("OCR requires an image file of at most 50 MiB. Resize the image and retry.")
        with tempfile.TemporaryDirectory(prefix="hr-vision-") as temporary:
            script = Path(temporary) / "vision.swift"
            script.write_text(_SWIFT_SOURCE, encoding="utf-8")
            process = subprocess.run(
                [swift, str(script), str(image)],
                capture_output=True, text=True, timeout=45, check=False,
            )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Local OCR exceeded 45 seconds. Resize the scan or retry after Swift's first-run setup completes.") from exc
    except OSError as exc:
        raise RuntimeError("Cannot read the image or start local OCR. Check file access and install Apple command-line tools with xcode-select --install.") from exc
    if process.returncode:
        # Diagnostic output comes from the local runtime, never document commands.
        detail = process.stderr.strip()[:1500]
        raise RuntimeError("Local Apple Vision OCR failed. Check Swift command-line tools and image readability. " + detail)
    try:
        blocks = json.loads(process.stdout)
        if not isinstance(blocks, list):
            raise ValueError("Expected a list")
        for block in blocks:
            if not isinstance(block["text"], str) or not block["text"].strip():
                raise ValueError("Invalid recognized text")
            box = block["box"]
            for value in [block["confidence"], *(box[key] for key in ("x", "y", "width", "height"))]:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
                    raise ValueError("Invalid OCR coordinates or confidence")
            if box["x"] + box["width"] > 1.000001 or box["y"] + box["height"] > 1.000001:
                raise ValueError("OCR box outside image")
    except (ValueError, TypeError, KeyError) as exc:
        raise RuntimeError("Local OCR returned invalid output. Verify the Swift runtime and retry the image.") from exc
    if not blocks:
        raise RuntimeError("Local OCR found no readable text. Supply a clearer scan or a searchable PDF; this document needs review.")
    return blocks
