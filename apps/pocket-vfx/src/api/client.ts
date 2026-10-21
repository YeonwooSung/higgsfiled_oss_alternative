import type { PocketVfxCapabilities, PocketVfxConfig, PocketVfxProject, VfxPresetsResponse, VfxValidateResponse } from "@oc/contracts";
import { ApiClient } from "@oc/web-core";

export { ApiError, sha256Hex } from "@oc/web-core";

/** Generic calls come from `@oc/web-core`; this adds the Pocket VFX endpoints and capability typing. */
export class PocketVfxClient extends ApiClient<PocketVfxConfig, PocketVfxProject> {
  constructor(fetcher?: ConstructorParameters<typeof ApiClient>[1]) {
    super("pocket-vfx", fetcher);
  }
  vfxCapabilities() {
    return this.request<PocketVfxCapabilities>("GET", "/capabilities?app_slug=pocket-vfx");
  }
  presets() {
    return this.request<VfxPresetsResponse>("GET", "/pocket-vfx/presets");
  }
  /** Config of a saved revision (e.g. the one a track job ran on). */
  revision(projectId: string, revision: number) {
    return this.request<{ revision: number; config: PocketVfxConfig; created_at: string }>("GET", `/projects/${projectId}/revisions/${revision}`);
  }
  /** Pre-render checks of a saved revision (`POST /projects/{id}/vfx/validate`). */
  validate(projectId: string, revision: number) {
    return this.request<VfxValidateResponse>("POST", `/projects/${projectId}/vfx/validate`, { revision });
  }
}

export const api = new PocketVfxClient();
