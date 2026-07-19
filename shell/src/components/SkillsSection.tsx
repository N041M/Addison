// Skills — the Settings section for user-authored, plain-text guidance notes.
//
// A skill is PURE TEXT: a name + free-form instructions the person writes ("Keep
// answers short", "Always show amounts in CZK"). Toggle one on and Addison keeps
// it in mind; there is no code or execution here (unlike routines/widgets), so
// this surface is identical in both profiles. Fern direction: the rows/cards are
// the person's to own and act on → rounded; the on/off control is a calm switch,
// not a blocky annotation. Plain, warm language throughout; no jargon.

import { useState } from "react";
import type { SkillsState } from "../hooks/useSkills";

interface Props {
  connected: boolean;
  skills: SkillsState;
}

// Sentinel for "the add-a-skill form is open" (vs. an id string for editing one).
const NEW = "__new__";

export function SkillsSection({ connected, skills: state }: Props) {
  const { skills, skillsLoaded, handleCreateSkill, handleUpdateSkill, handleToggleSkill, handleDeleteSkill } =
    state;

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
    return (
      <p className="text-meta text-muted">
        Your skills appear here once Addison's engine is connected.
      </p>
    );
  }

  if (!skillsLoaded) {
    return <p className="text-meta text-muted">Looking for your skills…</p>;
  }

  return (
    <div className="flex flex-col gap-3">
      {skills.length === 0 ? (
        <p className="text-meta text-muted">
          None yet. Add a short note telling Addison how you like things done — turn it
          on and Addison follows it.
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {skills.map((skill) => (
            <li key={skill.id} className="rounded border border-line bg-paper px-[14px] py-2.5">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <p className="text-action font-semibold text-ink">{skill.name}</p>
                  {skill.instructions && (
                    <p className="mt-0.5 line-clamp-2 text-fine text-faint">{skill.instructions}</p>
                  )}
                  {!skill.enabled && (
                    <p className="mt-0.5 text-fine text-muted">Off — Addison isn't using this right now.</p>
                  )}
                </div>
                <Toggle
                  on={skill.enabled}
                  onChange={(next) => handleToggleSkill(skill.id, next)}
                  label={skill.enabled ? `Turn off ${skill.name}` : `Turn on ${skill.name}`}
                />
              </div>

              {formFor === skill.id ? (
                <SkillForm
                  name={name}
                  instructions={instructions}
                  saving={saving}
                  onName={setName}
                  onInstructions={setInstructions}
                  onSave={() => void save()}
                  onCancel={closeForm}
                />
              ) : (
                <div className="mt-2 flex items-center gap-3.5">
                  <button
                    type="button"
                    onClick={() => openEdit(skill.id, skill.name, skill.instructions)}
                    className="text-xs font-medium text-muted hover:text-ink-soft"
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    onClick={() => remove(skill.id)}
                    className="text-xs font-medium text-faint hover:text-danger"
                  >
                    {confirmingDelete === skill.id ? "Really remove?" : "Remove"}
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      {/* Add a skill — an outlined, ownable button that expands into the form. */}
      {formFor === NEW ? (
        <div className="rounded border border-line bg-paper px-[14px] py-3">
          <SkillForm
            name={name}
            instructions={instructions}
            saving={saving}
            onName={setName}
            onInstructions={setInstructions}
            onSave={() => void save()}
            onCancel={closeForm}
          />
        </div>
      ) : (
        <button
          type="button"
          onClick={openAdd}
          className="self-start rounded-sm border border-line bg-transparent px-3.5 py-2 text-meta font-semibold text-ink-soft hover:border-muted max-md:min-h-[44px]"
        >
          ＋ Add a skill
        </button>
      )}
    </div>
  );
}

// The shared name + instructions form, used by both add and edit. Rounded inputs
// (8px) with the app's fern-when-filled border cue; plain-language placeholders.
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
    <div className="mt-3 flex flex-col gap-2 first:mt-0">
      <input
        type="text"
        value={name}
        onChange={(e) => onName(e.target.value)}
        placeholder="Name this skill"
        disabled={saving}
        aria-label="Skill name"
        className={
          "rounded border bg-surface px-3 py-2 text-control text-ink placeholder:text-faint disabled:opacity-60 max-md:min-h-[44px] " +
          (name ? "border-fern" : "border-line")
        }
      />
      <textarea
        value={instructions}
        onChange={(e) => onInstructions(e.target.value)}
        placeholder="Tell Addison how to approach this…"
        rows={4}
        disabled={saving}
        aria-label="Skill instructions"
        className={
          "resize-y rounded border bg-surface px-3 py-2 text-control leading-relaxed text-ink placeholder:text-faint disabled:opacity-60 " +
          (instructions ? "border-fern" : "border-line")
        }
      />
      <div className="flex gap-2">
        <button
          type="button"
          onClick={onSave}
          disabled={!canSave}
          className="rounded-sm bg-fern px-4 py-2 text-meta font-semibold text-on-accent hover:bg-fern-deep disabled:cursor-not-allowed disabled:opacity-50 max-md:min-h-[44px] max-md:px-5"
        >
          {saving ? "Saving…" : "Save"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={saving}
          className="rounded-sm border border-line bg-surface px-4 py-2 text-meta font-medium text-ink-soft hover:border-muted disabled:opacity-50 max-md:min-h-[44px]"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

// A calm on/off switch: rounded pill track (fern when on, hair when off) with a
// sliding knob. role="switch" + aria-checked for assistive tech; the knob's slide
// is the only transform motion and the global reduced-motion rule zeroes it.
function Toggle({
  on,
  onChange,
  label,
}: {
  on: boolean;
  onChange: (next: boolean) => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      onClick={() => onChange(!on)}
      className={
        "relative inline-flex h-[22px] w-[38px] shrink-0 items-center rounded-pill px-[3px] " +
        (on ? "bg-fern" : "bg-hair")
      }
    >
      <span
        aria-hidden="true"
        className={
          "inline-block h-4 w-4 rounded-pill bg-surface shadow-soft transition-transform duration-150 ease-out " +
          (on ? "translate-x-[16px]" : "translate-x-0")
        }
      />
    </button>
  );
}
