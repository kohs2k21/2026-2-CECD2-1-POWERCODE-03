import {
  useNavigate,
  useOutletContext,
  useSearchParams,
} from "react-router-dom";
import { AnalysisWorkspace } from "../features/analysis/AnalysisWorkspace";
import { HomeMock } from "../features/home/HomeMock";
import { SettingsPlaceholder } from "../features/settings/SettingsPlaceholder";
import { useAuthSession } from "../services/auth/AuthSessionProvider";
import { settingsReturnPath, viewPaths } from "./routePaths";

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
  const navigate = useNavigate();
  return (
    <div className="settings-route">
      <button
        type="button"
        className="settings-return"
        onClick={() => navigate(settingsReturnPath(params.get("returnTo")))}
      >
        이전 화면으로 돌아가기
      </button>
      <SettingsPlaceholder currentUser={useAuthSession().user!} />
    </div>
  );
};
export const NotFoundRoute = () => (
  <section className="route-state">
    <h1>화면을 찾을 수 없습니다</h1>
    <a href="/operations">운영 현황으로 이동</a>
  </section>
);
