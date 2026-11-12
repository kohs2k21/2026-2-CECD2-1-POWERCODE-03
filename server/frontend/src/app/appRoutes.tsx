import { Navigate, type RouteObject } from "react-router-dom";
import { AnalysisPage } from "@/features/analysis/AnalysisPage";
import { DetectionLayout } from "@/features/detection/DetectionLayout";
import { ModelVersionDetailPage } from "@/features/detection/models/VersionDetail";
import { EvaluationDetailPage } from "@/features/detection/evaluations/EvaluationDetail";
import { JobDetailPage } from "@/features/detection/pages/DetailPages";
import { EvaluationsPage } from "@/features/detection/pages/EvaluationsPage";
import { ModelsPage } from "@/features/detection/pages/ModelsPage";
import { OperationsSettingsPage } from "@/features/detection/pages/OperationsSettingsPage";
import { ReplacePage } from "@/features/detection/pages/ReplacePage";
import { RulesPage } from "@/features/detection/pages/RulesPage";
import { CandidateDetailPage } from "@/features/detection/rules/candidates/CandidateDetailPage";
import { CandidateListPage } from "@/features/detection/rules/candidates/CandidateListPage";
import { OperationsPage } from "@/features/operations/OperationsPage";
import { SettingsPage } from "@/features/settings/SettingsPage";
import { AppLayout } from "./AppLayout";
import { paths } from "./routes";
import { AdminGate, LegacyRedirect, LoginRoute, NotFoundRoute, SessionGate } from "./SessionRoutes";

/** 폐지한 옛 주소(navigation.md). 새 주소로 replace 이동. */
export const legacyPaths = [
  "/notifications",
  "/analysis/process-decisions",
  "/detection",
  "/detection/pipeline",
  "/detection/create",
  "/detection/evaluation",
  "/detection/versions",
  "/detection/collection",
] as const;

export const appRoutes: RouteObject[] = [
  { path: paths.login, element: <LoginRoute /> },
  {
    element: <SessionGate />,
    children: [
      {
        element: <AppLayout />,
        children: [
          { path: "/", element: <Navigate to={paths.operations} replace /> },
          { path: paths.operations, element: <OperationsPage /> },
          { path: paths.analysis, element: <AnalysisPage /> },
          { path: "/analysis/d/:decisionId", element: <AnalysisPage /> },
          { path: paths.analysisIncidents, element: <AnalysisPage /> },
          { path: "/analysis/incidents/:incidentId", element: <AnalysisPage /> },
          { path: paths.settings, element: <SettingsPage /> },
          ...legacyPaths.map((path) => ({ path, element: <LegacyRedirect /> })),
          {
            element: <AdminGate />,
            children: [
              {
                element: <DetectionLayout />,
                children: [
                  { path: paths.detectionOperations, element: <OperationsSettingsPage /> },
                  { path: paths.detectionReplace, element: <ReplacePage /> },
                  { path: "/detection/operations/jobs/:jobId", element: <JobDetailPage /> },
                  { path: paths.detectionRules, element: <RulesPage /> },
                  { path: paths.detectionRuleCandidates, element: <CandidateListPage /> },
                  { path: "/detection/rules/candidates/:candidateId", element: <CandidateDetailPage /> },
                  { path: paths.detectionModels, element: <ModelsPage /> },
                  { path: "/detection/models/:versionId", element: <ModelVersionDetailPage /> },
                  { path: paths.detectionEvaluations, element: <EvaluationsPage /> },
                  { path: "/detection/evaluations/:evaluationId", element: <EvaluationDetailPage /> },
                ],
              },
            ],
          },
          { path: "*", element: <NotFoundRoute /> },
        ],
      },
    ],
  },
];
