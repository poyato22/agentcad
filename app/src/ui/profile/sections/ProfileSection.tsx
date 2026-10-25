import type { JSX } from "preact";
import { useState } from "preact/hooks";
import type { ProfilePatch } from "../../../api/types";
import { actions } from "../../../state/store";
import { Button } from "../../common/Button";
import { Card, ErrorLine, SectionBox, useTask, type SectionProps } from "../common";

export function ProfileSection({ data, update }: SectionProps): JSX.Element {
  const p = data.profile;
  const [name, setName] = useState(p.display_name);
  const [material, setMaterial] = useState(p.default_material);
  const task = useTask();

  const patch: ProfilePatch = {};
  if (name !== p.display_name) patch.display_name = name;
  if (material !== p.default_material) patch.default_material = material;
  const dirty = Object.keys(patch).length > 0;

  async function save(): Promise<void> {
    await task.run(async () => {
      const next = await actions.client().updateProfile(patch);
      update({ profile: next });
      setName(next.display_name);
      setMaterial(next.default_material);
    }, "saved");
  }

  return (
    <SectionBox title="Profile">
      <Card title="You">
        <label class="profile-field"><span class="label">Name</span>
          <input aria-label="Name" value={name} onInput={(e) => { setName(e.currentTarget.value); task.clear(); }} />
        </label>
        <label class="profile-field"><span class="label">Default material</span>
          <input aria-label="Default material" value={material} onInput={(e) => { setMaterial(e.currentTarget.value); task.clear(); }} />
        </label>
        <div class="profile-field"><span class="label">Units</span><span>mm</span></div>
        <div class="profile-row">
          <Button variant="ink" disabled={!dirty || task.busy} onClick={() => { void save(); }}>Save</Button>
          {task.note && <span class="dim">{task.note}</span>}
        </div>
        <ErrorLine error={task.error} />
      </Card>
    </SectionBox>
  );
}
