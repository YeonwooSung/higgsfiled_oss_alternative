import type { Integrity, Preset, Project, ProductLockConfig } from "@oc/contracts";
import { ApiClient } from "@oc/web-core";

export { ApiError, sha256Hex } from "@oc/web-core";

/** Generic calls come from `@oc/web-core`; this adds the Product Lock endpoints. */
export class ProductLockClient extends ApiClient<ProductLockConfig, Project> {
  constructor(fetcher?: ConstructorParameters<typeof ApiClient>[1]) {
    super("product-lock", fetcher);
  }
  presets() {
    return this.request<{ presets: Preset[] }>("GET", "/product-lock/presets");
  }
  integrity(jobId: string) {
    return this.request<Integrity>("GET", `/jobs/${jobId}/integrity`);
  }
}

export const api = new ProductLockClient();
