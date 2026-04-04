import { createBrowserRouter } from "react-router-dom";
import { LatestRunPage } from "../pages/LatestRunPage";
import { LoginPage } from "../pages/LoginPage";
import { MarketDetailPage } from "../pages/MarketDetailPage";
import { OperationsPage } from "../pages/OperationsPage";
import { OverviewPage } from "../pages/OverviewPage";
import { WatchlistPage } from "../pages/WatchlistPage";

export const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  { path: "/", element: <OverviewPage /> },
  { path: "/runs/latest", element: <LatestRunPage /> },
  { path: "/markets/:marketId", element: <MarketDetailPage /> },
  { path: "/watchlist", element: <WatchlistPage /> },
  { path: "/operations", element: <OperationsPage /> },
]);
