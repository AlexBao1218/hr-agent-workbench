from pathlib import Path
import hashlib
import mimetypes
import json
import signal
import shutil
import sys
import os
import subprocess
import tempfile
import zipfile
import pymupdf as fitz
from PIL import Image, ImageOps

MAX_BYTES = 30 * 1024 * 1024
MAX_PAGES = 40
MAX_PIXELS = 24_000_000
BUNDLED_SOFFICE = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/override/soffice'


def _box(rect, width, height):
    x0, y0, x1, y1 = rect
    x0, x1 = max(0, min(1, x0 / width)), max(0, min(1, x1 / width))
    y0, y1 = max(0, min(1, y0 / height)), max(0, min(1, y1 / height))
    return dict(x=x0, y=y0, width=max(0, x1-x0), height=max(0, y1-y0))


def _add(result, page, text, box, method='native', confidence=None):
    if not text.strip():
        return
    digest = hashlib.sha256(f'{page}:{len(result["blocks"])}:{text}'.encode()).hexdigest()[:16]
    result['blocks'].append(dict(id=f'{result["id"]}-p{page}-{digest}', page=page,
                                 text=text, box=box, method=method, confidence=confidence))


def _overlap_fraction(a, b):
    """Intersection relative to smaller box, for native/OCR duplicate lines."""
    overlap = max(0, min(a['x']+a['width'], b['x']+b['width'])-max(a['x'], b['x'])) * max(0, min(a['y']+a['height'], b['y']+b['height'])-max(a['y'], b['y']))
    area = min(a['width']*a['height'], b['width']*b['height'])
    return overlap / area if area else 0


def _ocr(result, image, number, native_blocks=()):
    try:
        from .ocr import ocr_image
        lines = ocr_image(image)
        if not lines:
            raise RuntimeError('No readable text recognized; provide a clearer scan or a text PDF.')
        added = []
        for line in lines:
            normalized = ''.join(line['text'].split()).casefold()
            if any(normalized == ''.join(b['text'].split()).casefold()
                   and b['box'] and _overlap_fraction(line['box'], b['box']) >= .5
                   for b in native_blocks):
                continue
            _add(result, number, line['text'], line['box'], 'ocr', line['confidence'])
            added.append(result['blocks'][-1])
        return added
    except Exception as exc:
        result['status'] = 'needs_ocr'
        result['warnings'].append(f'Page {number}: local OCR unavailable or unsuccessful: {exc}')
        return []


def _pdf(path, out, result):
    with fitz.open(path) as doc:
        if doc.needs_pass:
            raise ValueError('Password-protected PDF: upload an unlocked copy.')
        if not 0 < len(doc) <= MAX_PAGES:
            raise ValueError(f'PDF must contain 1–{MAX_PAGES} pages.')
        for number, page in enumerate(doc, 1):
            width, height = page.rect.width, page.rect.height
            if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
                raise ValueError('PDF page dimensions exceed safe limits.')
            scale = min(2.0, (MAX_PIXELS / (width * height)) ** .5)
            image = out / f'page-{number}.png'
            page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).save(image)
            result['pages'].append(dict(number=number, width=width, height=height, imagePath=str(image)))
            blocks = page.get_text('dict', flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES, sort=True)['blocks']
            before = len(result['blocks'])
            for block in blocks:
                for line in block.get('lines', []):
                    text = ''.join(span['text'] for span in line['spans'])
                    rect = fitz.Rect(line['bbox']) * page.rotation_matrix
                    _add(result, number, text, _box(rect, width, height))
            native = result['blocks'][before:]
            if not native:
                _ocr(result, image, number)
            elif sum(len(''.join(b['text'].split())) for b in native) < 400:
                # Scope: sparse native headers over a substantial raster body.
                # get_image_info includes inline images and avoids loading pixels.
                large_images = []
                for info in page.get_image_info():
                    rect = (fitz.Rect(info['bbox']) * page.rotation_matrix) & page.rect
                    if rect.get_area() / (width * height) >= .30:
                        large_images.append(_box(rect, width, height))
                if large_images:
                    added = _ocr(result, image, number, native_blocks=native)
                    result['warnings'].append(f'Page {number}: sparse native text with a large embedded image; supplemental whole-page local OCR used, native evidence retained.')
                    if not any(_overlap_fraction(b['box'], region) >= .5
                               for b in added for region in large_images):
                        result['status'] = 'needs_ocr'
                        result['warnings'].append(f'Page {number}: embedded image yielded no additional readable evidence. Check the image or upload a clearer scan; native header alone is incomplete.')


def _renderer_path():
    configured = os.environ.get('HR_DOCUMENTS_SOFFICE')
    if configured:
        return Path(configured)
    if sys.platform.startswith('linux'):
        return Path(shutil.which('soffice') or '/usr/bin/soffice')
    return BUNDLED_SOFFICE


