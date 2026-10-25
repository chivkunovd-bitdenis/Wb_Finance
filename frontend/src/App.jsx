import { useLocation } from 'react-router-dom';
import { useAuth } from './AuthContext';
import { CacheProvider } from './CacheContext';
import { StoreProvider } from './StoreContext';
import LoginPage from './LoginPage';
import InvitePage from './InvitePage';
import Layout from './Layout';

// Ссылка приглашения сотрудника: /invite/<token>. Открывается и без входа, и после входа.
function inviteTokenFromPath(pathname) {
  const match = /^\/invite\/([^/]+)\/?$/.exec(pathname || '');
  if (!match) return null;
  try {
    return decodeURIComponent(match[1]);
  } catch {
    return match[1];
  }
}

export default function App() {
  const { token } = useAuth();
  const { pathname } = useLocation();
  const inviteToken = inviteTokenFromPath(pathname);

  if (!token) {
    if (inviteToken) {
      return <InvitePage key={inviteToken} token={inviteToken} />;
    }
    return <LoginPage />;
  }

  if (inviteToken) {
    return (
      <StoreProvider>
        <InvitePage key={inviteToken} token={inviteToken} />
      </StoreProvider>
    );
  }

  return (
    <StoreProvider>
      <CacheProvider>
        <Layout />
      </CacheProvider>
    </StoreProvider>
  );
}
