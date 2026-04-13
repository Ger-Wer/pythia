import { createBrowserRouter } from "react-router-dom";
import { Layout } from "../components/Layout";
import { LatestRunPage } from "../pages/LatestRunPage";
import { LoginPage } from "../pages/LoginPage";
import { MarketDetailPage } from "../pages/MarketDetailPage";
import { OperationsPage } from "../pages/OperationsPage";
import { OperatorVsShadowPage } from "../pages/OperatorVsShadowPage";
import { OverviewPage } from "../pages/OverviewPage";
import { QualityPage } from "../pages/QualityPage";
import { ShadowPerformancePage } from "../pages/ShadowPerformancePage";
import { WatchlistPage } from "../pages/WatchlistPage";

export const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  {
    path: "/",
    element: <Layout />,
    children: [
      { index: true, element: <OverviewPage /> },
      { path: "runs/latest", element: <LatestRunPage /> },
      { path: "quality", element: <QualityPage /> },
      { path: "quality/shadow-performance", element: <ShadowPerformancePage /> },
      { path: "quality/operator-vs-shadow", element: <OperatorVsShadowPage /> },
      { path: "markets/:marketId", element: <MarketDetailPage /> },
      { path: "watchlist", element: <WatchlistPage /> },
      { path: "operations", element: <OperationsPage /> },
    ],
  },
]);
