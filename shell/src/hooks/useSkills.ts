// Skills — user-authored, plain-text guidance notes the person toggles on; when
// enabled, Addison follows them. PURE TEXT, no execution surface (§ safety model:
// unlike routines/widgets a skill has no command/tool step, so it's identical in
// both modes). This hook owns the list + the create/edit/toggle/remove handlers,
// mirroring useWidgets: lazy refresh (a no-op when the engine is disconnected)
// and errors surfaced through the shared status banner.

import { useState } from "react";
import type { Skill } from "../types/ui";
import { ipc, isEngineConnected } from "../ipc/client";

interface UseSkillsArgs {
  /** Kept for parity with the other data hooks (useWidgets); the refreshers
   * gate on the live `isEngineConnected()` check rather than this snapshot. */
  connected: boolean;
  setStatusBanner: (text: string | null) => void;
}

export function useSkills({ setStatusBanner }: UseSkillsArgs) {
  const [skills, setSkills] = useState<Skill[]>([]);
  // Distinguishes "not loaded yet" from "loaded, and genuinely empty" so the
  // Settings section shows a looking-for line before it shows the empty state.
  const [loaded, setLoaded] = useState(false);

  function refreshSkills() {
    if (!isEngineConnected()) return;
    ipc
      .listSkills()
      .then((list) => {
        setSkills(list);
        setLoaded(true);
      })
      .catch(() => {
        // Leave the section on its last-known skills if we can't read them; still
        // mark loaded so it stops showing the looking-for line.
        setLoaded(true);
      });
  }

  // Create/update return a boolean so the form can clear itself only on success.
  // A resolved {ok:false} from the core shows its plain error; a thrown error
  // (e.g. engine disconnected) shows a plain fallback.
  async function handleCreateSkill(name: string, instructions: string): Promise<boolean> {
    try {
      const res = await ipc.createSkill(name, instructions);
      if (res.ok) {
        refreshSkills();
        return true;
      }
      if (res.error) setStatusBanner(res.error);
      return false;
    } catch (err) {
      setStatusBanner(err instanceof Error ? err.message : "I couldn't save that skill.");
      return false;
    }
  }

  async function handleUpdateSkill(
    id: string,
    name: string,
    instructions: string,
  ): Promise<boolean> {
    try {
      const res = await ipc.updateSkill(id, name, instructions);
      if (res.ok) {
        refreshSkills();
        return true;
      }
      if (res.error) setStatusBanner(res.error);
      return false;
    } catch (err) {
      setStatusBanner(err instanceof Error ? err.message : "I couldn't save that change.");
      return false;
    }
  }

  function handleToggleSkill(id: string, enabled: boolean) {
    // Optimistic: reflect the switch immediately, then reconcile from the core.
    setSkills((prev) => prev.map((s) => (s.id === id ? { ...s, enabled } : s)));
    ipc
      .setSkillEnabled(id, enabled)
      .then((res) => {
        if (!res.ok) setStatusBanner("Couldn't change that skill just now.");
        refreshSkills();
      })
      .catch(() => {
        setStatusBanner("Couldn't change that skill just now.");
        refreshSkills();
      });
  }

  function handleDeleteSkill(id: string) {
    ipc
      .deleteSkill(id)
      .then(() => refreshSkills())
      .catch(() => setStatusBanner("Couldn't remove that skill just now."));
  }

  return {
    skills,
    skillsLoaded: loaded,
    refreshSkills,
    handleCreateSkill,
    handleUpdateSkill,
    handleToggleSkill,
    handleDeleteSkill,
  };
}

export type SkillsState = ReturnType<typeof useSkills>;
