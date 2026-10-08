import * as chai from "chai";
import getLeftAndRightOfSequenceInRangeGivenPosition from "./getLeftAndRightOfSequenceInRangeGivenPosition";

chai.should();
describe("getLeftAndRightOfSequenceInRangeGivenPosition", () => {
  it("gets the left and right of the range correctly given a position inside the range", () => {
    const sequence = "aaaaaaaaaattttttttttgggggggggg";
    const result = getLeftAndRightOfSequenceInRangeGivenPosition(
      { start: 9, end: 20 },
      10,
      sequence
    );
    result.leftHandSide.should.equal("a");
    result.rightHandSide.should.equal("ttttttttttg");
  });

  it("gets the left and right of the range correctly given a position outside the range", () => {
    const sequence = "aaaaaaaaaattttttttttgggggggggg";
    const result = getLeftAndRightOfSequenceInRangeGivenPosition(
      { start: 9, end: 20 },
      6,
      sequence
    );
    result.leftHandSide.should.equal("");
    result.rightHandSide.should.equal("attttttttttg");
  });

  it("gets the left and right of the range correctly given a position outside the range", () => {
    const sequence = "aaaaaaaaaattttttttttgggggggggg";
    const result = getLeftAndRightOfSequenceInRangeGivenPosition(
      { start: 9, end: 20 },
      24,
      sequence
    );
    result.leftHandSide.should.equal("attttttttttg");
    result.rightHandSide.should.equal("");
  });
});
