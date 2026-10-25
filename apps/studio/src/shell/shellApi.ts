import { ApiClient } from "@oc/web-core";

/** Client for the tool-agnostic endpoints the shell calls (projects list, asset URLs); the slug is never sent there. */
export const shellApi = new ApiClient("camera-sketch");
