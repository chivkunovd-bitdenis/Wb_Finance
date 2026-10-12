import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

const baselineOnly = new URLSearchParams(window.location.search).get('baseline') === '1';

function findTarget(marketplace) {
  if (marketplace === 'wb') {
    const heading = [...document.querySelectorAll('.content .table-card > .table-head-row h3')]
      .find((element) => element.textContent?.trim() === 'Детализация по дням');
    const card = heading?.closest('.table-card');
    return { card, actions: card?.querySelector('.table-head-row > div') };
  }

  const table = document.querySelector('[data-testid="marketplace-daily-table"]');
  const card = table?.closest('.table-card');
  return { card, actions: card?.querySelector('.table-head-row > div') };
}

function paintDataRows(card, marketplace) {
  if (!card) return;
  const rows = marketplace === 'wb'
    ? [...card.querySelectorAll('tbody tr')].filter((row) => {
      const label = row.querySelector('td.left')?.textContent?.trim() || '';
      return /^\d{2}\.\d{2}$/.test(label);
    })
    : [...card.querySelectorAll('tbody tr[data-testid^="marketplace-day-"]')];

  rows.forEach((row, index) => {
    row.classList.add('prototype-day-row');
    row.classList.toggle('prototype-pair-strong', index % 4 >= 2);
  });
}

function hideSiblings(card, content, savedState) {
  const rememberAndHide = (element) => {
    if (!savedState.has(element)) {
      savedState.set(element, {
        ariaHidden: element.getAttribute('aria-hidden'),
        inert: Boolean(element.inert),
      });
    }
    element.setAttribute('aria-hidden', 'true');
    element.inert = true;
  };

  const topbar = document.querySelector('.main > .topbar');
  if (topbar) rememberAndHide(topbar);

  if (!content?.contains(card)) return;
  let branch = card;
  while (branch && branch !== content) {
    const parent = branch.parentElement;
    if (!parent) break;
    [...parent.children].forEach((sibling) => {
      if (sibling !== branch) rememberAndHide(sibling);
    });
    if (parent === content) break;
    branch = parent;
  }
}

function restoreSiblings(savedState) {
  for (const [element, previous] of savedState.entries()) {
    element.inert = previous.inert;
    if (previous.ariaHidden === null) element.removeAttribute('aria-hidden');
    else element.setAttribute('aria-hidden', previous.ariaHidden);
  }
  savedState.clear();
}

function setExpandedGeometry(card) {
  const sidebar = document.querySelector('.layout > .sidebar');
  const rect = sidebar?.getBoundingClientRect();
  const horizontalMenu = rect
    && rect.width >= window.innerWidth * 0.7
    && rect.height < window.innerHeight * 0.75;
  const left = horizontalMenu ? 0 : (rect?.right || 0);
  const top = horizontalMenu ? (rect?.bottom || 0) : 0;

  card.style.setProperty('--prototype-overlay-left', `${left}px`);
  card.style.setProperty('--prototype-overlay-top', `${top}px`);
  card.classList.add('prototype-table-expanded');
}

