// Skills — the Settings section for user-authored, plain-text guidance notes,
// in the dark direction's row idiom.
//
// A skill is PURE TEXT: a name + free-form instructions the person writes ("Keep
// answers short", "Always show amounts in CZK"). Toggle one on and Addison keeps
// it in mind; there is no code or execution here (unlike routines/widgets), so
// this surface is identical in both profiles.
//
// The on/off control is a MONO action rather than a switch: on a surface built
// from hairline rows, a sliding pill would be the only piece of chrome on the
// page. It reads as the current state (`on` / `off`) and flips when pressed —
// and because a page of two-letter controls is unusable with a screen reader,
// each one carries the same explicit "Turn on/off {name}" label the switch had.

import { useState } from "react";
import type { SkillsState } from "../hooks/useSkills";
import { RowAction, SurfaceRow } from "./Surface";

interface Props {
  connected: boolean;
  skills: SkillsState;
}

// Sentinel for "the add-a-skill form is open" (vs. an id string for editing one).
const NEW = "__new__";

export function SkillsSection({ connected, skills: state }: Props) {
  const {
    skills,
    skillsLoaded,
    handleCreateSkill,
    handleUpdateSkill,
    handleToggleSkill,
    handleDeleteSkill,
  } = state;

  // Which form is open: null = none, NEW = the add form, otherwise a skill id
  // being edited. Only one form is open at a time so the section stays calm.
  const [formFor, setFormFor] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [instructions, setInstructions] = useState("");
  const [saving, setSaving] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);

  function openAdd() {
    setFormFor(NEW);
    setName("");
    setInstructions("");
    setConfirmingDelete(null);
  }

  function openEdit(id: string, currentName: string, currentInstructions: string) {
    setFormFor(id);
    setName(currentName);
    setInstructions(currentInstructions);
    setConfirmingDelete(null);
  }

  function closeForm() {
    setFormFor(null);
    setName("");
    setInstructions("");
    setSaving(false);
  }

  async function save() {
    const trimmedName = name.trim();
    const trimmedInstructions = instructions.trim();
    if (!trimmedName || !trimmedInstructions || saving) return;
    setSaving(true);
    const ok =
      formFor === NEW
        ? await handleCreateSkill(trimmedName, trimmedInstructions)
        : await handleUpdateSkill(formFor as string, trimmedName, trimmedInstructions);
    setSaving(false);
    if (ok) closeForm();
  }

  function remove(id: string) {
    if (confirmingDelete !== id) {
      setConfirmingDelete(id);
      return;
    }
    setConfirmingDelete(null);
    if (formFor === id) closeForm();
    handleDeleteSkill(id);
  }

  if (!connected) {
    return <SurfaceRow wrap name="Your skills appear here once Addison's engine is connected." />;
  }

  if (!skillsLoaded) {
    return <SurfaceRow wrap name="Looking for your skills…" />;
  }

  return (
    <>
      {skills.length === 0 && formFor !== NEW && (
        <SurfaceRow
          name="None yet"
          value="short notes on how you like things done"
          action="add a skill"
          onAction={openAdd}
        />
      )}

      {skills.map((skill) => (
        <SurfaceRow
          key={skill.id}
          name={skill.name}
          actions={
            <>
              <RowAction
                mono
                ariaLabel={skill.enabled ? `Turn off ${skill.name}` : `Turn on ${skill.name}`}
                onClick={() => handleToggleSkill(skill.id, !skill.enabled)}
              >
                {skill.enabled ? "on" : "off"}
              </RowAction>
              <RowAction
                tone="muted"
                ariaLabel={`Edit ${skill.name}`}
                onClick={() =>
                  formFor === skill.id
                    ? closeForm()
                    : openEdit(skill.id, skill.name, skill.instructions)
                }
              >
                {formFor === skill.id ? "Close" : "Edit"}
              </RowAction>
              <RowAction
                tone="danger"
                ariaLabel={`Remove ${skill.name}`}
                onClick={() => remove(skill.id)}
              >
                {confirmingDelete === skill.id ? "Really remove?" : "Remove"}
              </RowAction>
            </>
          }
        >
          {skill.instructions && formFor !== skill.id && (
            <p className="m-0 mt-1 line-clamp-2 text-[12px] leading-[1.55] text-muted">
              {skill.instructions}
            </p>
          )}
          {!skill.enabled && formFor !== skill.id && (
            <p className="m-0 mt-1 text-[12px] text-muted">
              Off — Addison isn&rsquo;t using this right now.
            </p>
          )}
          {formFor === skill.id && (
            <SkillForm
              name={name}
              instructions={instructions}
              saving={saving}
              onName={setName}
              onInstructions={setInstructions}
              onSave={() => void save()}
              onCancel={closeForm}
            />
          )}
        </SurfaceRow>
      ))}

      {formFor === NEW ? (
        <SurfaceRow name="New skill">
          <SkillForm
            name={name}
            instructions={instructions}
            saving={saving}
            onName={setName}
            onInstructions={setInstructions}
            onSave={() => void save()}
            onCancel={closeForm}
          />
        </SurfaceRow>
      ) : (
        skills.length > 0 && (
          <SurfaceRow name="Another note" action="add a skill" onAction={openAdd} />
        )
      )}
    </>
  );
}

// The shared name + instructions form, used by both add and edit. Borderless
// mono fields over a hairline that brightens on focus — the composer's language,
// not a boxed input, because the surface has no boxes on it.
function SkillForm({
  name,
  instructions,
  saving,
  onName,
  onInstructions,
  onSave,
  onCancel,
}: {
  name: string;
  instructions: string;
  saving: boolean;
  onName: (v: string) => void;
  onInstructions: (v: string) => void;
  onSave: () => void;
  onCancel: () => void;
}) {
  const canSave = Boolean(name.trim()) && Boolean(instructions.trim()) && !saving;
  return (
    <div className="mt-2.5 flex flex-col gap-2">
      <input
        type="text"
        value={name}
        onChange={(e) => onName(e.target.value)}
        placeholder="Name this skill"
        disabled={saving}
        aria-label="Skill name"
        className="w-full border-b border-line bg-transparent py-1.5 font-mono text-[11px] text-ink placeholder:text-disabled focus:border-track-hi disabled:opacity-60"
      />
      <textarea
        value={instructions}
        onChange={(e) => onInstructions(e.target.value)}
        placeholder="Tell Addison how to approach this…"
        rows={4}
        disabled={saving}
        aria-label="Skill instructions"
        className="w-full resize-y border-b border-line bg-transparent py-1.5 font-mono text-[11px] leading-[1.6] text-ink placeholder:text-disabled focus:border-track-hi disabled:opacity-60"
      />
      <div className="flex items-baseline gap-5">
        <RowAction onClick={onSave} disabled={!canSave}>
          {saving ? "Saving…" : "Save"}
        </RowAction>
        <RowAction tone="muted" onClick={onCancel} disabled={saving}>
          Cancel
        </RowAction>
      </div>
    </div>
  );
}
