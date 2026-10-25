import { useEffect, useLayoutEffect, useRef, type RefObject } from 'react';

type Anchor = { page: HTMLElement; x: number; y: number; viewportX: number; viewportY: number };
export function useDocumentGestures(viewport: RefObject<HTMLDivElement | null>, ready: boolean, zoom: number, fitPage: boolean, setZoom: (value: number) => void, setFitPage: (value: boolean) => void) {
  const current = useRef({ zoom, fitPage });
  current.current = { zoom, fitPage };
  const anchor = useRef<Anchor | null>(null);
  const captureAnchor = (clientX: number, clientY: number) => {
    const viewer = viewport.current;
    if (!viewer) return null;
    const pages = [...viewer.querySelectorAll<HTMLElement>('.source-page')].filter(page => page.querySelector('.source-image'));
    const page = pages.find(page => { const r = page.getBoundingClientRect(); return clientY >= r.top && clientY <= r.bottom; }) || pages.reduce<HTMLElement | null>((nearest, page) => {
      const distance = (node: HTMLElement) => Math.abs(node.getBoundingClientRect().top + node.clientHeight / 2 - clientY);
      return !nearest || distance(page) < distance(nearest) ? page : nearest;
    }, null);
    if (!page) return null;
    const rect = page.getBoundingClientRect(), box = viewer.getBoundingClientRect();
    return { page, x: Math.max(0, Math.min(1, (clientX - rect.left) / rect.width)), y: Math.max(0, Math.min(1, (clientY - rect.top) / rect.height)), viewportX: clientX - box.left, viewportY: clientY - box.top };
  };
  const changeZoom = (next: number, clientX?: number, clientY?: number) => {
    const viewer = viewport.current;
    if (!viewer) return;
    const value = Math.max(25, Math.min(300, Math.round(next)));
    if (!current.current.fitPage && current.current.zoom === value) return;
    const rect = viewer.getBoundingClientRect();
    anchor.current = captureAnchor(clientX ?? rect.left + viewer.clientWidth / 2, clientY ?? rect.top + viewer.clientHeight / 2);
    current.current = { zoom: value, fitPage: false };
    setFitPage(false);
    setZoom(value);
  };
  useLayoutEffect(() => {
    const viewer = viewport.current, point = anchor.current;
    if (!viewer || !point || !point.page.isConnected) return;
    const page = point.page.getBoundingClientRect(), box = viewer.getBoundingClientRect();
    viewer.scrollLeft += page.left + point.x * page.width - box.left - point.viewportX;
    viewer.scrollTop += page.top + point.y * page.height - box.top - point.viewportY;
    anchor.current = null;
  }, [zoom, fitPage]);
  useEffect(() => {
    const viewer = viewport.current;
    if (!ready || !viewer) return;
    const wheel = (event: WheelEvent) => {
      if (!event.ctrlKey && !event.metaKey) return; // Native two-axis scrolling stays native.
      const point = captureAnchor(event.clientX, event.clientY);
      if (!point) return;
      event.preventDefault();
      const canvas = point.page.closest<HTMLElement>('.source-canvas');
      const base = current.current.fitPage && canvas ? canvas.getBoundingClientRect().width / Math.max(1, viewer.clientWidth - 16) * 100 : current.current.zoom;
      const pixels = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? viewer.clientHeight : 1);
      changeZoom(base * Math.exp(-pixels * .002), event.clientX, event.clientY);
    };
    let drag: { id: number; x: number; y: number; left: number; top: number; moved: boolean } | null = null;
    let suppressClick = false;
    const down = (event: PointerEvent) => {
      suppressClick = false;
      if (event.button !== 0 || event.pointerType !== 'mouse') return;
      const target = event.target as HTMLElement;
      if (!target.closest('.source-canvas')) return;
      drag = { id: event.pointerId, x: event.clientX, y: event.clientY, left: viewer.scrollLeft, top: viewer.scrollTop, moved: false };
    };
    const move = (event: PointerEvent) => {
      if (!drag || event.pointerId !== drag.id) return;
      const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
      if (!drag.moved && Math.hypot(dx, dy) < 5) return;
      if (!drag.moved) { drag.moved = true; viewer.setPointerCapture(event.pointerId); viewer.classList.add('is-panning'); }
      event.preventDefault();
      viewer.scrollLeft = drag.left - dx;
      viewer.scrollTop = drag.top - dy;
    };
    const finish = (event: PointerEvent) => {
      if (!drag || event.pointerId !== drag.id) return;
      suppressClick = drag.moved && event.type !== 'pointercancel';
      if (viewer.hasPointerCapture(event.pointerId)) viewer.releasePointerCapture(event.pointerId);
      drag = null;
      viewer.classList.remove('is-panning');
    };
    const click = (event: MouseEvent) => {
      if (suppressClick && event.detail > 0) { event.preventDefault(); event.stopImmediatePropagation(); }
      suppressClick = false;
    };
    const nativeDrag = (event: DragEvent) => event.preventDefault();
    viewer.addEventListener('wheel', wheel, { passive: false });
    viewer.addEventListener('pointerdown', down);
    viewer.addEventListener('pointermove', move);
    viewer.addEventListener('pointerup', finish);
    viewer.addEventListener('pointercancel', finish);
    viewer.addEventListener('click', click, true);
    viewer.addEventListener('dragstart', nativeDrag);
    return () => {
      viewer.removeEventListener('wheel', wheel);
      viewer.removeEventListener('pointerdown', down);
      viewer.removeEventListener('pointermove', move);
      viewer.removeEventListener('pointerup', finish);
      viewer.removeEventListener('pointercancel', finish);
      viewer.removeEventListener('click', click, true);
      viewer.removeEventListener('dragstart', nativeDrag);
      viewer.classList.remove('is-panning');
    };
  }, [ready]);
  return changeZoom;
}
