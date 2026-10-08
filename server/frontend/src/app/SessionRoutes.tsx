import { Navigate, Outlet, useLocation } from "react-router-dom";
import { AuthPage } from "../features/auth/AuthPage";
import { useAuthSession } from "../services/auth/AuthSessionProvider";
import { HttpError } from "../services/api/client";
import { loginReturnPath } from "./routePaths";
export const SessionGate = () => {
  const session = useAuthSession();
  const location = useLocation();
  if (session.checking)
    return (
      <main className="auth-loading" aria-live="polite">
        <p>로그인 상태를 확인하는 중...</p>
      </main>
    );
  if (!session.hasToken)
    return (
      <Navigate
        to="/login"
        replace
        state={{
          returnTo: session.explicitLogout
            ? undefined
            : location.pathname + location.search,
        }}
      />
    );
  if (!session.user)
    return (
      <main className="route-state" role="alert">
        <h1>
          {session.error instanceof HttpError && session.error.status === 403
            ? "접근 권한을 확인할 수 없습니다"
            : "로그인 상태를 확인하지 못했습니다"}
        </h1>
        <button type="button" onClick={session.retry}>
          다시 확인
        </button>
        <button type="button" onClick={session.logout}>
          로그아웃
        </button>
      </main>
    );
  return <Outlet />;
};
export const AdminGate = () => {
  const session = useAuthSession();
  return session.user?.userType === "admin" ? (
    <Outlet />
  ) : (
    <section className="route-state" role="alert">
      <h1>관리자 권한이 필요합니다</h1>
      <p>운영 현황과 이상 분석을 이용할 수 있습니다.</p>
    </section>
  );
};
export const LoginRoute = () => {
  const session = useAuthSession();
  const location = useLocation();
  if (session.checking)
    return (
      <main className="auth-loading" aria-live="polite">
        <p>로그인 상태를 확인하는 중...</p>
      </main>
    );
  if (session.user)
    return (
      <Navigate
        to={loginReturnPath(location.state?.returnTo, session.user.userType)}
        replace
      />
    );
  return (
    <AuthPage
      onLoginSuccess={session.login}
      authError={
        session.hasToken && session.error
          ? "로그인 상태를 확인하지 못했습니다. 다시 확인해 주세요."
          : null
      }
      onRetryAuth={session.retry}
    />
  );
};
