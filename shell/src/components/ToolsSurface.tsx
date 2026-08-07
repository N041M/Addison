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
// is not a back door to one. Tool servers are gated the same way, and that gate
// is the whole of what a Simple-profile person is told about them: no section is
// rendered at all, because these tools are Developer-only and a page that named
// them would be claiming reach the active profile does not have.
//
// TOOL SERVERS GET A SECTION EACH, never rows mixed into the lists above
// (Phase-2 step 7, phase 2 — the plan's Decision 2, answered 2026-08-07).
// Namespacing exists in the core because a foreign tool must never be mistaken
// for a native one, and this page has to draw the same line the registry draws.
// A section per server is where that line is visible: it carries the provenance
// ("from your tool server X") and the server-level state — never checked,
// unreachable, N tools found — in ONE place. Step 6's disabled-card treatment
// answers a different sentence ("a thing you made that your profile can't use");
// borrowed here it would scatter one server's outage across the native list as
// unexplained dead rows, with nowhere to say which server went away.
//
// Everything a server sent is a STRANGER'S TEXT. Names and descriptions render as
// plain children, through React's own escaping — never `dangerouslySetInnerHTML`,
// and never through the markdown renderer the chat thread uses.

import { Surface, SurfaceRow, SurfaceSection } from "./Surface";
import type { ProviderInfo } from "../ipc/client";
import type { McpServer, RoleOption, WorkspaceRoot } from "../types/ui";
import type { ReactNode } from "react";

const TOOLS_DESCRIPTION =
  "What Addison can reach on this computer. Connect only what you're comfortable with.";

/** Under every discovered tool, since dispatch shipped (Phase-2 step 7, phase 3).
 *
 * It replaced "Addison can see this tool but can't use it yet." — the sentence the
 * core answered with while there was no dispatch behind these rows — and the
 * replacement has to carry the same weight the old one did. What a person needs
 * from this line now is not that the tool works but that NOTHING here runs behind
 * their back: every call stops and asks, every time, because this is somebody
 * else's program and Addison cannot know what it will do. */
const ASKS_FIRST = "Addison asks you before each use.";

export function ToolsSurface({
  connected,
  providers,
  roles,
  trustedRoots,
  showTrustedFolders,
  mcpServers = [],
  onAddKey,
  onStopTrusting,
  workspaceBusy = false,
  pinned,
}: {
  connected: boolean;
  providers: ProviderInfo[];
  roles: RoleOption[];
  trustedRoots: WorkspaceRoot[];
  /** Developer/Custom only — the same gate the Settings panel uses. Covers the
   * tool-server sections too: both answer "what may Addison reach", and both are
   * Developer surfaces. */
  showTrustedFolders: boolean;
  /** The configured tool servers with whatever the last check found. Empty on the
   * Simple surface, which is the gate rather than a rendering choice. */
  mcpServers?: McpServer[];
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
  const servers = showTrustedFolders ? mcpServers : [];

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

      {/* One section per tool server — the whole of Decision 2, rendered. Each is
          a direct child of <Surface> so the enter/leave stagger still walks them
          (Surface.tsx's first convention). */}
      {servers.map((server) => (
        <McpServerSection key={server.id} server={server} />
      ))}
    </Surface>
  );
}

/** One tool server: who it is, where it stands, and what it offered.
 *
 * The section body always says SOMETHING. A server nobody has checked, one that
 * could not be reached, and one that answered with nothing are three different
 * facts, and a silent empty section would make all three look like the fourth
 * (a server with no tools) — on the page where being wrong about reach matters
 * most. */
function McpServerSection({ server }: { server: McpServer }) {
  return (
    <SurfaceSection label={`Tool server · ${server.name}`}>
      <SurfaceRow
        wrap
        name={`From your tool server ${server.name}. Addison asks before it uses any of these.`}
        value={server.status === "ok" ? `${server.tools.length} found` : undefined}
      />
      {server.status === "never" && (
        <SurfaceRow wrap name="Not checked yet — check it in Settings to see what it offers." />
      )}
      {server.status === "failed" && (
        <SurfaceRow
          wrap
          name={server.error ?? "Addison couldn't reach this server when it last checked."}
        />
      )}
      {server.status === "ok" && server.tools.length === 0 && (
        <SurfaceRow wrap name="This server answered, and offered no tools." />
      )}
      {server.tools.map((tool) => (
        // `name` and `description` are the server's own words. Passed as plain
        // children, so React escapes them; nothing on this page renders foreign
        // text as markup.
        <SurfaceRow key={tool.name} name={tool.name} value="asks each time">
          {tool.description && (
            <p className="m-0 mt-1 text-[12px] leading-[1.55] text-muted">{tool.description}</p>
          )}
          <p className="m-0 mt-1 text-[12px] leading-[1.55] text-muted">{ASKS_FIRST}</p>
        </SurfaceRow>
      ))}
      {server.status === "ok" && server.skipped ? (
        <SurfaceRow
          wrap
          name={`This server offered ${server.skipped} more Addison wouldn't take — an odd name, a name already in use, or too many.`}
        />
      ) : null}
    </SurfaceSection>
  );
}

/** The last path segment, for a readable row name beside the full mono path. */
function folderName(directory: string): string {
  const parts = directory.split("/").filter(Boolean);
  return parts.length > 0 ? parts[parts.length - 1] : directory;
}
