import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import TableExpandMockup from './TableExpandMockup.jsx';
import './table-expand.css';

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <TableExpandMockup />
  </StrictMode>,
);
