import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { App } from './App';
import { bootstrapOriginStateBridge } from './app/origin-state-bridge';
import { purgeRemovedModuleData } from './lib/storage/compat';
import './index.css';
import './styles.css';
import './styles.practice-tune.css';
import './styles.shadcn-unified.css';
import './styles.typography-shadcn.css';

if (typeof document !== 'undefined') {
  document.documentElement.classList.add('dark');
}

const redirectedByOriginBridge = bootstrapOriginStateBridge();
if (!redirectedByOriginBridge) {
  void purgeRemovedModuleData();

  ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
    <React.StrictMode>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </React.StrictMode>
  );
}
