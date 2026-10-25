/**
 * Three.js objects for the preview mesh (no React; plain three). Owns its geometries/materials and
 * frees them in `dispose()`; the texture belongs to the caller.
 *
 * Two layers, approximating `render.rasterize` on the GPU with the stencil buffer
 * (the renderer must be created with `stencil: true`; three clears stencil every frame):
 *  1. Regular faces (renderOrder 0): normal z-buffered draw; every drawn pixel writes stencil = 1.
 *  2. Edge faces (renderOrder 1): drawn only where stencil bit 0 is clear, i.e. where NO regular
 *     face covers the pixel; z-buffered among themselves (depth there is still the clear value,
 *     because a regular fragment that writes depth also writes stencil). Writes stencil = 2.
 *     Per face, `stretch = A / A_ref` is computed on the CPU each frame (`edgeFaceStretch`) and
 *     uploaded as a per-vertex `valid` attribute (the edge geometry is non-indexed). An invalid face
 *     (flipped or stretch > 1.5) still shows its texture like Python's `rgb`; with holes on it is
 *     covered by the red hatch.
 *  3. Hole quad (renderOrder 2, only with holes on): full-screen hatch where stencil == 0
 *     (covered by nothing).
 * So a hole = a pixel covered by nothing, or won by an invalid edge face — the server rule.
 */
import {
  AlwaysStencilFunc,
  BufferAttribute,
  BufferGeometry,
  DoubleSide,
  EqualStencilFunc,
  Group,
  KeepStencilOp,
  Mesh,
  PlaneGeometry,
  ReplaceStencilOp,
  ShaderMaterial,
  type Texture,
} from "three";
import type { Mat3, Mat4 } from "../model/camera";
import { edgeFaceRefAreas, edgeFaceStretch, isStretchValid, type PreviewMesh } from "../model/mesh";
import { toThreePositions } from "../model/three";

const HATCH = /* glsl */ `
uniform float uPx;
uniform float uShowHoles;
vec3 hatch(vec3 base) {
  float s = mod(gl_FragCoord.x + gl_FragCoord.y, 10.0 * uPx);
  float stripe = step(s, 3.0 * uPx);
  return mix(base * 0.45, vec3(0.898, 0.282, 0.302), stripe);
}
`;

