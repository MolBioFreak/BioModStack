import '../polyfills';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '../index.css';
import { ShareGuestApp } from './ShareGuestApp';

// The link key is the URL fragment; nothing else is read from the location.
createRoot(document.getElementById('root')!).render(
    <StrictMode>
        <ShareGuestApp />
    </StrictMode>,
);