export default function TableExpansionEnhancement() {
  const [portalTarget, setPortalTarget] = useState(null);
  const [marketplace, setMarketplace] = useState('wb');
  const [expanded, setExpanded] = useState(false);
  const targetCardRef = useRef(null);
  const buttonRef = useRef(null);
  const savedInertStateRef = useRef(new Map());
  const scrollSnapshotRef = useRef(null);
  const expandedCardRef = useRef(null);

  useEffect(() => {
    if (baselineOnly) return undefined;

    const syncTarget = () => {
      const selector = document.querySelector('[data-testid="marketplace-selector"]');
      const selectedMarketplace = selector?.value || 'wb';
      const next = findTarget(selectedMarketplace);
      const previousCard = targetCardRef.current;

      setMarketplace((current) => (current === selectedMarketplace ? current : selectedMarketplace));
      targetCardRef.current = next.card || null;
      setPortalTarget((current) => (current === next.actions ? current : next.actions || null));

      if (expanded && previousCard !== next.card) setExpanded(false);
      if (next.card) paintDataRows(next.card, selectedMarketplace);
    };

    const observer = new MutationObserver(syncTarget);
    observer.observe(document.body, { childList: true, subtree: true });
    document.addEventListener('change', syncTarget);
    window.addEventListener('resize', syncTarget);
    syncTarget();

    return () => {
      observer.disconnect();
      document.removeEventListener('change', syncTarget);
      window.removeEventListener('resize', syncTarget);
    };
  }, [expanded]);

  useLayoutEffect(() => {
    if (baselineOnly) return undefined;
    const card = targetCardRef.current;
    if (!card) return undefined;

    card.classList.remove('prototype-marketplace-wb', 'prototype-marketplace-ozon');
    if (marketplace === 'wb') card.classList.add('prototype-marketplace-wb');
    if (marketplace === 'ozon') card.classList.add('prototype-marketplace-ozon');
    paintDataRows(card, marketplace);

    return () => {
      card.classList.remove('prototype-marketplace-wb', 'prototype-marketplace-ozon');
    };
  }, [marketplace, portalTarget]);

  useLayoutEffect(() => {
    if (baselineOnly || !expanded) return undefined;
    const card = targetCardRef.current;
    const content = document.querySelector('.main > .content');
    if (!card || !content) return undefined;

    const scrollArea = card.querySelector('.table-wrap');
    const expandButton = buttonRef.current;
    const savedInertState = savedInertStateRef.current;
    expandedCardRef.current = card;
    scrollSnapshotRef.current = {
      contentScrollTop: content.scrollTop,
      windowScrollX: window.scrollX,
      windowScrollY: window.scrollY,
      tableScrollTop: scrollArea?.scrollTop || 0,
      tableScrollLeft: scrollArea?.scrollLeft || 0,
    };

    const refreshOverlay = () => setExpandedGeometry(card);
    const isolateUnderlyingContent = () => hideSiblings(card, content, savedInertStateRef.current);
    refreshOverlay();
    isolateUnderlyingContent();
    document.body.classList.add('prototype-lock-scroll');

    const contentObserver = new MutationObserver(isolateUnderlyingContent);
    contentObserver.observe(content, { childList: true, subtree: true });
    const sidebarObserver = new ResizeObserver(refreshOverlay);
    const sidebar = document.querySelector('.layout > .sidebar');
    if (sidebar) sidebarObserver.observe(sidebar);
    window.addEventListener('resize', refreshOverlay);

    const onKeyDown = (event) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      setExpanded(false);
    };
    document.addEventListener('keydown', onKeyDown, true);

    return () => {
      document.removeEventListener('keydown', onKeyDown, true);
      window.removeEventListener('resize', refreshOverlay);
      contentObserver.disconnect();
      sidebarObserver.disconnect();
      card.classList.remove('prototype-table-expanded');
      card.style.removeProperty('--prototype-overlay-left');
      card.style.removeProperty('--prototype-overlay-top');
      document.body.classList.remove('prototype-lock-scroll');
      restoreSiblings(savedInertState);

      const snapshot = scrollSnapshotRef.current;
      requestAnimationFrame(() => {
        if (snapshot) {
          content.scrollTop = snapshot.contentScrollTop;
          window.scrollTo(snapshot.windowScrollX, snapshot.windowScrollY);
          if (scrollArea?.isConnected) {
            scrollArea.scrollTop = snapshot.tableScrollTop;
            scrollArea.scrollLeft = snapshot.tableScrollLeft;
          }
        }
        if (targetCardRef.current === card && card.isConnected && expandButton?.isConnected) expandButton.focus();
      });
    };
  }, [expanded]);

  if (baselineOnly || !portalTarget) return null;

  const button = (
    <button
      ref={buttonRef}
      type="button"
      className="btn-outline prototype-table-expand"
      aria-label={expanded ? 'Свернуть таблицу' : 'Развернуть таблицу'}
      aria-expanded={expanded}
      title={expanded ? 'Свернуть таблицу' : 'Развернуть таблицу'}
      onClick={() => setExpanded((current) => !current)}
    >
      <span className="prototype-expand-icon" aria-hidden="true">⤢</span>
      <span className="prototype-expand-label">{expanded ? 'Свернуть' : 'Развернуть'}</span>
    </button>
  );

  return createPortal(button, portalTarget);
}
