// The Tools surface — the sidebar's Workspace → Tools entry
// (docs/design-brief-dark, "Tools surface").
//
// "What Addison can reach on this computer." REAL DATA ONLY, and this page is the
// clearest case in the app for why that rule exists: it is the page a person opens
// to check what their assistant is wired into. The prototype's IDE / Files /
// Calendar / Email / Browser rows are demo content for connections Addison does
// not have, and shipping them would be the app claiming reach it does not possess
// (IMPLEMENTATION.md, standing rule 1). So:
//
//   Connected — providers whose key is in the keychain, the models set up to run
//               on this computer, and the folders Addison may work in.
//   Available — providers with no key yet, each with the way to add one.
//
// Trusted folders appear here ONLY on the Developer and Custom surfaces, keyed
// off the active profile exactly as the Settings panel is (Phase-2 step 5): a
// Simple-profile person has no workspace-trust surface anywhere, and this page
// is not a back door to one.

import { Surface, SurfaceRow, SurfaceSection } from "./Surface";
import type { ProviderInfo } from "../ipc/client";
import type { RoleOption, WorkspaceRoot } from "../types/ui";
import type { ReactNode } from "react";

export const TOOLS_DESCRIPTION =
  "What Addison can reach on this computer. Connect only what you're comfortable with.";

export function ToolsSurface({
  connected,
  providers,
  roles,
  trustedRoots,
  showTrustedFolders,
  onAddKey,
  onStopTrusting,
  workspaceBusy = false,
  pinned,
}: {
  connected: boolean;
  providers: ProviderInfo[];
  roles: RoleOption[];
  trustedRoots: WorkspaceRoot[];
  /** Developer/Custom only — the same gate the Settings panel uses. */
  showTrustedFolders: boolean;
  /** Opens Settings at the API-keys section. */
  onAddKey: () => void;
  onStopTrusting: (directory: string) => void;
  workspaceBusy?: boolean;
  pinned?: ReactNode;
}) {
  const connectedProviders = providers.filter((p) => p.connected);
  const availableProviders = providers.filter((p) => !p.connected);
  const localModels = roles.find((r) => r.role === "local" && r.configured)?.models ?? [];
  const folders = showTrustedFolders ? trustedRoots : [];

  const hasConnected =
    connectedProviders.length > 0 || localModels.length > 0 || folders.length > 0;

  return (
    <Surface title="Tools" description={TOOLS_DESCRIPTION} pinned={pinned}>
      <SurfaceSection label="Connected">
        {!hasConnected && (
          <SurfaceRow
            name={
              connected
                ? "Nothing yet — Addison can only reach what you connect below."
                : "Addison's engine isn't connected, so this page can't say what it can reach."
            }
          />
        )}
        {connectedProviders.map((p) => (
          <SurfaceRow key={p.id} name={p.label} value="connected" />
        ))}
        {localModels.map((m) => (
          <SurfaceRow key={m.id} name={m.label} value="on this computer" />
        ))}
        {folders.map((root) => (
          <SurfaceRow
            key={root.directory}
            name={folderName(root.directory)}
            value={
              <span className="block max-w-[220px] truncate" title={root.directory}>
                {root.directory}
              </span>
            }
            action="Stop trusting"
            actionAriaLabel={`Stop trusting ${root.directory}`}
            actionDisabled={workspaceBusy}
            onAction={() => onStopTrusting(root.directory)}
          />
        ))}
      </SurfaceSection>

      {availableProviders.length > 0 && (
        <SurfaceSection label="Available">
          {availableProviders.map((p) => (
            <SurfaceRow
              key={p.id}
              name={p.label}
              value="not connected"
              action="add key"
              actionAriaLabel={`Add a key for ${p.label}`}
              onAction={onAddKey}
            />
          ))}
        </SurfaceSection>
      )}
    </Surface>
  );
}

/** The last path segment, for a readable row name beside the full mono path. */
function folderName(directory: string): string {
  const parts = directory.split("/").filter(Boolean);
  return parts.length > 0 ? parts[parts.length - 1] : directory;
}
