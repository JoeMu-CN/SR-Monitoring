import {StrictMode} from 'react';
import {createRoot} from 'react-dom/client';
import App from './App.tsx';
import './index.css';

const storedTheme = localStorage.getItem('sr-theme') ?? 'system';
const useDark = storedTheme === 'dark'
  || (storedTheme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);
document.documentElement.classList.toggle('dark', useDark);
document.documentElement.classList.toggle('light', !useDark);
document.documentElement.classList.toggle('reduce-motion', localStorage.getItem('sr-reduce-motion') === 'true');

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
