import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Build } from "../../../api/builds";
import type { CoreClient } from "../../../api/client";
import { activeBuildId, buildActions, builds, resetBuilds } from "../../../state/builds";
import { bindClient, resetStore } from "../../../state/store";
import { chosenBuild } from "./BuildPicker";

const b = (id: string, name: string): Build => ({ id, name, created_at: 1, updated_at: 1, trashed_at: null, part_count: 0, run_count: 0, onshape: null });

beforeEach(() => { resetStore(); resetBuilds(); chosenBuild.value = null; });

describe("Into build picker", () => {
  it("a build switch clears the earlier pick, so the picker follows the active build (M-3)", async () => {
    bindClient({ setActiveBuild: vi.fn(async (t: unknown) => t), parts: vi.fn(async () => []) } as unknown as CoreClient);
    builds.value = [b("q", "Quad"), b("d", "Default")];
    activeBuildId.value = "d";
    chosenBuild.value = "q";                  // picked Quad on the Onshape page
    await buildActions.switchTo("q");
    await buildActions.switchTo("d");
    expect(chosenBuild.value).toBeNull();
  });
});
