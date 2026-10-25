"""Offline CPU OCR adapter. TSV word evidence is aggregated into source lines."""
import csv
import io
import math
from pathlib import Path
import shutil
import subprocess
from PIL import Image

LANGUAGES = ('eng', 'chi_sim', 'chi_tra')
MAX_PIXELS = 24_000_000


def _lines_from_tsv(tsv: str, width: int, height: int) -> list[dict]:
    required = {'level', 'page_num', 'block_num', 'par_num', 'line_num',
                'left', 'top', 'width', 'height', 'conf', 'text'}
    groups = {}
    try:
        reader = csv.DictReader(io.StringIO(tsv), delimiter='\t', quoting=csv.QUOTE_NONE)
        if not required.issubset(reader.fieldnames or []):
            raise ValueError('Missing TSV columns')
        for row in reader:
            if int(row['level']) != 5:  # Structural rows have confidence -1.
                continue
            text = row['text']
            if text is None or None in row:
                raise ValueError('Malformed TSV word')
            if not text.strip():
                continue
            x, y, w, h = (int(row[k]) for k in ('left', 'top', 'width', 'height'))
            confidence = float(row['conf'])
            if not (math.isfinite(confidence) and 0 <= confidence <= 100 and
                    0 <= x < width and 0 <= y < height and w > 0 and h > 0 and
                    x+w <= width and y+h <= height):
                raise ValueError('Invalid TSV box or confidence')
            key = tuple(int(row[k]) for k in ('page_num', 'block_num', 'par_num', 'line_num'))
            groups.setdefault(key, []).append((text, x, y, w, h, confidence))
        result = []
        for words in groups.values():
            x0 = min(w[1] for w in words); y0 = min(w[2] for w in words)
            x1 = max(w[1]+w[3] for w in words); y1 = max(w[2]+w[4] for w in words)
            result.append({'text': ' '.join(w[0] for w in words),
                           'box': {'x': x0/width, 'y': y0/height,
                                   'width': (x1-x0)/width, 'height': (y1-y0)/height},
                           'confidence': sum(w[5] for w in words)/len(words)/100})
    except (ValueError, TypeError, KeyError, csv.Error) as exc:
        raise RuntimeError('Tesseract returned invalid TSV evidence; check the installed runtime and retry.') from exc
    if not result:
        raise RuntimeError('Tesseract found no readable text. Supply a clearer scan or a searchable PDF.')
    return result


def ocr_image(path: Path) -> list[dict]:
    """Require English and both Chinese language packs; never silently drop languages."""
    executable = shutil.which('tesseract')
    if not executable:
        raise RuntimeError('Local OCR requires tesseract-ocr with tesseract-ocr-eng, tesseract-ocr-chi-sim and tesseract-ocr-chi-tra. Install these OS packages and retry.')
    try:
        image = Path(path).resolve(strict=True)
        if not image.is_file() or image.stat().st_size > 30 * 1024 * 1024:
            raise RuntimeError('OCR image exceeds the 30 MiB input limit.')
        with Image.open(image) as source:
            width, height = source.size
            if width <= 0 or height <= 0 or width*height > MAX_PIXELS:
                raise RuntimeError('OCR image exceeds the 24 megapixel limit; resize and retry.')
            if getattr(source, 'n_frames', 1) != 1:
                raise RuntimeError('OCR accepts one image frame at a time; export separate pages.')
            source.verify()
        available = subprocess.run([executable, '--list-langs'], capture_output=True,
                                   text=True, encoding='utf-8', errors='replace', timeout=10, check=False)
        if available.returncode:
            raise RuntimeError('Cannot inspect Tesseract language data. Check the installation and TESSDATA_PREFIX.')
        missing = set(LANGUAGES) - set(available.stdout.splitlines())
        if missing:
            raise RuntimeError('Missing Tesseract language data: ' + ', '.join(sorted(missing)) + '. Install English, Simplified Chinese and Traditional Chinese language packages.')
        process = subprocess.run([executable, str(image), 'stdout', '-l', '+'.join(LANGUAGES),
                                  '--oem', '1', '--psm', '3', 'tsv'], capture_output=True,
                                 text=True, encoding='utf-8', errors='strict', timeout=45, check=False)
        if process.returncode:
            raise RuntimeError('Local Tesseract OCR failed. Check language data and image readability; no fallback evidence was generated.')
        if len(process.stdout.encode('utf-8')) > 8 * 1024 * 1024:
            raise RuntimeError('Tesseract evidence exceeds the 8 MiB output limit; split the document.')
        return _lines_from_tsv(process.stdout, width, height)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError('Local Tesseract OCR timed out (10 seconds for language discovery, 45 seconds for recognition). Resize the scan and retry.') from exc
    except (OSError, UnicodeError, Image.DecompressionBombError) as exc:
        raise RuntimeError('Cannot decode the OCR image or start Tesseract. Check the image and local runtime installation.') from exc
