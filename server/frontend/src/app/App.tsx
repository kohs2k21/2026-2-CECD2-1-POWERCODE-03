import {
  createBrowserRouter,
  Navigate,
  RouterProvider,
} from "react-router-dom";
import { SessionGate, AdminGate, LoginRoute } from "./SessionRoutes";
import { AppLayout } from "./AppLayout";
import {
  OperationsRoute,
  AnalysisRoute,
  SettingsRoute,
  NotFoundRoute,
} from "./WorkspaceRoutes";
import { DetectionLayout } from "../features/detection/DetectionLayout";
import {
  CreatePage,
  DetectionPage,
} from "../features/detection/DetectionPages";

const router = createBrowserRouter([
  { path: "/login", element: <LoginRoute /> },
  {
    element: <SessionGate />,
    children: [
      {
        element: <AppLayout />,
        children: [
          { path: "/", element: <Navigate to="/operations" replace /> },
          { path: "/operations", element: <OperationsRoute /> },
          { path: "/analysis", element: <AnalysisRoute /> },
          { path: "/settings", element: <SettingsRoute /> },
          {
            element: <AdminGate />,
            children: [
              {
                element: <DetectionLayout />,
                children: [
                  {
                    path: "/detection",
                    element: (
                      <Navigate
                        to="/detection/create?tab=data-features"
                        replace
                      />
                    ),
                  },
                  { path: "/detection/create", element: <CreatePage /> },
                  {
                    path: "/detection/evaluation",
                    element: <DetectionPage title="성능 평가·비교" />,
                  },
                  {
                    path: "/detection/versions",
                    element: <DetectionPage title="운영 버전 관리" />,
                  },
                  {
                    path: "/detection/collection",
                    element: <DetectionPage title="데이터 수집 현황" />,
                  },
                ],
              },
            ],
          },
          { path: "*", element: <NotFoundRoute /> },
        ],
      },
    ],
  },
]);

if (import.meta.hot) import.meta.hot.dispose(() => router.dispose());

export const App = () => <RouterProvider router={router} />;
