import { describe, expect, it } from "vitest";
import {
  hashString, pixelKey,
  boneLength, buildTree, circleNodes, effectiveBrushRadius, fitView, focusRect, imageToScreen, moveHandle,
  moveNode, pathD, screenToImage, zoomAt,
} from "./geometry";

describe("canvas coordinate conversion", () => {
  it("round-trips image <-> screen", () => {
    const v = { scale: 0.37, x: 120.5, y: -40 };
    const p = { x: 541.2, y: 466.6 };
    const s = imageToScreen(v, p);
    const back = screenToImage(v, s);
    expect(back.x).toBeCloseTo(p.x, 9);
    expect(back.y).toBeCloseTo(p.y, 9);
  });
  it("zoom keeps the image point under the cursor fixed", () => {
    const v = { scale: 0.5, x: 10, y: 20 };
    const cursor = { x: 300, y: 200 };
    const before = screenToImage(v, cursor);
    const z = zoomAt(v, cursor, 1.7);
    const after = screenToImage(z, cursor);
    expect(z.scale).toBeCloseTo(0.85, 9);
    expect(after.x).toBeCloseTo(before.x, 9);
    expect(after.y).toBeCloseTo(before.y, 9);
  });
  it("clamps zoom", () => {
    expect(zoomAt({ scale: 30, x: 0, y: 0 }, { x: 0, y: 0 }, 10).scale).toBe(32);
    expect(zoomAt({ scale: 0.06, x: 0, y: 0 }, { x: 0, y: 0 }, 0.1).scale).toBe(0.05);
  });
  it("fits the T-pose canvas inside a viewport, centred", () => {
    const v = fitView(1086, 1448, 1000, 800);
    expect(1448 * v.scale).toBeLessThanOrEqual(800);
    expect(v.x + (1086 * v.scale) / 2).toBeCloseTo(500, 6);
    expect(v.y + (1448 * v.scale) / 2).toBeCloseTo(400, 6);
  });
  it("focuses a joint rectangle", () => {
    const v = focusRect([380, 440, 440, 500], 800, 600);
    const c = imageToScreen(v, { x: 410, y: 470 });
    expect(c.x).toBeCloseTo(400, 6);
    expect(c.y).toBeCloseTo(300, 6);
  });
});

describe("mask brush", () => {
  it("uses the backend's 50%-contour rule", () => {
    expect(effectiveBrushRadius(20, 1)).toBe(20);
    expect(effectiveBrushRadius(20, 0)).toBe(10);
    expect(effectiveBrushRadius(20, 0.5)).toBe(15);
  });
});

describe("vector corrections", () => {
  it("builds the same path data as the backend", () => {
    const nodes = [{ x: 10, y: 20, hin: [0, 0] as [number, number], hout: [5, 0] as [number, number] },
                   { x: 30, y: 40, hin: [0, -5] as [number, number], hout: [0, 0] as [number, number] }];
    expect(pathD(nodes, false)).toBe("M 10.00 20.00 C 15.00 20.00 30.00 35.00 30.00 40.00");
    expect(pathD(nodes, true).endsWith("Z")).toBe(true);
  });
  it("moves anchors and mirrored handles", () => {
    const n = circleNodes(100, 100, 10);
    const m = moveNode(n, 0, { x: 120, y: 100 });
    expect(m[0].x).toBe(120);
    expect(n[0].x).toBe(110); // immutable
    const h = moveHandle(m, 0, "hout", { x: 120, y: 112 });
    expect(h[0].hout).toEqual([0, 12]);
    expect(h[0].hin).toEqual([-0, -12]);
  });
  it("measures bones", () => {
    expect(boneLength([0, 0], [3, 4])).toBe(5);
  });
});

describe("layer hierarchy", () => {
  it("nests groups and keeps layer order", () => {
    const groups = { CHARACTER: { name: "CHARACTER", parent: null }, Head: { name: "Head", parent: "CHARACTER" },
                     Hair: { name: "Hair", parent: "Head" } };
    const t = buildTree(groups, [{ id: "head_base", group: "Head" }, { id: "hair", group: "Hair" },
                                 { id: "torso", group: "CHARACTER" }]);
    expect(t.children[0].name).toBe("Head");
    expect(t.children[0].children[0].layers).toEqual(["hair"]);
    expect(t.layers).toEqual(["torso"]);
  });
});

describe("layer pixel cache key", () => {
  it("changes only with mask edits or rejections", () => {
    const ops = [{ type: "mask.edit", layer: "torso" }];
    const a = pixelKey(ops, { "x": { status: "pending_review" } });
    expect(pixelKey([...ops], { "x": { status: "approved" } })).toBe(a);
    expect(pixelKey([...ops], { "x": { status: "rejected" } })).not.toBe(a);
    expect(pixelKey([...ops, { type: "mask.reset", layer: "torso" }], {})).not.toBe(a);
    expect(hashString("abc")).toBe(hashString("abc"));
  });
});

