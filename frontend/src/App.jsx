import { useAuth } from './AuthContext';
import { CacheProvider } from './CacheContext';
import LoginPage from './LoginPage';
import Layout from './Layout';

export default function App() {
  const { token } = useAuth();

  if (!token) {
    return <LoginPage />;
  }

  return (
    <CacheProvider>
      <Layout />
    </CacheProvider>
  );
}
