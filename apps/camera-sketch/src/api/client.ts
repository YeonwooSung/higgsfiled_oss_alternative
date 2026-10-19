import type { CameraCapabilities, CameraPreset, CameraSketchConfig, CameraSketchProject } from "@oc/contracts";
import { ApiClient } from "@oc/web-core";

export { ApiError, sha256Hex } from "@oc/web-core";

/** Generic calls come from `@oc/web-core`; this adds the Camera Sketch endpoints and capability typing. */
export class CameraSketchClient extends ApiClient<CameraSketchConfig, CameraSketchProject> {
  constructor(fetcher?: ConstructorParameters<typeof ApiClient>[1]) {
    super("camera-sketch", fetcher);
  }
  cameraCapabilities() {
    return this.request<CameraCapabilities>("GET", "/capabilities?app_slug=camera-sketch");
  }
  presets() {
    return this.request<{ presets: CameraPreset[] }>("GET", "/camera-sketch/presets");
  }
}

export const api = new CameraSketchClient();
