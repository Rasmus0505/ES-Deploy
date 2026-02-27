import { useEffect } from 'react';
import { ListeningUploadHistoryEntryBridge } from './ListeningUploadHistoryEntryBridge';

const HISTORY_LAYOUT_STYLE_ID = 'listening-history-layout-bridge-style';
const HISTORY_LAYOUT_STYLE = `
.history-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

@media (max-width: 1199px) {
  .history-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 760px) {
  .history-grid {
    grid-template-columns: 1fr;
  }
}

.history-item,
.history-item__content {
  min-width: 0;
}

.history-item__title {
  display: block;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
`;

function syncHistoryTitleTooltips() {
  const titles = Array.from(document.querySelectorAll<HTMLElement>('.history-item__title'));
  titles.forEach((title) => {
    const text = title.textContent?.trim() || '';
    if (text) {
      title.setAttribute('title', text);
      return;
    }
    title.removeAttribute('title');
  });
}

export function ListeningUploadLayoutBridge() {
  useEffect(() => {
    let styleElement = document.getElementById(HISTORY_LAYOUT_STYLE_ID) as HTMLStyleElement | null;
    if (!styleElement) {
      styleElement = document.createElement('style');
      styleElement.id = HISTORY_LAYOUT_STYLE_ID;
      styleElement.textContent = HISTORY_LAYOUT_STYLE;
      document.head.appendChild(styleElement);
    }

    return () => {
      const existing = document.getElementById(HISTORY_LAYOUT_STYLE_ID);
      if (existing) existing.remove();
    };
  }, []);

  useEffect(() => {
    syncHistoryTitleTooltips();

    const observer = new MutationObserver(() => {
      syncHistoryTitleTooltips();
    });
    observer.observe(document.body, { childList: true, subtree: true });

    return () => observer.disconnect();
  }, []);

  return <ListeningUploadHistoryEntryBridge />;
}
