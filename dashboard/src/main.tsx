import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./app/App";

// Dev-only axe-core smoke check for the Command Center (Phase 1 a11y gate).
if (import.meta.env.DEV) {
  void import("@axe-core/react").then(({ default: axe }) => {
    axe(React, ReactDOM, 1000);
  });
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
