import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth";

/**
 * Landing route for the OIDC flow. The backend's /auth/oidc/callback redirects
 * here with the freshly-minted JWTs in the URL fragment
 * (`#access_token=…&refresh_token=…`) when `[oidc] frontend_redirect` points at
 * this path. We stash them via the auth context, then go to the app.
 */
export function OidcCallback() {
  const { setTokens } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const hash = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    const access = hash.get("access_token");
    const refresh = hash.get("refresh_token");
    if (!access || !refresh) {
      setError("OIDC callback did not include tokens. Check [oidc] frontend_redirect on the server.");
      return;
    }
    setTokens({ access_token: access, refresh_token: refresh })
      .then(() => navigate("/", { replace: true }))
      .catch((e) => setError(String(e)));
  }, [setTokens, navigate]);

  return <div className="centered muted">{error ? <span className="err">{error}</span> : "Completing sign-in…"}</div>;
}
