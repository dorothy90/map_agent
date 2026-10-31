import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import './styles.css';
import { isHermesOptIn } from './web/opt-in';
if (isHermesOptIn(window.location.search)) {
  void import('./web/entry').then(entry => entry.mount());
} else {
createRoot(document.getElementById('root')!).render(<StrictMode><App/></StrictMode>);
}
