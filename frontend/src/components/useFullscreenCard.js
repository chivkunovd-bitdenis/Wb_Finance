import { useLayoutEffect, useState } from 'react';

// Растягивает карточку таблицы на всё окно и возвращает обратно.
// Карточка остаётся на своём месте в дереве React: столбцы, данные и состояние не пересоздаются.
// В развёрнутом виде она становится fixed и начинается от реального бокового меню: от его правой границы
// или от нижней, если меню в мобильной раскладке — верхняя полоса. Всё остальное на экране делается inert,
// поэтому Tab и экранный диктор остаются внутри таблицы и меню. При сворачивании возвращаются прокрутка и фокус.
export default function useFullscreenCard() {
  // null, пока таблица свёрнута; иначе карточка, кнопка и позиции прокрутки, снятые до разворота.
  const [view, setView] = useState(null);

  useLayoutEffect(() => {
    if (!view) return undefined;
    const { card, button, scroll } = view;
    const layout = card.closest('.layout');
    const saved = new Map();
    const isolate = () => isolateBehindCard(card, saved);
    const place = () => placeOverSidebar(card);

    const onKeyDown = (event) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      setView(null);
    };

    place();
    isolate();
    const isolationObserver = new MutationObserver(isolate);
    const sidebarObserver = new ResizeObserver(place);
    if (layout) {
      isolationObserver.observe(layout, { childList: true, subtree: true });
      const sidebar = layout.querySelector(':scope > .sidebar');
      if (sidebar) sidebarObserver.observe(sidebar);
    }
    window.addEventListener('resize', place);
    document.addEventListener('keydown', onKeyDown, true);

    return () => {
      document.removeEventListener('keydown', onKeyDown, true);
      window.removeEventListener('resize', place);
      sidebarObserver.disconnect();
      isolationObserver.disconnect();
      card.style.left = '';
      card.style.top = '';
      for (const [element, wasInert] of saved) element.inert = wasInert;
      saved.clear();

      requestAnimationFrame(() => {
        restoreScrollPositions(scroll);
        if (card.isConnected && button.isConnected) button.focus();
      });
    };
  }, [view]);

  function toggle(event) {
    if (view) {
      setView(null);
      return;
    }
    // Позицию прокрутки снимаем до переключения класса: fixed-карточка меняет высоту страницы.
    const button = event.currentTarget;
    const card = button.closest('.table-card');
    if (card) setView({ card, button, scroll: captureScrollPositions(card) });
  }

  return {
    expanded: view !== null,
    toggle,
    cardClassName: view ? 'table-card table-card-expanded' : 'table-card',
  };
}

function placeOverSidebar(card) {
  const sidebar = card.closest('.layout')?.querySelector(':scope > .sidebar');
  const rect = sidebar ? sidebar.getBoundingClientRect() : null;
  const isTopBar = Boolean(rect) && rect.width >= window.innerWidth * 0.7 && rect.height < window.innerHeight * 0.75;
  card.style.left = `${rect && !isTopBar ? rect.right : 0}px`;
  card.style.top = `${rect && isTopBar ? rect.bottom : 0}px`;
}

// Делает inert все соседние ветки от карточки до корня раскладки. Боковое меню — прямой ребёнок корня,
// поэтому до него этот обход не доходит и остаётся доступным.
function isolateBehindCard(card, saved) {
  if (!card.closest('.layout')) return;
  let node = card;
  while (node.parentElement && !node.parentElement.classList.contains('layout')) {
    const parent = node.parentElement;
    for (const sibling of parent.children) {
      if (sibling === node) continue;
      if (!saved.has(sibling)) saved.set(sibling, sibling.inert);
      sibling.inert = true;
    }
    node = parent;
  }
}

function captureScrollPositions(card) {
  const boundary = card.closest('.layout') ?? document.body;
  const scrollers = [];
  for (let node = card.parentElement; node && node !== boundary; node = node.parentElement) scrollers.push(node);
  const tableWrap = card.querySelector('.table-wrap');
  if (tableWrap) scrollers.push(tableWrap);
  return {
    windowX: window.scrollX,
    windowY: window.scrollY,
    positions: scrollers.map((element) => ({ element, top: element.scrollTop, left: element.scrollLeft })),
  };
}

function restoreScrollPositions(snapshot) {
  for (const { element, top, left } of snapshot.positions) {
    if (!element.isConnected) continue;
    element.scrollTop = top;
    element.scrollLeft = left;
  }
  window.scrollTo(snapshot.windowX, snapshot.windowY);
}
