import assert from "node:assert/strict";
import getReverseComplementAnnotation from "./getReverseComplementAnnotation";
describe("getReverseComplementAnnotation", () => {
  it("reverse complements an annotation ", () => {
    //0123456789
    //---abc----   //normal
    //----cba---   //reverse complemented
    const newAnn = getReverseComplementAnnotation(
      {
        start: 3,
        end: 5
      },
      10
    );
    assert.deepEqual(newAnn, {
      start: 4,
      end: 6,
      forward: true,
      strand: 1
    });
  });
  it("reverse complements an annotation crossing origin", () => {
    //0123456789
    //cde-----ab   //normal
    //ab-----edc   //reverse complemented
    const newAnn = getReverseComplementAnnotation(
      {
        start: 8,
        end: 2,
        strand: 1
      },
      10
    );
    assert.deepEqual(newAnn, {
      start: 7,
      end: 1,
      forward: true,
      strand: -1
    });
  });
});