const VERT = /* glsl */ `
attribute float valid;
varying vec2 vUv;
varying float vValid;
void main() {
  vUv = uv;
  vValid = valid;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

const FRAG = /* glsl */ `
uniform sampler2D map;
varying vec2 vUv;
varying float vValid;
${HATCH}
void main() {
  vec3 c = texture2D(map, vUv).rgb;
  if (uShowHoles > 0.5 && vValid < 0.5) c = hatch(c);
  gl_FragColor = vec4(c, 1.0);
}
`;

const HOLE_VERT = /* glsl */ `void main() { gl_Position = vec4(position.xy, 0.0, 1.0); }`;
const HOLE_FRAG = /* glsl */ `
${HATCH}
void main() { gl_FragColor = vec4(hatch(vec3(0.039, 0.047, 0.063)), 1.0); }
`;

function material(map: Texture, extra: Partial<ShaderMaterial>): ShaderMaterial {
  const m = new ShaderMaterial({
    uniforms: { map: { value: map }, uPx: { value: 1 }, uShowHoles: { value: 0 } },
    vertexShader: VERT,
    fragmentShader: FRAG,
    side: DoubleSide, // the server rasterizer does not cull
  });
  return Object.assign(m, { stencilWrite: true, stencilFail: KeepStencilOp, stencilZFail: KeepStencilOp, stencilZPass: ReplaceStencilOp }, extra);
}

export class DepthMesh {
  readonly group = new Group();
  readonly regularFaces: number;
  readonly edgeFaces: number;
  private readonly regular: Mesh<BufferGeometry, ShaderMaterial>;
  private readonly edge: Mesh<BufferGeometry, ShaderMaterial>;
  private readonly holes: Mesh<PlaneGeometry, ShaderMaterial>;
  private readonly validAttr: BufferAttribute;
  private readonly cvPositions: Float32Array;
  private readonly edgeIndex: Uint32Array;
  private readonly stretch: Float32Array;
  private refAreas: Float64Array | null = null;
  private refKey = "";

  constructor(mesh: PreviewMesh, map: Texture) {
    this.cvPositions = mesh.positions;
    this.edgeIndex = mesh.edgeIndex;
    this.regularFaces = mesh.index.length / 3;
    this.edgeFaces = mesh.edgeIndex.length / 3;
    this.stretch = new Float32Array(this.edgeFaces);
    const pos = toThreePositions(mesh.positions);

    const g = new BufferGeometry();
    g.setAttribute("position", new BufferAttribute(pos, 3));
    g.setAttribute("uv", new BufferAttribute(mesh.uvs, 2));
    g.setAttribute("valid", new BufferAttribute(new Float32Array(pos.length / 3).fill(1), 1));
    g.setIndex(new BufferAttribute(mesh.index, 1));
    this.regular = new Mesh(g, material(map, { stencilFunc: AlwaysStencilFunc, stencilRef: 1 }));

    // Non-indexed so every edge face carries its own `valid` flag.
    const n = this.edgeFaces * 3;
    const ep = new Float32Array(n * 3), eu = new Float32Array(n * 2);
    for (let k = 0; k < n; k++) {
      const vi = mesh.edgeIndex[k];
      ep.set(pos.subarray(vi * 3, vi * 3 + 3), k * 3);
      eu.set(mesh.uvs.subarray(vi * 2, vi * 2 + 2), k * 2);
    }
    const eg = new BufferGeometry();
    eg.setAttribute("position", new BufferAttribute(ep, 3));
    eg.setAttribute("uv", new BufferAttribute(eu, 2));
    this.validAttr = new BufferAttribute(new Float32Array(n).fill(1), 1);
    eg.setAttribute("valid", this.validAttr);
    // ref & funcMask: pass where (stencil & 1) == (2 & 1) == 0, then write 2.
    this.edge = new Mesh(eg, material(map, { stencilFunc: EqualStencilFunc, stencilRef: 2, stencilFuncMask: 1 }));
    this.edge.renderOrder = 1;

    const hm = new ShaderMaterial({
      uniforms: { uPx: { value: 1 }, uShowHoles: { value: 1 } },
      vertexShader: HOLE_VERT,
      fragmentShader: HOLE_FRAG,
      depthTest: false,
      depthWrite: false,
    });
    Object.assign(hm, { stencilWrite: true, stencilWriteMask: 0, stencilFunc: EqualStencilFunc, stencilRef: 0 });
    this.holes = new Mesh(new PlaneGeometry(2, 2), hm);
    this.holes.renderOrder = 2;
    this.holes.frustumCulled = false;
    this.regular.frustumCulled = false; // bounding sphere of the depth mesh is not worth computing per edit
    this.edge.frustumCulled = false;
    this.group.add(this.regular, this.edge, this.holes);
  }

  /** Per-frame CPU work: edge-face stretch validity + uniforms. Returns the invalid edge-face count. */
  update(Tcw: Mat4, Kout: Mat3, showHoles: boolean, pixelRatio: number): number {
    const key = Kout.flat().join(",");
    if (!this.refAreas || key !== this.refKey) {
      this.refAreas = edgeFaceRefAreas(this.cvPositions, this.edgeIndex, Kout);
      this.refKey = key;
    }
    edgeFaceStretch(this.cvPositions, this.edgeIndex, Tcw, Kout, this.refAreas, this.stretch);
    const valid = this.validAttr.array as Float32Array;
    let invalid = 0;
    for (let f = 0; f < this.edgeFaces; f++) {
      const ok = isStretchValid(this.stretch[f]) ? 1 : 0;
      invalid += 1 - ok;
      valid[f * 3] = valid[f * 3 + 1] = valid[f * 3 + 2] = ok;
    }
    this.validAttr.needsUpdate = true;
    for (const m of [this.regular.material, this.edge.material, this.holes.material]) {
      m.uniforms.uPx.value = pixelRatio;
      m.uniforms.uShowHoles.value = showHoles ? 1 : 0;
    }
    this.holes.visible = showHoles;
    return invalid;
  }

  dispose(): void {
    this.group.removeFromParent();
    for (const m of [this.regular, this.edge, this.holes]) {
      m.geometry.dispose();
      m.material.dispose();
    }
  }
}
