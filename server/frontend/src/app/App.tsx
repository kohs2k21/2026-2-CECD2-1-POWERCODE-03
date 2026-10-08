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
import { CreatePage } from "../features/detection/DetectionPages";
import { EvaluationPage } from "../features/detection/pages/EvaluationPage";
import { VersionsPage } from "../features/detection/pages/VersionsPage";
import { CollectionPage } from "../features/detection/pages/CollectionPage";

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
                    element: <EvaluationPage />,
                  },
                  {
                    path: "/detection/versions",
                    element: <VersionsPage />,
                  },
                  {
                    path: "/detection/collection",
                    element: <CollectionPage />,
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