def _docx(path, out, result):
    with zipfile.ZipFile(path) as archive:
        if sum(i.file_size for i in archive.infolist()) > MAX_BYTES * 4:
            raise ValueError('Expanded DOCX exceeds 120 MB limit.')
        if 'word/document.xml' not in archive.namelist():
            raise ValueError('Invalid DOCX package.')
    renderer = _renderer_path()
    if renderer.is_file():
        try:
            with tempfile.TemporaryDirectory(prefix='docx-', dir=out) as temp:
                temp = Path(temp)
                # Separate profile prevents interference with another document worker.
                cmd = [str(renderer), f'-env:UserInstallation={(temp / "profile").as_uri()}', '--headless', '--convert-to', 'pdf', '--outdir', str(temp), str(path)]
                subprocess.run(cmd, capture_output=True, timeout=60, check=True)
                converted = temp / (path.stem + '.pdf')
                if not converted.is_file():
                    raise RuntimeError('renderer did not produce PDF')
                _pdf(converted, out, result)
                result['previewKind'] = 'rendered'
                return
        except Exception as exc:
            result['pages'].clear()
            result['blocks'].clear()
            result['status'] = 'ready'
            result['warnings'].append(f'DOCX renderer failed ({type(exc).__name__}); showing extracted text only.')
    else:
        result['warnings'].append('DOCX layout renderer unavailable. Configure HR_DOCUMENTS_SOFFICE with a headless LibreOffice binary (Linux: install libreoffice-writer; Mac: use the bundled renderer); showing extracted text only.')
    from docx import Document
    document = Document(path)
    result['previewKind'] = 'text'
    result['pages'] = [dict(number=1, width=1, height=1, imagePath=None)]
    # XML paragraph order retains paragraphs inside tables; no invented pagination or coordinates.
    for paragraph in document.element.body.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p'):
        text = ''.join(node.text or '' for node in paragraph.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t'))
        _add(result, 1, text, None)


def _parse_document(input_path: str | Path, output_dir: str | Path, document_id: str) -> dict:
    """Return the exact DOCUMENTS.txt plain-dict boundary, including recoverable failures."""
    path = Path(input_path).resolve()
    result = dict(id=document_id, filename=path.name, mimeType=mimetypes.guess_type(path.name)[0] or 'application/octet-stream', status='ready', previewKind='original', pages=[], blocks=[], warnings=[], text='')
    try:
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError('Input file is missing or empty.')
        if path.stat().st_size > MAX_BYTES:
            raise ValueError('Document exceeds 30 MB limit.')
        # Content-derived directory avoids collisions and never trusts document_id as a path.
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:20]
        out = Path(output_dir).resolve() / digest
        out.mkdir(parents=True, exist_ok=True)
        suffix = path.suffix.lower()
        if suffix == '.pdf':
            _pdf(path, out, result)
        elif suffix == '.docx':
            _docx(path, out, result)
        elif suffix in ('.png', '.jpg', '.jpeg', '.tif', '.tiff', '.webp'):
            with Image.open(path) as original:
                if getattr(original, 'n_frames', 1) > 1:
                    raise ValueError('Multi-frame images are unsupported; export each page or use PDF.')
                if original.width * original.height > MAX_PIXELS:
                    raise ValueError('Image exceeds 24 megapixels; resize before upload.')
                image = ImageOps.exif_transpose(original).convert('RGB')
                target = out / 'page-1.png'
                image.save(target)
                result['pages'].append(dict(number=1, width=image.width, height=image.height, imagePath=str(target)))
                _ocr(result, target, 1)
        else:
            raise ValueError('Unsupported type. Use PDF, DOCX, PNG, JPEG, TIFF or WebP.')
        if not result['blocks'] and result['status'] == 'ready':
            raise ValueError('Document contains no readable text.')
    except Exception as exc:
        result['status'] = 'failed'
        result['warnings'].append(f'{type(exc).__name__}: {exc}')
    result['text'] = '\n'.join(block['text'] for block in result['blocks'])
    return result


PARSE_TIMEOUT = 120


def parse_document(input_path: str | Path, output_dir: str | Path, document_id: str) -> dict:
    """Isolate native readers and bound the full operation to 120 seconds.

    Arguments are data, never shell strings. A timeout kills this worker's process
    group including any local OCR/renderer child, leaving other workers untouched.
    """
    process = None
    try:
        process = subprocess.Popen(
            [sys.executable, '-m', 'hr_documents.worker', str(input_path), str(output_dir), document_id],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True,
        )
        stdout, stderr = process.communicate(timeout=PARSE_TIMEOUT)
        if process.returncode:
            raise RuntimeError('Local document worker failed; check the installed hr-documents package and file readability.')
        return json.loads(stdout)
    except subprocess.TimeoutExpired:
        if process is not None:
            if os.name == 'posix':
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.communicate()
        warning = f'Document parsing exceeded {PARSE_TIMEOUT} seconds. Reduce page count or image size and retry.'
    except Exception as exc:
        warning = f'{type(exc).__name__}: {exc}'
    path = Path(input_path)
    return dict(id=document_id, filename=path.name,
                mimeType=mimetypes.guess_type(path.name)[0] or 'application/octet-stream',
                status='failed', previewKind='original', pages=[], blocks=[], warnings=[warning], text='')
