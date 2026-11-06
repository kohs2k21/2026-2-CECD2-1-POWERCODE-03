import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { appRoutes } from "./appRoutes";

const router = createBrowserRouter(appRoutes);
if (import.meta.hot) import.meta.hot.dispose(() => router.dispose());

export const App = () => <RouterProvider router={router} />;
