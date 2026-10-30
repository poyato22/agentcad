import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import type { ProfilePatch, ViewerBackground } from "../../../api/types";
import { applyBackground, BACKGROUNDS, choiceOf, previewBackground, THEME_BACKGROUND } from "../../viewer/background";
import { actions } from "../../../state/store";
import { setTheme, THEMES, type Theme } from "../../../styles/theme";
import { Button } from "../../common/Button";
import { Card, ErrorLine, SectionBox, useTask, type SectionProps } from "../common";

const THEME_LABEL: Record<Theme, string> = { system: "System", light: "Light", dark: "Dark" };

export function ProfileSection({ data, update }: SectionProps): JSX.Element {
  const p = data.profile;
  const [name, setName] = useState(p.display_name);
  const [material, setMaterial] = useState(p.default_material);
  const [theme, setThemeState] = useState<Theme>(p.theme);
  const savedBg = p.viewer_background ?? THEME_BACKGROUND;
  const [bg, setBg] = useState<ViewerBackground>(savedBg);
  // the background last saved: an unsaved preview is put back when the Profile closes
  const savedRef = useRef(savedBg);
  useEffect(() => () => { applyBackground(savedRef.current); }, []);
  const task = useTask();

  const patch: ProfilePatch = {};
  if (name !== p.display_name) patch.display_name = name;
  if (material !== p.default_material) patch.default_material = material;
  if (theme !== p.theme) patch.theme = theme;
  if (JSON.stringify(bg) !== JSON.stringify(savedBg)) patch.viewer_background = bg;
  const bgChoice = choiceOf(bg);

  // Like the theme, the background previews live as it is picked; Save persists it.
  function pickBackground(id: string): void {
    const c = BACKGROUNDS.find((x) => x.id === id);
    if (!c) return;
    setBg(c.bg);
    previewBackground(c.bg);
    task.clear();
  }
  const dirty = Object.keys(patch).length > 0;

  // The theme previews live as it is picked; Save persists it (PUT /profile).
  function pickTheme(t: Theme): void {
    setThemeState(t);
    setTheme(t);
    task.clear();
  }

  async function save(): Promise<void> {
    await task.run(async () => {
      const next = await actions.client().updateProfile(patch);
      update({ profile: next });
      setName(next.display_name);
      setMaterial(next.default_material);
      setThemeState(next.theme);
      setTheme(next.theme);
      setBg(next.viewer_background ?? THEME_BACKGROUND);
      savedRef.current = next.viewer_background ?? THEME_BACKGROUND;
      applyBackground(savedRef.current);
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
        <div class="profile-field"><span class="label">Units</span><span>{p.units}</span></div>
        <label class="profile-field"><span class="label">Theme</span>
          <select aria-label="Theme" value={theme} onChange={(e) => pickTheme(e.currentTarget.value as Theme)}>
            {THEMES.map((t) => <option key={t} value={t}>{THEME_LABEL[t]}</option>)}
          </select>
        </label>
        <label class="profile-field"><span class="label">Viewer background</span>
          <select aria-label="Viewer background" value={bgChoice?.id ?? "custom"} onChange={(e) => pickBackground(e.currentTarget.value)}>
            {BACKGROUNDS.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            {!bgChoice && <option value="custom">Custom ({bg.colors.join(" → ")})</option>}
          </select>
        </label>
        <div class="profile-row">
          <Button variant="ink" disabled={!dirty || task.busy} onClick={() => { void save(); }}>Save</Button>
          {task.note && <span class="dim">{task.note}</span>}
        </div>
        <ErrorLine error={task.error} />
      </Card>
    </SectionBox>
  );
}
