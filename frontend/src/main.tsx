import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Navigate, RouterProvider, createBrowserRouter } from "react-router-dom";

import { AppShell } from "./layout/AppShell";
import { AnalyticsPage } from "./pages/Analytics";
import { NarrativePage } from "./pages/Narrative";
import { ReconciliationPage } from "./pages/Reconciliation";
import { DayProvider } from "./state/DayContext";
import "./styles.css";

const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <Navigate to="/reconciliation" replace /> },
      { path: "reconciliation", element: <ReconciliationPage /> },
      { path: "analytics", element: <AnalyticsPage /> },
      { path: "narrative", element: <NarrativePage /> },
      { path: "*", element: <Navigate to="/reconciliation" replace /> },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {/* Above the router: the selected clinic-day persists across all three screens. */}
    <DayProvider>
      <RouterProvider router={router} />
    </DayProvider>
  </StrictMode>,
);
