/* eslint-disable no-unused-expressions*/
import genbankToJson, { parseFeatureLocation } from "../src/genbankToJson";

import path from "path";
import fs from "fs";
import { chai } from "vitest";
import jsonToGenbank from "../src/jsonToGenbank";

chai.should();

describe("genbankToJson tests", function () {
  it(`correctly handles features with a direction of BOTH and NONE`, () => {
    const string = `LOCUS       kc2         108 bp    DNA     linear    01-NOV-2016
COMMENT             teselagen_unique_id: 581929a7bc6d3e00ac7394e8
FEATURES             Location/Qualifiers
      CDS             1..108
                      /label="GFPuv"
                      /direction="BOTH"
      misc_feature    61..108
                      /label="gly_ser_linker"
                      /direction="NONE"
ORIGIN
        1 atgaaggtct acggcaagga acagtttttg cggatgcgcc agagcatgtt ccccgatcgc
        61 ggtggcagtg gtagcgggag ctcgggtggc tcaggctctg ggg
//

`;
    const result = genbankToJson(string);
    result[0].parsedSequence.features.should.containSubset([
      {
        name: "GFPuv",
        strand: 1,
        arrowheadType: "BOTH"
      },
      {
        name: "gly_ser_linker",
        strand: 1,
        arrowheadType: "NONE"
      }
    ]);
    const gb = jsonToGenbank(result[0].parsedSequence);
    //we should retain the direction information on a round trip
    const result2 = genbankToJson(gb);
    result2[0].parsedSequence.features.should.containSubset([
      {
        name: "GFPuv",
        strand: 1,
        arrowheadType: "BOTH"
      },
      {
        name: "gly_ser_linker",
        strand: 1,
        arrowheadType: "NONE"
      }
    ]);
  });

  it(`correctly handles the single-stranded/double-stranded RNA/DNA in LOCUS line`, () => {
    const ss_DNA_string = `LOCUS       Tt2-PstI-SphI-rev(dna)        20 bp    ss-DNA     circular
    04-FEB-2021
DEFINITION  [Heavy] lalalal
            more description here
            and still more
ACCESSION   Tt2-PstI-SphI-rev
VERSION     Tt2-PstI-SphI-rev.0
KEYWORDS    .
SOURCE      Homo sapiens
ORGANISM  Homo sapiens
    .
COMMENT     Chain:Heavy
    Numbering:Kabat
    AnnotationCategory:VREGION
    Plasmid: pAETEST
    ClonedAnnotationCategory:VREGION
ORIGIN
1 tcgcgcgttt cggtgatgac
//`;

    const ds_DNA_string = ss_DNA_string.replace("ss-DNA", "DNA");

    const ss_RNA_string = `LOCUS       Tt2-PstI-SphI-rev(rna)        20 bp    ss-RNA     circular
    04-FEB-2021
DEFINITION  [Heavy] lalalal
            more description here
            and still more
ACCESSION   Tt2-PstI-SphI-rev
VERSION     Tt2-PstI-SphI-rev.0
KEYWORDS    .
SOURCE      Homo sapiens
ORGANISM  Homo sapiens
    .
COMMENT     Chain:Heavy
    Numbering:Kabat
    AnnotationCategory:VREGION
    Plasmid: pAETEST
    ClonedAnnotationCategory:VREGION
ORIGIN
1 ucgcgcguuu cggugaugac
//`;

    const ds_RNA_string = ss_RNA_string.replace("ss-RNA", "RNA");

    const ss_DNA_result = genbankToJson(ss_DNA_string);
    ss_DNA_result[0].parsedSequence.isSingleStrandedDNA.should.equal(true);

    const ds_DNA_result = genbankToJson(ds_DNA_string);
    Boolean(ds_DNA_result[0].parsedSequence.isSingleStrandedDNA).should.equal(
      false
    );

    const ss_RNA_result = genbankToJson(ss_RNA_string);
    Boolean(ss_RNA_result[0].parsedSequence.isDoubleStrandedRNA).should.equal(
      false
    );

    const ds_RNA_result = genbankToJson(ds_RNA_string);
    ds_RNA_result[0].parsedSequence.isDoubleStrandedRNA.should.equal(true);
  });

  it(`correctly handles a multi-line DEFINITION converting it to description`, () => {
    const string = `LOCUS       Tt2-PstI-SphI-rev(dna)        7628 bp    DNA     circular
    04-FEB-2021
DEFINITION  [Heavy] lalalal
            more description here
            and still more
ACCESSION   Tt2-PstI-SphI-rev(dna)
VERSION     Tt2-PstI-SphI-rev(dna).0
KEYWORDS    .
SOURCE      Homo sapiens
ORGANISM  Homo sapiens
    .
COMMENT     Chain:Heavy
    Numbering:Kabat
    AnnotationCategory:VREGION
    Plasmid: pAETEST
    ClonedAnnotationCategory:VREGION
FEATURES             Location/Qualifiers
source          1..76
             /chain_orf="1"
             /chain_strand="+"
             /inference="Antibody-Extractor"
             /numbering="Kabat"
             /plasmid="pAETEST"
             /lab_host="Escherichia coli"
             /mol_type="other DNA"
             /organism="Homo sapiens"
             /db_xref="taxon:9606"
ORIGIN
1 tcgcgcgttt cggtgatgac ggtgaaaacc tctgacacat gcagctcccg gagacggtca
61 cagcttgtct gtaagcggat gccgggagca gacaagcccg tcagggcgcg tcagcgggtg
121 ttggcgggtg tcggggctgg cttaactatg cggcatcaga gcagattgta ctgagagtgc
//
`;
    const result = genbankToJson(string);
    result[0].parsedSequence.description.should.equal(
      `[Heavy] lalalal more description here and still more`
    );
  });

  it(`correctly handles a multi-line LOCUS and parses the sequence as circular`, () => {
    const string = `LOCUS       Tt2-PstI-SphI-rev(dna)        7628 bp    DNA     circular
    04-FEB-2021
DEFINITION  [Heavy]
ACCESSION   Tt2-PstI-SphI-rev(dna)
VERSION     Tt2-PstI-SphI-rev(dna).0
KEYWORDS    .
SOURCE      Homo sapiens
ORGANISM  Homo sapiens
    .
COMMENT     Chain:Heavy
    Numbering:Kabat
    AnnotationCategory:VREGION
    Plasmid: pAETEST
    ClonedAnnotationCategory:VREGION
FEATURES             Location/Qualifiers
source          1..76
             /chain_orf="1"
             /chain_strand="+"
             /inference="Antibody-Extractor"
             /numbering="Kabat"
             /plasmid="pAETEST"
             /lab_host="Escherichia coli"
             /mol_type="other DNA"
             /organism="Homo sapiens"
             /db_xref="taxon:9606"
ORIGIN
1 tcgcgcgttt cggtgatgac ggtgaaaacc tctgacacat gcagctcccg gagacggtca
61 cagcttgtct gtaagcggat gccgggagca gacaagcccg tcagggcgcg tcagcgggtg
121 ttggcgggtg tcggggctgg cttaactatg cggcatcaga gcagattgta ctgagagtgc
//
`;
    const result = genbankToJson(string);
    result[0].parsedSequence.name.should.equal("Tt2-PstI-SphI-rev(dna)");
    result[0].parsedSequence.circular.should.equal(true);
    result[0].parsedSequence.type.should.equal("DNA");
    // result[0].parsedSequence.isProtein.should.be.
  });
  it(`allows for overflow features if an allowOverflowAnnotations flag is passed`, () => {
    const string = `LOCUS       Tt2-PstI-SphI-rev(dna)        7628 bp    DNA     circular
    04-FEB-2021
DEFINITION  [Heavy]
ACCESSION   NT_123456
VERSION     Tt2-PstI-SphI-rev(dna).0
KEYWORDS    .
SOURCE      Homo sapiens
ORGANISM  Homo sapiens
    .
COMMENT     Chain:Heavy
    Numbering:Kabat
    AnnotationCategory:VREGION
    Plasmid: pAETEST
    ClonedAnnotationCategory:VREGION
FEATURES             Location/Qualifiers
  source          1..76
              /chain_orf="1"
              /chain_strand="+"
              /inference="Antibody-Extractor"
              /numbering="Kabat"
              /plasmid="pAETEST"
              /lab_host="Escherichia coli"
              /mol_type="other DNA"
              /organism="Homo sapiens"
              /db_xref="taxon:9606"
//
`;
    const result = genbankToJson(string, { allowOverflowAnnotations: true });

    result[0].parsedSequence.accession.should.equal("NT_123456");
    result[0].parsedSequence.name.should.equal("Tt2-PstI-SphI-rev(dna)");
    result[0].parsedSequence.circular.should.equal(true);
    result[0].parsedSequence.type.should.equal("DNA");
    result[0].parsedSequence.size.should.equal(0);
    result[0].parsedSequence.features[0].name.should.equal("Homo sapiens");
    result[0].parsedSequence.features[0].start.should.equal(0);
    result[0].parsedSequence.features[0].end.should.equal(75);

    // result[0].parsedSequence.isProtein.should.be.
  });
  it(`parses out the DIVISION property correctly https://www.ncbi.nlm.nih.gov/Sitemap/samplerecord.html#GenBankDivisionB`, () => {
    const string = `LOCUS       ProteinSeq          10 bp    DNA  linear  PLN  04-MAR-2019
ORIGIN
    1 gtagaggccg
//`;
    const result = genbankToJson(string);
    result[0].parsedSequence.name.should.equal("ProteinSeq");
    result[0].parsedSequence.gbDivision.should.equal("PLN");
    result[0].parsedSequence.sequenceTypeFromLocus.should.equal("DNA");
    result[0].parsedSequence.type.should.equal("DNA");
    // result[0].parsedSequence.isProtein.should.be.
    result[0].parsedSequence.sequence.should.equal("gtagaggccg");
    result[0].parsedSequence.size.should.equal(10);
    const gbString = jsonToGenbank(result[0].parsedSequence);
    assert(gbString.includes(" PLN "));
  });
  it(`does not parse a dna file with the name ProteinSeq into a protein `, () => {
    const string = `LOCUS       ProteinSeq          10 bp    DNA  linear    04-MAR-2019
ORIGIN
    1 gtagaggccg
//`;
    const result = genbankToJson(string);
    result[0].parsedSequence.name.should.equal("ProteinSeq");
    result[0].parsedSequence.type.should.equal("DNA");
    // result[0].parsedSequence.isProtein.should.be.
    result[0].parsedSequence.sequence.should.equal("gtagaggccg");
    result[0].parsedSequence.size.should.equal(10);
  });
  it(`parses a protein genbank file into a protein sequence json by default `, () => {
    const string = `LOCUS       Untitled_Sequence          10 aa  linear    04-MAR-2019
ORIGIN
    1 MTCAGRRAYL
//`;
    const result = genbankToJson(string);
    result[0].parsedSequence.name.should.equal("Untitled_Sequence");
    result[0].parsedSequence.type.should.equal("PROTEIN");
    result[0].parsedSequence.sequenceTypeFromLocus.should.equal("aa");
    result[0].parsedSequence.isProtein.should.equal(true);
    result[0].parsedSequence.proteinSequence.should.equal("MTCAGRRAYL");
    result[0].parsedSequence.proteinSize.should.equal(10);
  });

  it("handles joined features/parts correctly", function () {
    const string = fs.readFileSync(
      path.join(
        __dirname,
        "./testData/genbank/gbWithJoinedFeaturesAndParts.gb"
      ),
      "utf8"
    );
    const result = genbankToJson(string);
    result[0].parsedSequence.features.should.containSubset([
      {
        name: "reg_elem",
        start: 867,
        end: 1017,
        locations: [
          {
            start: 867,
            end: 961
          },
          {
            start: 975,
            end: 1017
          }
        ],
        strand: 1
      }
    ]);
  });

  it("parses multiline notes correctly", () => {
    const string = fs.readFileSync(
      path.join(__dirname, "./testData/genbank/BlueScribe.gb"),
      "utf8"
    );

    const result = genbankToJson(string, { primersAsFeatures: true });

    result.should.be.an("array");
    result[0].success.should.be.true;
    result[0].parsedSequence.features.should.deep.include(
      {
        notes: {
          note: ["common sequencing primer, one of multiple similar variants"]
        },
        type: "primer_bind",
        strand: 1,
        name: "M13 fwd",
        start: 378,
        end: 394,
        forward: true
      }
    );

    result[0].parsedSequence.features.should.deep.include(
      {
        notes: {
          bound_moiety: ["lac repressor encoded by lacI"],
          note: [
            "The lac repressor binds to the lac operator to inhibit transcription in E. coli. This inhibition can be relieved by adding lactose or isopropyl-beta-D-thiogalactopyranoside (IPTG)."
          ]
        },
        type: "protein_bind",
        strand: 1,
        name: "lac operator",
        start: 548,
        end: 564,
        forward: true
      }
    );
  });

  it("parseFeatureLocation returns expected outputs", () => {
    const testCases = [
      { input: "1..2", output: [{ start: 0, end: 1 }] },
      { input: "complement(1..2)", output: [{ start: 0, end: 1 }] },
      {
        input: "join(1..2,3..4)",
        output: [
          { start: 0, end: 1 },
          { start: 2, end: 3 }
        ]
      },
      {
        input: "complement(join(1..2,3..4))",
        output: [
          { start: 0, end: 1 },
          { start: 2, end: 3 }
        ]
      },
      // Single position location in join
      {
        input: "complement(join(1,3..4))",
        output: [
          { start: 0, end: 0 },
          { start: 2, end: 3 }
        ]
      },
      {
        input: "complement(join(1..2,3))",
        output: [
          { start: 0, end: 1 },
          { start: 2, end: 2 }
        ]
      },
      // Origin-spanning features
      {
        input: "complement(join(3..4,1..2))",
        output: [{ start: 2, end: 1 }]
      },
      {
        input: "join(3..4,1..2)",
        output: [{ start: 2, end: 1 }]
      }
    ];
    testCases.forEach(({ input, output }) => {
      const result = parseFeatureLocation(input, 0, 0, 0, 1, 4);
      expect(result).toEqual(output);
    });
  });
  it("correctly parses origin-spanning features in genbank files", () => {
    const str = `LOCUS       .                         20 bp    DNA     circular UNK 01-JAN-1980
        DEFINITION  .
        ACCESSION   <unknown id>
        VERSION     <unknown id>
        KEYWORDS    .
        SOURCE      .
          ORGANISM  .
                    .
        FEATURES             Location/Qualifiers
             misc_feature    join(19..20,1)
        ORIGIN
                1 aaaaaaaaaa aaaaaaaaaa
        //

`;
    const result = genbankToJson(str);

    expect(result[0].parsedSequence.features[0].locations).toEqual(
      undefined,
      "origin-spanning locations should have been merged"
    );
    expect(result[0].parsedSequence.features[0].start).toEqual(
      18,
      "start should be 18"
    );
    expect(result[0].parsedSequence.features[0].end).toEqual(
      0,
      "end should be 0"
    );
  });

});
