import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { WorkspaceProvider } from "./workspace/WorkspaceContext";
import { TabsProvider } from "./tabs/TabsContext";
import { ModalsProvider } from "./common/Modals";
import App from "./App";
import "./i18n";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <ModalsProvider>
        <AuthProvider>
          <WorkspaceProvider>
            <TabsProvider>
              <App />
            </TabsProvider>
          </WorkspaceProvider>
        </AuthProvider>
      </ModalsProvider>
    </BrowserRouter>
  </StrictMode>,
);
