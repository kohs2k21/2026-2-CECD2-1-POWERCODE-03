import {
  useNavigate,
  useOutletContext,
  useSearchParams,
  Link,
} from "react-router-dom";
import { AnalysisWorkspace } from "../features/analysis/AnalysisWorkspace";
import { HomeMock } from "../features/home/HomeMock";
import { SettingsPlaceholder } from "../features/settings/SettingsPlaceholder";
import { useAuthSession } from "../services/auth/AuthSessionProvider";
import { settingsReturnPath, viewPaths } from "./routePaths";
import { Button } from "../components/ui/button";

export const OperationsRoute = () => {
  const session = useAuthSession();
  const navigate = useNavigate();
  return (
    <HomeMock
      role={session.user!.userType}
      onSelectView={(view) => navigate(viewPaths[view])}
    />
  );
};
export const AnalysisRoute = () => {
  const { requestLogout } = useOutletContext<{ requestLogout: () => void }>();
  return <AnalysisWorkspace onLogout={requestLogout} />;
};
export const SettingsRoute = () => {
  const [params] = useSearchParams();
  return (
    <div className="settings-route">
      <Button asChild variant="outline" className="settings-return">
        <Link to={settingsReturnPath(params.get("returnTo"))}>
          이전 화면으로 돌아가기
        </Link>
      </Button>
      <SettingsPlaceholder currentUser={useAuthSession().user!} />
    </div>
  );
};
export const NotFoundRoute = () => (
  <section className="route-state">
    <h1>화면을 찾을 수 없습니다</h1>
    <Button asChild variant="outline">
      <Link to="/operations">운영 현황으로 이동</Link>
    </Button>
  </section>
);
