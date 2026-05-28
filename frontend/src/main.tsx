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
import "./i18n";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
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
