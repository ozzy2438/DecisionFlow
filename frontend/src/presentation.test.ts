import { describe, expect, it } from "vitest";

import { pipelineStates, riskTone, shortHash } from "./presentation";
import type { DecisionRecord } from "./types";

describe("decision presentation", () => {
  it("uses the governed risk bands", () => {
    expect(riskTone(0.49)).toBe("low");
    expect(riskTone(0.5)).toBe("elevated");
    expect(riskTone(0.8)).toBe("high");
  });

  it("shows an approval wait without claiming action or verification", () => {
    const decision = {
      status: "PENDING_APPROVAL",
      decision: { approval_required: true },
      verifications: [],
      ledger_verification: { valid: true },
    } as unknown as DecisionRecord;
    const states = Object.fromEntries(pipelineStates(decision).map((item) => [item.label, item.state]));
    expect(states.Approve).toBe("waiting");
    expect(states.Act).toBe("idle");
    expect(states.Verify).toBe("idle");
    expect(shortHash("1234567890abcdef")).toBe("12345678…abcdef");
  });
});
