import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { WorkspaceProvider } from "./workspace/WorkspaceContext";
import { TabsProvider } from "./tabs/TabsContext";
import { ModalsProvider } from "./common/Modals";
import { SioProvider } from "./sio/SioContext";
import { BrandingProvider } from "./branding/BrandingContext";
import App from "./App";
import { basePath } from "./api/client";
import { routerBasename } from "./services/basePath";
import "./i18n";
import "./index.css";

// A deploy (or a dev rebuild) replaces the hashed chunks: a page loaded before it fails to fetch a
// lazy chunk ("Failed to fetch dynamically imported module"). Vite raises `vite:preloadError`;
// one reload picks up the new index. Guarded so a genuinely missing chunk never loops.
window.addEventListener("vite:preloadError", (event) => {
  const key = "liberty.chunk-reload";
  const last = Number(sessionStorage.getItem(key) ?? 0);
  if (Date.now() - last < 30_000) return;
  sessionStorage.setItem(key, String(Date.now()));
  event.preventDefault();
  window.location.reload();
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter basename={routerBasename(basePath())}>
      <BrandingProvider>
        <ModalsProvider>
          <AuthProvider>
            <SioProvider>
              <WorkspaceProvider>
                <TabsProvider>
                  <App />
                </TabsProvider>
              </WorkspaceProvider>
            </SioProvider>
          </AuthProvider>
        </ModalsProvider>
      </BrandingProvider>
    </BrowserRouter>
  </StrictMode>,
);
