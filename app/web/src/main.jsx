import React from "react";
import { createRoot } from "react-dom/client";

import { TooltipProvider } from "./components/ui/tooltip";
import { AppRouter } from "./router";
import "./index.css";

// App-wide so tooltips (`Hint`) work on every screen, including the error boundary.
// AppShell keeps its own provider.
createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <TooltipProvider>
      <AppRouter />
    </TooltipProvider>
  </React.StrictMode>,
);
