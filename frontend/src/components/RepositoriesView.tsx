import { useEffect, useState } from "react";
import type { CSSProperties, FormEvent } from "react";
import {
  ApiError,
  browseFolders,
  connectFolder,
  disconnectFolder,
  disconnectRepositoryAccount,
  getOAuthProviders,
  getSyncOverview,
  listRepositoryAccounts,
  listSyncedFiles,
  runSync,
  startSignIn,
} from "../api/client";
import type {
  FolderNode,
  OAuthProvider,
  OAuthProviderInfo,
  RepositoryAccount,
  SyncedFile,
  SyncOverview,
  SyncSource,
} from "../api/types";

// ID-HU-INT-001: sign in with Google or Microsoft, pick the folders to sync,
// and see what each sync found. Files added or edited in a connected folder
// are ingested automatically every few minutes.

const PROVIDER_LABELS: Record<OAuthProvider, string> = { google: "Google Drive", microsoft: "SharePoint / OneDrive" };
const KIND_LABELS: Record<SyncSource["kind"], string> = { google_drive: "Google Drive", sharepoint: "SharePoint" };
const CLASSIFICATIONS = ["internal", "public", "restricted"];
const POLL_MS = 3000;

const timeFormat = new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeStyle: "short" });

function errorText(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

// After the provider sends the browser back: ?oauth=connected / ?oauth_error=…
// Pure (React may call a state initializer twice); the URL is cleaned in an effect.
function readSignInResult(): { ok: boolean; message: string } | null {
  const params = new URLSearchParams(window.location.search);
  const error = params.get("oauth_error");
  const connected = params.get("oauth") === "connected";
  if (!error && !connected) return null;
  if (error) return { ok: false, message: `Sign-in failed: ${error}` };
  const provider = params.get("provider") as OAuthProvider | null;
  const label = provider ? PROVIDER_LABELS[provider] : "the account";
  return { ok: true, message: `Signed in to ${label} as ${params.get("account") ?? "your account"}. Pick the folders to sync below.` };
}

function summaryText(source: SyncSource): string {
  const s = source.last_summary ?? {};
  const parts = [
    s.new ? `${s.new} new` : null,
    s.updated ? `${s.updated} updated` : null,
    s.unchanged ? `${s.unchanged} unchanged` : null,
    s.failed ? `${s.failed} failed` : null,
    s.skipped ? `${s.skipped} skipped (not Excel/PDF)` : null,
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : "no files yet";
}

interface Crumb {
  id: string | null;
  name: string;
}

interface ConnectForm {
  node: FolderNode;
  name: string;
  classification: string;
  autoApprove: boolean;
  includeSubfolders: boolean;
}

export default function RepositoriesView({ active = true }: { active?: boolean }) {
  const [providers, setProviders] = useState<OAuthProviderInfo[]>([]);
  const [accounts, setAccounts] = useState<RepositoryAccount[]>([]);
  const [overview, setOverview] = useState<SyncOverview | null>(null);
  const [notice, setNotice] = useState<{ ok: boolean; message: string } | null>(readSignInResult);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [account, setAccount] = useState<RepositoryAccount | null>(null);
  const [path, setPath] = useState<Crumb[]>([]);
  const [nodes, setNodes] = useState<FolderNode[]>([]);
  const [browsing, setBrowsing] = useState(false);
  const [browseError, setBrowseError] = useState<string | null>(null);
  const [form, setForm] = useState<ConnectForm | null>(null);
  const [files, setFiles] = useState<Record<string, SyncedFile[]>>({});
  const [watching, setWatching] = useState(false);

  function reload() {
    setLoadError(null);
    Promise.all([getOAuthProviders(), listRepositoryAccounts(), getSyncOverview()])
      .then(([p, a, o]) => {
        setProviders(p);
        setAccounts(a);
        setOverview(o);
      })
      .catch((err) => setLoadError(errorText(err, "Could not load the repositories.")));
  }

  useEffect(() => {
    if (active) reload();
  }, [active]);

  // The sign-in result has been read: drop it from the URL so a reload doesn't repeat it.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.has("oauth") || params.has("oauth_error")) {
      window.history.replaceState(null, "", `${window.location.pathname}${window.location.hash}`);
    }
  }, []);

  // While a sync runs, refresh its results until it's done.
  useEffect(() => {
    if (!watching) return;
    const timer = window.setInterval(() => {
      getSyncOverview()
        .then((o) => {
          setOverview(o);
          if (!o.running) setWatching(false);
        })
        .catch(() => setWatching(false));
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [watching]);

  async function signIn(provider: OAuthProvider) {
    try {
      window.location.assign(await startSignIn(provider));
    } catch (err) {
      setNotice({ ok: false, message: errorText(err, "Could not start the sign-in.") });
    }
  }

  function open(target: RepositoryAccount, crumbs: Crumb[]) {
    setAccount(target);
    setPath(crumbs);
    setForm(null);
    setBrowsing(true);
    setBrowseError(null);
    const parent = crumbs.length ? crumbs[crumbs.length - 1].id ?? undefined : undefined;
    browseFolders(target.id, parent)
      .then(setNodes)
      .catch((err) => {
        setNodes([]);
        setBrowseError(errorText(err, "Could not list the folders."));
      })
      .finally(() => setBrowsing(false));
  }

  async function disconnectAccount(target: RepositoryAccount) {
    try {
      await disconnectRepositoryAccount(target.id);
      if (account?.id === target.id) setAccount(null);
      reload();
    } catch (err) {
      setNotice({ ok: false, message: errorText(err, "Could not disconnect the account.") });
    }
  }

  async function submitConnect(event: FormEvent) {
    event.preventDefault();
    if (!form || !account) return;
    const provider = providers.find((p) => p.provider === account.provider);
    try {
      const source = await connectFolder({
        kind: provider?.kind ?? (account.provider === "google" ? "google_drive" : "sharepoint"),
        name: form.name.trim() || form.node.name,
        config: { ...form.node.config, include_subfolders: form.includeSubfolders },
        confidentiality_tag: form.classification,
        auto_approve: form.autoApprove,
        account_id: account.id,
      });
      setForm(null);
      await runSync(source.id);
      setNotice({ ok: true, message: `Connected "${source.name}". Its files are being synced now.` });
      setWatching(true);
      reload();
    } catch (err) {
      setNotice({ ok: false, message: errorText(err, "Could not connect the folder.") });
    }
  }

  async function syncNow(sourceId?: string) {
    try {
      const { started } = await runSync(sourceId);
      setNotice(started ? null : { ok: false, message: "A sync is already running; its results will appear here." });
      setWatching(true);
      getSyncOverview().then(setOverview).catch(() => undefined);
    } catch (err) {
      setNotice({ ok: false, message: errorText(err, "Could not start the sync.") });
    }
  }

  async function removeFolder(source: SyncSource) {
    try {
      await disconnectFolder(source.id);
      reload();
    } catch (err) {
      setNotice({ ok: false, message: errorText(err, "Could not disconnect the folder.") });
    }
  }

  async function toggleFiles(sourceId: string) {
    if (files[sourceId]) {
      setFiles(({ [sourceId]: _, ...rest }) => rest);
      return;
    }
    try {
      const list = await listSyncedFiles(sourceId);
      setFiles((current) => ({ ...current, [sourceId]: list }));
    } catch (err) {
      setNotice({ ok: false, message: errorText(err, "Could not list the files.") });
    }
  }

  if (loadError) {
    return <div role="alert" style={banner(false)}>{loadError}</div>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
      <section style={panel}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: "1rem", flexWrap: "wrap" }}>
          <div>
            <h2 style={{ margin: 0, fontSize: "1.1rem" }}>Repositories</h2>
            <p style={hint}>
              Sign in to Google Drive or SharePoint and pick folders: files added or edited there are ingested
              automatically
              {overview && overview.interval_minutes > 0 ? ` every ${overview.interval_minutes} minutes` : ""}.
              {overview && overview.interval_minutes <= 0 ? " Automatic sync is off (SYNC_INTERVAL_MINUTES=0)." : ""}
            </p>
          </div>
          <button type="button" onClick={() => syncNow()} disabled={!overview?.sources.length} style={secondaryButton}>
            {overview?.running || watching ? "Syncing…" : "Sync all now"}
          </button>
        </div>
        {notice && (
          <div role={notice.ok ? "status" : "alert"} style={{ ...banner(notice.ok), marginTop: "0.6rem" }}>
            {notice.message}
          </div>
        )}
      </section>

      <section aria-label="Accounts" style={panel}>
        <h3 style={heading}>Accounts</h3>
        <div style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap" }}>
          {providers.map((p) => (
            <div key={p.provider} style={{ display: "flex", flexDirection: "column", gap: "0.25rem" }}>
              <button
                type="button"
                onClick={() => signIn(p.provider)}
                disabled={!p.configured}
                style={p.provider === "google" ? googleButton : microsoftButton}
              >
                Sign in with {p.provider === "google" ? "Google" : "Microsoft"}
              </button>
              {!p.configured && (
                <span style={{ ...hint, maxWidth: "20rem" }}>
                  Not set up yet: an administrator adds the {p.provider === "google" ? "Google" : "Microsoft"} OAuth app
                  (redirect URI <code>{p.redirect_uri}</code>) to the server's .env.
                </span>
              )}
            </div>
          ))}
        </div>
        {accounts.length === 0 ? (
          <p style={hint}>No account connected yet.</p>
        ) : (
          <ul style={list}>
            {accounts.map((a) => (
              <li key={a.id} style={row}>
                <span>
                  <strong>{PROVIDER_LABELS[a.provider]}</strong> · {a.email}
                </span>
                <span style={{ display: "flex", gap: "0.4rem" }}>
                  <button type="button" onClick={() => open(a, [])} style={primaryButton}>
                    Add folders
                  </button>
                  <button type="button" onClick={() => disconnectAccount(a)} style={dangerButton}>
                    Disconnect
                  </button>
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {account && (
        <section aria-label="Folder browser" style={panel}>
          <h3 style={heading}>
            Pick a folder · {PROVIDER_LABELS[account.provider]} ({account.email})
          </h3>
          <nav aria-label="Folder path" style={{ display: "flex", gap: "0.3rem", flexWrap: "wrap", fontSize: "0.85rem" }}>
            <button type="button" onClick={() => open(account, [])} style={crumbButton}>
              Top
            </button>
            {path.map((crumb, i) => (
              <span key={`${crumb.id}-${i}`}>
                {" / "}
                <button type="button" onClick={() => open(account, path.slice(0, i + 1))} style={crumbButton}>
                  {crumb.name}
                </button>
              </span>
            ))}
          </nav>
          {browseError && <div role="alert" style={{ ...banner(false), marginTop: "0.5rem" }}>{browseError}</div>}
          {browsing ? (
            <p style={hint}>Loading folders…</p>
          ) : (
            <ul style={list} aria-label="Folders">
              {nodes.length === 0 && !browseError && <li style={hint}>No folders here.</li>}
              {nodes.map((node) => (
                <li key={node.id} style={row}>
                  <span>📁 {node.name}</span>
                  <span style={{ display: "flex", gap: "0.4rem" }}>
                    {node.browsable && (
                      <button
                        type="button"
                        onClick={() => open(account, [...path, { id: node.id, name: node.name }])}
                        style={secondaryButton}
                        aria-label={`Open ${node.name}`}
                      >
                        Open
                      </button>
                    )}
                    {node.selectable && (
                      <button
                        type="button"
                        onClick={() =>
                          setForm({
                            node,
                            name: [...path.map((c) => c.name), node.name].join("/"),
                            classification: "internal",
                            autoApprove: false,
                            includeSubfolders: false,
                          })
                        }
                        style={primaryButton}
                        aria-label={`Connect ${node.name}`}
                      >
                        Connect
                      </button>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {form && (
            <form onSubmit={submitConnect} aria-label="Connect folder" style={formBox}>
              <strong>Connect “{form.node.name}”</strong>
              <label style={field}>
                Name
                <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} style={input} />
              </label>
              <label style={field}>
                Classification of its documents
                <select
                  value={form.classification}
                  onChange={(e) => setForm({ ...form, classification: e.target.value })}
                  style={input}
                >
                  {CLASSIFICATIONS.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              </label>
              <label style={checkbox}>
                <input
                  type="checkbox"
                  checked={form.includeSubfolders}
                  onChange={(e) => setForm({ ...form, includeSubfolders: e.target.checked })}
                />
                Include subfolders
              </label>
              <label style={checkbox}>
                <input
                  type="checkbox"
                  checked={form.autoApprove}
                  onChange={(e) => setForm({ ...form, autoApprove: e.target.checked })}
                />
                The folder only holds approved documents (approve each synced version)
              </label>
              <span style={{ display: "flex", gap: "0.4rem" }}>
                <button type="submit" style={primaryButton}>
                  Connect and sync
                </button>
                <button type="button" onClick={() => setForm(null)} style={secondaryButton}>
                  Cancel
                </button>
              </span>
            </form>
          )}
        </section>
      )}

      <section aria-label="Connected folders" style={panel}>
        <h3 style={heading}>Connected folders</h3>
        {!overview?.sources.length ? (
          <p style={hint}>No folder connected yet.</p>
        ) : (
          <ul style={list}>
            {overview.sources.map((source) => (
              <li key={source.id} style={{ ...row, flexDirection: "column", alignItems: "stretch" }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: "0.5rem", flexWrap: "wrap" }}>
                  <span>
                    <strong>{source.name}</strong>{" "}
                    <span style={hint}>
                      {KIND_LABELS[source.kind]}
                      {source.account_email ? ` · ${source.account_email}` : " · service credentials"} ·{" "}
                      {source.confidentiality_tag}
                      {source.auto_approve ? " · auto-approve" : ""}
                    </span>
                  </span>
                  <span style={{ display: "flex", gap: "0.4rem" }}>
                    <button type="button" onClick={() => syncNow(source.id)} style={secondaryButton}>
                      Sync now
                    </button>
                    <button type="button" onClick={() => toggleFiles(source.id)} style={secondaryButton}>
                      {files[source.id] ? "Hide files" : `Files (${source.file_count})`}
                    </button>
                    <button type="button" onClick={() => removeFolder(source)} style={dangerButton}>
                      Disconnect
                    </button>
                  </span>
                </div>
                <div data-testid={`sync-status-${source.id}`} style={{ fontSize: "0.8rem", color: "#475569" }}>
                  {source.last_sync_at
                    ? `Last sync ${timeFormat.format(new Date(source.last_sync_at))}: ${summaryText(source)}`
                    : "Not synced yet"}
                </div>
                {source.last_status === "error" && source.last_error && (
                  <div role="alert" style={banner(false)}>{source.last_error}</div>
                )}
                {files[source.id] && (
                  <table style={table} aria-label={`Files of ${source.name}`}>
                    <tbody>
                      {files[source.id].length === 0 && (
                        <tr>
                          <td style={cell}>No files synced yet.</td>
                        </tr>
                      )}
                      {files[source.id].map((f) => (
                        <tr key={f.remote_id}>
                          <td style={cell}>{f.path}</td>
                          <td style={{ ...cell, color: f.status === "failed" ? "#b91c1c" : "#15803d" }}>
                            {f.status === "failed" ? `failed: ${f.error}` : "ingested"}
                          </td>
                          <td style={{ ...cell, color: "#64748b" }}>{timeFormat.format(new Date(f.synced_at))}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function banner(ok: boolean): CSSProperties {
  return {
    background: ok ? "#f0fdf4" : "#fef2f2",
    color: ok ? "#15803d" : "#b91c1c",
    padding: "0.5rem 0.75rem",
    borderRadius: "0.5rem",
    fontSize: "0.85rem",
  };
}

const panel: CSSProperties = {
  border: "1px solid #e2e8f0",
  borderRadius: "0.75rem",
  padding: "1rem",
  background: "white",
  display: "flex",
  flexDirection: "column",
  gap: "0.6rem",
};
const heading: CSSProperties = { margin: 0, fontSize: "0.95rem" };
const hint: CSSProperties = { margin: "0.2rem 0 0", fontSize: "0.8rem", color: "#64748b" };
const list: CSSProperties = { listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: "0.4rem" };
const row: CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  gap: "0.5rem",
  padding: "0.5rem 0.6rem",
  border: "1px solid #f1f5f9",
  borderRadius: "0.5rem",
  fontSize: "0.88rem",
};
const buttonBase: CSSProperties = {
  padding: "0.35rem 0.75rem",
  borderRadius: "0.45rem",
  cursor: "pointer",
  fontSize: "0.82rem",
};
const primaryButton: CSSProperties = { ...buttonBase, border: "1px solid #4f46e5", background: "#4f46e5", color: "white" };
const secondaryButton: CSSProperties = { ...buttonBase, border: "1px solid #cbd5e1", background: "white", color: "#334155" };
const dangerButton: CSSProperties = { ...buttonBase, border: "1px solid #fecaca", background: "white", color: "#b91c1c" };
const googleButton: CSSProperties = { ...buttonBase, border: "1px solid #dadce0", background: "white", color: "#3c4043", fontWeight: 600 };
const microsoftButton: CSSProperties = { ...buttonBase, border: "1px solid #2f2f2f", background: "#2f2f2f", color: "white", fontWeight: 600 };
const crumbButton: CSSProperties = { background: "none", border: "none", padding: 0, color: "#4f46e5", cursor: "pointer", fontSize: "0.85rem" };
const formBox: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "0.5rem",
  padding: "0.75rem",
  border: "1px solid #c7d2fe",
  borderRadius: "0.6rem",
  background: "#eef2ff",
  fontSize: "0.85rem",
};
const field: CSSProperties = { display: "flex", flexDirection: "column", gap: "0.2rem" };
const checkbox: CSSProperties = { display: "flex", gap: "0.4rem", alignItems: "center" };
const input: CSSProperties = { padding: "0.3rem 0.4rem", borderRadius: "0.4rem", border: "1px solid #cbd5e1", fontSize: "0.85rem" };
const table: CSSProperties = { width: "100%", borderCollapse: "collapse", fontSize: "0.8rem" };
const cell: CSSProperties = { padding: "0.25rem 0.4rem", borderTop: "1px solid #f1f5f9" };
