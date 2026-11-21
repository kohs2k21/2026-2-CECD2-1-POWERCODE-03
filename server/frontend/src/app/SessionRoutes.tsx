import type { ReactNode } from "react";
import { Link, Navigate, Outlet, useLocation } from "react-router-dom";
import { AuthPage } from "@/features/auth/AuthPage";
import { useAuthSession } from "@/services/auth/AuthSessionProvider";
import { HttpError } from "@/services/api/client";
import { Button } from "@/components/ui/button";
import { LoadingState } from "@/components/ui/feedback";
import { isMobileViewport } from "@/hooks/useIsMobile";
import { homePath, legacyRedirect, loginReturnPath, paths } from "./routes";

const CenteredState = ({ children, role }: { children: ReactNode; role?: "alert" }) => (
  <main role={role} className="flex min-h-screen flex-col items-center justify-center gap-4 bg-background p-6 text-center">
    {children}
  </main>
);

export const SessionGate = () => {
  const session = useAuthSession();
  const location = useLocation();
  if (session.checking)
    return (
      <CenteredState>
        <LoadingState>로그인 상태를 확인하는 중...</LoadingState>
      </CenteredState>
    );
  if (!session.hasToken)
    return (
      <Navigate
        to={paths.login}
        replace
        state={{ returnTo: session.explicitLogout ? undefined : location.pathname + location.search }}
      />
    );
  if (!session.user)
    return (
      <CenteredState role="alert">
        <h1 className="text-section-title">
          {session.error instanceof HttpError && session.error.status === 403
            ? "접근 권한을 확인할 수 없습니다"
            : "로그인 상태를 확인하지 못했습니다"}
        </h1>
        <div className="flex gap-3">
          <Button variant="outline" onClick={session.retry}>
            다시 확인
          </Button>
          <Button variant="ghost" onClick={session.logout}>
            로그아웃
          </Button>
        </div>
      </CenteredState>
    );
  return <Outlet />;
};

export const AdminGate = () => {
  const session = useAuthSession();
  return session.user?.userType === "admin" ? (
    <Outlet />
  ) : (
    <section role="alert" className="grid justify-items-start gap-3 py-10">
      <h1 className="text-page-title">관리자 권한이 필요합니다</h1>
      <p className="text-sm text-muted-foreground">운영 현황과 이상 분석을 이용할 수 있습니다.</p>
    </section>
  );
};

export const LoginRoute = () => {
  const session = useAuthSession();
  const location = useLocation();
  if (session.checking)
    return (
      <CenteredState>
        <LoadingState>로그인 상태를 확인하는 중...</LoadingState>
      </CenteredState>
    );
  // 돌아갈 주소가 없으면 첫 화면(휴대폰 폭 = 알림 페이지). 폭은 이동하는 그 순간 한 번만 본다.
  if (session.user)
    return <Navigate to={loginReturnPath(location.state?.returnTo, session.user.userType, homePath(isMobileViewport()))} replace />;
  return (
    <AuthPage
      onLoginSuccess={session.login}
      authError={
        session.hasToken && session.error ? "로그인 상태를 확인하지 못했습니다. 다시 확인해 주세요." : null
      }
      onRetryAuth={session.retry}
    />
  );
};

/** 기본 경로 `/`: 휴대폰 폭이면 알림 페이지, 그 밖은 운영 현황(navigation.md 기본 경로). */
export const HomeRedirect = () => <Navigate to={homePath(isMobileViewport())} replace />;

/** 폐지한 옛 주소를 새 주소로 replace 이동(navigation.md 옛 주소 넘김). */
export const LegacyRedirect = () => {
  const location = useLocation();
  return <Navigate to={legacyRedirect(location.pathname, location.search) ?? paths.operations} replace />;
};

export const NotFoundRoute = () => (
  <section className="grid justify-items-start gap-3 py-10">
    <h1 className="text-page-title">화면을 찾을 수 없습니다</h1>
    <Button asChild variant="outline">
      <Link to={paths.operations}>운영 현황으로 이동</Link>
    </Button>
  </section>
);
